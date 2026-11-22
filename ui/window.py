"""Main window: sidebar navigation + lazily-built pages (glass redesign)."""
from __future__ import annotations

import base64
import threading
from typing import Callable

from PyQt6.QtCore import QByteArray, QEvent, QEventLoop, Qt, QTimer
from PyQt6.QtWidgets import QAbstractButton, QApplication, QLabel
from PyQt6.QtGui import QAction, QActionGroup, QGuiApplication, QIcon, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLayout, QMainWindow, QProgressBar, QStackedWidget, QWidget,
)

from . import icons, motion
from .ctx import Ctx
from .i18n import i18n, tr
from .pages.base import Page
from .pages.dashboard import DashboardPage
from .pages.download import DownloadPage
from .pages.history import HistoryPage
from .pages.queue import QueuePage
from .pages.settings import SettingsPage
from .dialogs import AUTHOR, VERSION, AboutDialog, ShortcutsDialog, confirm
from .titlebar import TitleBar
from .menus import RoundMenu
from .pages.upload import UploadPage
from core import procs
from .overlay import DimOverlay
from .dropfx import WindowDropOverlay
from .sidebar import FrozenView, Sidebar
from .sounds import sounds
from .theme import THEME_LABELS, theme
from .tray import TrayIcon
from .hub import HUB
from .widgets import (
    Button, ElidedLabel, NavButton, Pill, ThemeToggle, Toast, hbox, label, repolish, vbox,
)

NAV = [("dashboard", "nav.dashboard", "grid", "nav.workspace"),
       ("upload", "nav.upload", "cloud-upload", "nav.transfer"),
       ("queue", "nav.queue", "queue", ""),
       ("download", "nav.download", "cloud-download", ""),
       ("history", "nav.history", "history", "")]

THEME_ORDER = ["dark", "black", "navy", "cream", "light"]


class AppWindow(QMainWindow):
    watchdog_enabled = False          # main.py turns this on: a quit may never hang > 15 s

    def __init__(self, ctx: Ctx) -> None:
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle("Transfer Now")
        self.setWindowIcon(icons.app_icon())
        self.setMinimumSize(1000, 660)
        self.resize(1240, 800)
        self.setAcceptDrops(True)
        # window-less: no OS frame; we draw the caption bar and rounded corners ourselves
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        root = QFrame()
        root.setObjectName("winframe")
        root.setProperty("max", False)
        self.root = root
        self.setCentralWidget(root)
        outer = vbox(root)
        self.titlebar = TitleBar(self)
        outer.addWidget(self.titlebar)
        body = QWidget()
        body.setObjectName("page")
        lay = QHBoxLayout(body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        outer.addWidget(body, 1)
        self.status_bar = QFrame()
        self.status_bar.setObjectName("statusbar")
        self.status_bar.setFixedHeight(0)       # the mockup has no status bar: its widgets stay alive, hidden
        self.status_bar.hide()
        outer.addWidget(self.status_bar)

        self.sidebar = self._build_sidebar()
        lay.addWidget(self.sidebar)
        # Windows 11 Mica: the page lives on a rounded "sheet" inset from the right/bottom edges
        sheet_wrap = QWidget()
        sheet_wrap.setObjectName("page")
        swl = QHBoxLayout(sheet_wrap)
        swl.setContentsMargins(0, 0, 10, 10)
        swl.setSpacing(0)
        self.sheet = QFrame()
        self.sheet.setObjectName("sheet")
        self._freeze = None
        sl = QHBoxLayout(self.sheet)
        sl.setContentsMargins(1, 1, 1, 1)
        sl.setSpacing(0)
        self.stack = QStackedWidget()
        self.stack.setObjectName("page")
        sl.addWidget(self.stack)
        swl.addWidget(self.sheet)
        lay.addWidget(sheet_wrap, 1)
        self.drop_overlay = WindowDropOverlay(sheet_wrap)   # "drop anywhere" comet overlay
        QApplication.instance().installEventFilter(self)

        self.pages: dict[str, Page] = {}
        self._factories: dict[str, Callable[[Ctx], Page]] = {
            "dashboard": DashboardPage, "upload": UploadPage, "queue": QueuePage,
            "download": DownloadPage, "history": HistoryPage, "settings": SettingsPage,
        }
        self._commands: list = []            # (title, group, shortcut, QAction) for the palette
        self._menu_icon_pairs: list = []     # (QAction, icon name) for theme-aware menu icons
        self.dim = DimOverlay(root)
        self._dim_depth = 0
        self.toast = Toast(root, self.ctx.settings)
        self.toast.bottom_inset = 10 + 16        # sheet inset + gap
        self.toast.announced.connect(self._status_message)
        self._activity_fx = motion.FadeToggle(self.activity)
        self.tray = TrayIcon(self)               # no-op when the OS has no tray
        self._last_clip = ""                     # clipboard watcher dedupe
        self._clip_timer = QTimer(self)
        self._clip_timer.setInterval(1200)
        self._clip_timer.timeout.connect(self._watch_clipboard)
        self._sync_clip_watch()
        self._booted = False
        self._act_active = False
        self._last_pct = -1
        self._msg_timer = QTimer(self)
        self._msg_timer.setSingleShot(True)
        self._msg_timer.timeout.connect(self._status_idle)
        self._flash = QTimer(self)
        self._flash.setSingleShot(True)
        self._flash.timeout.connect(self._status_reset)
        ctx.navigate = self.navigate
        ctx.toast = self.toast.show_message
        ctx.activity.connect(self._on_activity)
        ctx.settings.on_change(self._setting_changed)

        self.sidebar.moving.connect(self._sidebar_moving)
        self._build_menus()
        theme.changed.connect(self._refresh_menu_icons)
        self.titlebar.palette_btn.clicked.connect(self._open_palette)
        self._build_statusbar()
        self._shortcuts()
        self._restore_geometry()
        self.navigate("dashboard")
        self._booted = True
        i18n.changed.connect(self._retranslate)

    # ---------------------------------------------------------------- sidebar
    def _build_sidebar(self) -> QFrame:
        sb = Sidebar()
        lay = vbox(sb, (14, 4, 14, 12), 2)
        lay.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)   # width is animated, never pushed

        brand = hbox(spacing=11)
        brand.setContentsMargins(8, 0, 0, 0)
        self.logo = label("")
        self.logo.setFixedSize(34, 34)
        brand.addWidget(self.logo)
        brand_text = QWidget()
        col = vbox(brand_text, spacing=0)
        name = label("Transfer Now")
        name.setStyleSheet("font-size: 14px; font-weight: 600;")
        col.addWidget(name)
        self.tagline = label(tr("app.tagline"), "caption")
        col.addWidget(self.tagline)
        brand.addWidget(brand_text, 1)
        lay.addLayout(brand)
        sb.brand_layout = brand
        sb.register_fade(brand_text)
        lay.addSpacing(22)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.nav: dict[str, NavButton] = {}
        for key, text_key, icon, section_key in NAV:
            if section_key:
                if key != "dashboard":
                    lay.addSpacing(12)
                head = label(tr(section_key), "overline")
                head.setObjectName("section_" + section_key)
                head.setContentsMargins(12, 4, 0, 4)
                lay.addWidget(head)
                sb.register_fold(head)
            b = NavButton(tr(text_key), icon)
            b._text_key = text_key
            b.clicked.connect(lambda _=False, k=key: self.navigate(k))
            self.group.addButton(b)
            self.nav[key] = b
            lay.addWidget(b)
            sb.register_nav(b)
        lay.addStretch(1)

        # quiet mini-progress block (visible only while a transfer runs)
        self.activity = QFrame()
        self.activity.setObjectName("minibar")
        al = vbox(self.activity, (12, 9, 12, 10), 6)
        arow = hbox(spacing=8)
        self.act_label = ElidedLabel("")
        self.act_label.setObjectName("actlabel")
        self.act_pct = label("", "")
        self.act_pct.setObjectName("actpct")
        arow.addWidget(self.act_label, 1)
        arow.addWidget(self.act_pct)
        al.addLayout(arow)
        self.act_bar = QProgressBar()
        self.act_bar.setObjectName("actbar")
        self.act_bar.setRange(0, 100)
        self.act_bar.setTextVisible(False)
        al.addWidget(self.act_bar)
        self.activity.hide()
        lay.addWidget(self.activity)
        lay.addSpacing(12)

        sett = NavButton(tr("nav.settings"), "gear")
        sett._text_key = "nav.settings"
        sett.clicked.connect(lambda: self.navigate("settings"))
        self.group.addButton(sett)
        self.nav["settings"] = sett
        lay.addWidget(sett)
        sb.register_nav(sett)

        # collapse / expand control (same row style as the nav, but not a page)
        self.collapse_btn = NavButton(tr("sb.collapse"), "panel-left")
        self.collapse_btn.setCheckable(False)
        self.collapse_btn.clicked.connect(self._toggle_sidebar)
        lay.addWidget(self.collapse_btn)
        sb.register_nav(self.collapse_btn)

        foot_w = QWidget()
        foot = hbox(foot_w, (12, 8, 2, 0), 8)
        self.ver_label = label(f"Version {VERSION}", "caption")
        foot.addWidget(self.ver_label)
        foot.addStretch(1)
        self.theme_btn = ThemeToggle()
        self.theme_btn.picked.connect(self._pick_theme)
        foot.addWidget(self.theme_btn)
        lay.addWidget(foot_w)
        sb.register_fold(foot_w)
        sb.changed.connect(self._sidebar_changed)
        sb.set_collapsed(bool(self.ctx.settings.get("sidebar_collapsed", False)), animate=False)
        self._sidebar_changed(sb.is_collapsed(), save=False)
        self._snap = None
        theme.about_to_change.connect(self._theme_snapshot)
        theme.changed.connect(self._on_theme)
        self._on_theme()
        HUB.jobs_changed.connect(self._sync_queue_badge)
        HUB.jobs_updated.connect(self._sync_queue_badge)
        return sb

    def _sync_queue_badge(self) -> None:
        n = sum(1 for j in HUB.jobs if getattr(j, "state", "") in ("queued", "scheduled", "running"))
        self.nav["queue"].set_badge(n)

    # ---- theme switch: snapshot → restyle underneath → short cross-fade (hides any lag)
    def _theme_snapshot(self) -> None:
        if not self.isVisible() or getattr(self, "_snap", None) is not None:
            return
        lbl = QLabel(self.root)
        lbl.setPixmap(self.root.grab())
        lbl.setGeometry(self.root.rect())
        lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lbl.show()
        lbl.raise_()
        self._snap = lbl

    def _theme_fade(self) -> None:
        lbl, self._snap = getattr(self, "_snap", None), None
        if lbl is not None:
            motion.fade_out(lbl, 200, lbl.deleteLater)

    def _on_theme(self) -> None:
        if getattr(self, "_booted", False):
            QTimer.singleShot(0, lambda: sounds.play("theme"))
            QTimer.singleShot(0, self._theme_fade)
        self.logo.setPixmap(icons.logo_pixmap(34))
        self.theme_btn.set_dark(theme.dark)

    def _pick_theme(self, mode: str) -> None:
        self.ctx.settings.set("theme", mode)

    def _toggle_theme(self) -> None:
        cur = self.ctx.settings.get("theme", "dark")
        if cur == "system":
            cur = "dark"
        nxt = THEME_ORDER[(THEME_ORDER.index(cur) + 1) % len(THEME_ORDER)] if cur in THEME_ORDER else "dark"
        self.ctx.settings.set("theme", nxt)

    # -------------------------------------------------------------- navigation
    def page(self, key: str) -> Page:
        if key not in self.pages:
            pg = self._factories[key](self.ctx)
            self.pages[key] = pg
            self.stack.addWidget(pg)
        return self.pages[key]

    def navigate(self, key: str) -> None:
        if self._booted and key != self.current_key():
            sounds.play("nav")
        pg = self.page(key)
        prev = self.stack.currentWidget()
        self.stack.setCurrentWidget(pg)
        self.nav[key].setChecked(True)
        pg.on_show()
        if self._booted and prev is not None and prev is not pg:
            motion.page_in(pg)          # fade + 10 px rise, Shade curve

    def current_key(self) -> str:
        w = self.stack.currentWidget()
        return next((k for k, p in self.pages.items() if p is w), "")

    def _retranslate(self) -> None:
        """Language switched: update every static label we own here."""
        self.tagline.setText(tr("app.tagline"))
        for head in self.sidebar.findChildren(type(self.tagline)):
            pass
        for key, text_key, _icon, section_key in NAV:
            self.nav[key].set_text(tr(text_key))
            if section_key:
                head = self.sidebar.findChild(type(self.tagline), "section_" + section_key)
                if head is not None:
                    head.setText(tr(section_key))
        self.nav["settings"].set_text(tr("nav.settings"))
        self._sidebar_changed(self.sidebar.is_collapsed(), save=False)
        self.status_left.setText(tr("misc.ready"))
        self.tray.retranslate()
        for pg in self.pages.values():
            if hasattr(pg, "retranslate"):
                pg.retranslate()

    def _on_activity(self, text: str, pct: int) -> None:
        if not text:                                   # a transfer just ended (or none running)
            was_active, self._act_active = self._act_active, False
            self._activity_fx.hide()
            if was_active and self._last_pct >= 90:    # finished: flash a green, full bar
                self._msg_timer.stop()
                self.status_prog.setValue(100)
                self.status_prog.setProperty("tone", "success")
                repolish(self.status_prog)
                self.status_pct.setText("100%")
                self.status_left.setText(tr("misc.completed"))
                self._set_active_look(False)
                self._flash.start(2200)
                self.tray.notify("Transfer Now", tr("tray.done"))
            elif was_active and not self._flash.isActive():
                self._status_reset()
            self._last_pct = -1
            return
        if not self._act_active and sounds.idle_for(0.5):
            sounds.play("start")
        self._act_active = True
        self._flash.stop()
        self._msg_timer.stop()
        self._last_pct = pct
        if self.status_prog.property("tone"):
            self.status_prog.setProperty("tone", "")
            repolish(self.status_prog)
        shown = f"{text}…" + (f" {pct}%" if pct >= 0 else "")
        self.status_left.setText(shown)
        self.status_prog.setValue(max(0, pct))
        self.status_pct.setText(f"{pct}%" if pct >= 0 else "…")
        self._set_active_look(True)
        self.act_label.set_full_text(text)
        self.act_pct.setText(f"{pct}%" if pct >= 0 else "")
        self.act_bar.setValue(max(0, pct))
        self._activity_fx.show()

    # ----------------------------------------------------- dialog backdrop
    def dim_in(self) -> None:
        """A dialog opened: blur + darken the window behind it (nesting-safe)."""
        self._dim_depth += 1
        if self._dim_depth == 1:
            self.dim.present(rounded=not (self.isMaximized() or self.isFullScreen()))

    def dim_out(self) -> None:
        self._dim_depth = max(0, self._dim_depth - 1)
        if self._dim_depth == 0:
            self.dim.dismiss()

    # -------------------------------------------------------- clipboard watch
    _CLIP_LINK = "https://www.transfernow.net/dl/"

    def _sync_clip_watch(self) -> None:
        on = bool(self.ctx.settings.get("clip_watch", False))
        if on and not self._clip_timer.isActive():
            self._last_clip = QGuiApplication.clipboard().text()
            self._clip_timer.start()
        elif not on and self._clip_timer.isActive():
            self._clip_timer.stop()

    def _watch_clipboard(self) -> None:
        """A TransferNow link was copied elsewhere: offer to open it in Download."""
        text = QGuiApplication.clipboard().text().strip()
        if not text or text == self._last_clip:
            return
        self._last_clip = text
        link = text if text.startswith(self._CLIP_LINK) and "\n" not in text else ""
        if not link or self._dim_depth or self._act_active:
            return
        if any(e.get("link") == link for e in self.ctx.history.entries()):
            return          # our own link, copied from this app — no toast needed

        def open_download() -> None:
            self.navigate("download")
            page = self.pages.get("download")
            if page is not None:
                page.prefill(link)

        self.toast.show_message(tr("toast.clip_link"), "info",
                                action_text=tr("toast.clip_open"), on_action=open_download)

    # ------------------------------------------------------------- status bar
    def _set_active_look(self, active: bool) -> None:
        for w in (self.status_left, self.status_dot):
            w.setProperty("active", active)
            repolish(w)

    def _status_reset(self) -> None:
        """Back to the idle look: empty bar, 'Ready'."""
        self.status_prog.setValue(0)
        self.status_prog.setProperty("tone", "")
        repolish(self.status_prog)
        self.status_pct.setText("")
        self.status_left.setText(tr("misc.ready"))
        self._set_active_look(False)

    def _status_idle(self) -> None:
        if not self._act_active and not self._flash.isActive():
            self.status_left.setText(tr("misc.ready"))

    def _status_message(self, text: str, kind: str) -> None:
        """Mirror every toast in the status bar (unless a transfer is using it)."""
        if self._act_active or self._flash.isActive():
            return
        self.status_left.setText(text)
        self._msg_timer.start(4000)

    # ------------------------------------------------------------------ menus
    _MENU_ICONS = {"New Upload": "cloud-upload", "Add Files\u2026": "plus",
                   "Download from Link": "cloud-download", "Check Saved Links": "shield-check",
                   "Open Download Folder": "folder", "Export History as CSV\u2026": "download-arrow",
                   "Quit": "x", "Copy Last Link": "link", "Paste Link && Fetch": "paste",
                   "Find in History": "search", "Clear History\u2026": "trash",
                   "Command Palette\u2026": "sparkles", "Keyboard Shortcuts": "keyboard",
                   "About Transfer Now": "info", "Cycle Theme": "sun", "Full Screen": "monitor",
                   "Theme": "sun"}

    def _menu_icon_for(self, title: str) -> str:
        title = title.replace("&", "").strip()
        if title in self._MENU_ICONS:
            return self._MENU_ICONS[title]
        return {k: v for k, v in ((tr(tk), ic) for tk, ic in
                (("nav.dashboard", "grid"), ("nav.upload", "cloud-upload"),
                 ("nav.queue", "queue"), ("nav.download", "cloud-download"),
                 ("nav.history", "history"), ("nav.settings", "gear")))}.get(title, "")

    def _refresh_menu_icons(self) -> None:
        for a, name in self._menu_icon_pairs:
            if name:
                a.setIcon(QIcon(icons.pixmap(name, theme.pal["text2"], 16, 1.6)))

    def _act(self, menu, text: str, slot, shortcut: str = "", checkable: bool = False,
             palette: bool = True) -> QAction:
        a = QAction(text, self)
        self.addAction(a)            # keeps shortcuts live even if the menu is closed
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
            a.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        a.setCheckable(checkable)
        if not checkable:
            name = self._menu_icon_for(text) or (self._menu_icon_for(menu.title())
                                                 if menu is not None else "")
            if name:
                a.setIcon(QIcon(icons.pixmap(name, theme.pal["text2"], 16, 1.6)))
                self._menu_icon_pairs.append((a, name))
        a.triggered.connect(lambda _=False: (sounds.play("select"), slot()))
        if menu is not None:
            menu.addAction(a)
        if palette:
            group = menu.title().replace("&", "") if menu is not None else "View"
            title = text.replace("&&", "\0").replace("&", "").replace("\0", "&")
            if group == "Theme":
                title, group = f"Theme: {title}", "View"
            self._commands.append((title, group, shortcut, a))
        return a

    def _build_menus(self) -> None:
        mb = self.titlebar.menubar

        m = RoundMenu("&File", self)
        mb.addMenu(m)
        self._act(m, "New Upload", lambda: self.navigate("upload"), "Ctrl+U")
        self._act(m, "Add Files…", self._add_files, "Ctrl+O")
        self._act(m, "Download from Link", lambda: self.navigate("download"), "Ctrl+D")
        self._act(m, "Check Saved Links", self._check_links, "Ctrl+L")
        m.addSeparator()
        self._act(m, "Open Download Folder", self._open_dl_folder)
        self._act(m, "Export History as CSV…", lambda: self.ctx.export_history(self))
        m.addSeparator()
        self._act(m, "Quit", lambda: self.close(force=True), "Ctrl+Q")

        m = RoundMenu("&Edit", self)
        mb.addMenu(m)
        self._act(m, "Copy Last Link", self._copy_last, "Ctrl+Shift+C")
        self._act(m, "Paste Link && Fetch", self._paste_fetch, "Ctrl+Shift+V")
        m.addSeparator()
        self._act(m, "Find in History", self._find, "Ctrl+F")
        self._act(m, "Clear History…", lambda: (self.navigate("history"),
                                                self.pages["history"].clear_all()))

        m = RoundMenu("&View", self)
        mb.addMenu(m)
        for i, (key, text_key) in enumerate((("dashboard", "nav.dashboard"),
                                             ("upload", "nav.upload"),
                                             ("queue", "nav.queue"),
                                             ("download", "nav.download"),
                                             ("history", "nav.history"),
                                             ("settings", "nav.settings")), 1):
            sc = {"history": "Ctrl+H", "settings": "Ctrl+,"}.get(key, f"Ctrl+{i}")
            self._act(m, tr(text_key), lambda k=key: self.navigate(k), sc)
        m.addSeparator()
        self._sidebar_act = self._act(m, tr("sb.expand") if self.sidebar.is_collapsed()
                                      else tr("sb.collapse"), self._toggle_sidebar, "Ctrl+B")
        tm = RoundMenu("Theme", self)
        tm_name = self._menu_icon_for("Theme")
        if tm_name:
            tm.setIcon(QIcon(icons.pixmap(tm_name, theme.pal["text2"], 16, 1.6)))
            self._menu_icon_pairs.append((tm, tm_name))
        m.addMenu(tm)
        grp = QActionGroup(self)
        self._theme_acts = {}
        for key in THEME_ORDER + ["system"]:
            a = self._act(tm, THEME_LABELS[key], lambda k=key: self.ctx.settings.set("theme", k),
                          "", True)
            grp.addAction(a)
            self._theme_acts[key] = a
        self._theme_acts.get(self.ctx.settings.get("theme", "dark"),
                             self._theme_acts["dark"]).setChecked(True)
        m.addSeparator()
        self._sound_act = self._act(m, "Sound Effects", self._toggle_sounds, "Ctrl+Shift+M", True)
        self._sound_act.setChecked(bool(self.ctx.settings.get("sounds", True)))
        self._act(m, "Full Screen", self._toggle_fullscreen, "F11")

        m = RoundMenu("&Help", self)
        mb.addMenu(m)
        self._act(m, "Command Palette…", self._open_palette, "Ctrl+K", palette=False)
        m.addSeparator()
        self._act(m, "Keyboard Shortcuts", lambda: ShortcutsDialog(self).exec(), "F1")
        self._act(m, tr("wiz.run_again"), self._run_wizard, "")
        self._act(None, "Cycle Theme", self._toggle_theme)
        m.addSeparator()
        self._act(m, "About Transfer Now", lambda: AboutDialog(self).exec())

    def _build_statusbar(self) -> None:
        row = hbox(self.status_bar, (14, 0, 14, 0), 10)
        self.status_dot = QFrame()
        self.status_dot.setObjectName("statusdot")
        self.status_dot.setProperty("active", False)
        row.addWidget(self.status_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        self.status_left = label(tr("misc.ready"))
        self.status_left.setObjectName("statuslabel")
        self.status_left.setProperty("active", False)
        row.addWidget(self.status_left, 1)
        self.status_prog = QProgressBar()
        self.status_prog.setObjectName("statusprog")
        self.status_prog.setRange(0, 100)
        self.status_prog.setValue(0)
        self.status_prog.setTextVisible(False)
        row.addWidget(self.status_prog, 0, Qt.AlignmentFlag.AlignVCenter)
        self.status_pct = label("")
        self.status_pct.setObjectName("statuspct")
        self.status_pct.setFixedWidth(40)
        self.status_pct.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.status_pct)
        row.addSpacing(10)
        row.addWidget(label(f"Transfer Now v{VERSION}  ·  {AUTHOR}", "caption"))

    def _open_palette(self) -> None:
        if self._dim_depth:                       # a dialog is already open
            return
        from .palette import CommandPalette
        d = CommandPalette(self, self._commands)
        d.exec()
        if d.chosen is not None:                  # run after the dialog + backdrop are gone
            QTimer.singleShot(0, d.chosen.trigger)

    def _run_wizard(self) -> None:
        """Re-run the first-launch setup wizard (Help menu / command palette)."""
        from .wizard import SetupWizard
        SetupWizard(self).exec()

    def _check_links(self) -> None:
        self.navigate("history")
        self.pages["history"].check_links()

    def _toggle_sounds(self) -> None:
        on = not bool(self.ctx.settings.get("sounds", True))
        self.ctx.settings.set("sounds", on)
        self.toast.show_message("Sound effects on" if on else "Sound effects off", "info")

    def _setting_changed(self, key: str) -> None:
        if key == "sounds" and hasattr(self, "_sound_act"):
            self._sound_act.setChecked(bool(self.ctx.settings.get("sounds", True)))
            sp = self.pages.get("settings")
            if sp is not None and hasattr(sp, "sound_sw"):
                sp.sound_sw.setChecked(bool(self.ctx.settings.get("sounds", True)))
        if key == "tray_icon":
            self.tray.refresh_visibility()
        if key == "toast_pos":
            self.toast.reposition()
        if key == "clip_watch":
            self._sync_clip_watch()
        if key == "theme" and hasattr(self, "_theme_acts"):
            a = self._theme_acts.get(self.ctx.settings.get("theme"))
            if a is not None:
                a.setChecked(True)
            sp = self.pages.get("settings")
            if sp is not None:
                for k, b in sp.theme_cards.items():
                    b.setChecked(k == self.ctx.settings.get("theme", "dark"))
        if key == "language":
            i18n.set_language(self.ctx.settings.get("language", "en"))

    def _add_files(self) -> None:
        self.navigate("upload")
        self.pages["upload"].browse()

    def _open_dl_folder(self) -> None:
        import os
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        folder = self.ctx.settings.download_folder
        os.makedirs(folder, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _copy_last(self) -> None:
        for e in self.ctx.history.entries():
            if e.get("kind") == "upload" and e.get("link"):
                QGuiApplication.clipboard().setText(e["link"])
                self.toast.show_message(tr("toast.link_copied"))
                return
        self.toast.show_message(tr("toast.no_links"), "info")

    def _paste_fetch(self) -> None:
        text = QGuiApplication.clipboard().text().strip()
        self.navigate("download")
        if text:
            self.pages["download"].prefill(text)
        else:
            self.toast.show_message(tr("toast.clip_empty"), "info")

    def _sidebar_moving(self, on: bool) -> None:
        """While the sidebar glides, show a still of the page instead of re-laying-out
        the live (heavy) page on every frame; swap back with a short cross-fade."""
        if on:
            if getattr(self, "_freeze", None) is None and self.isVisible():
                self._freeze = FrozenView(self.sheet, self.stack.grab())
                self._freeze.show()
                self._freeze.raise_()
                self.stack.hide()
            return
        fz, self._freeze = getattr(self, "_freeze", None), None
        self.stack.show()
        if fz is not None:
            motion.fade_out(fz, 180, fz.deleteLater)

    def _toggle_sidebar(self) -> None:
        """Collapse to the icon rail / expand to the full sidebar (animated)."""
        self.sidebar.toggle()

    def _sidebar_changed(self, collapsed: bool, save: bool = True) -> None:
        if save:
            self.ctx.settings.set("sidebar_collapsed", collapsed)
            sounds.play("nav")
        self.collapse_btn.set_text(tr("sb.expand") if collapsed else tr("sb.collapse"))
        self.collapse_btn.setToolTip(tr("sb.expand") if collapsed else "")
        for k, b in self.nav.items():
            b.setToolTip(b._text if collapsed else "")
        if hasattr(self, "_sidebar_act"):
            self._sidebar_act.setText(tr("sb.expand") if collapsed else tr("sb.collapse"))
        # the mini progress block has no rail form: tuck it away while collapsed
        self.activity.setMaximumHeight(0 if collapsed else 16777215)

    def _toggle_fullscreen(self) -> None:
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    # --------------------------------------------------------------- shortcuts
    def _shortcuts(self) -> None:
        s = QShortcut(QKeySequence("Ctrl+Shift+P"), self)
        s.setContext(Qt.ShortcutContext.WindowShortcut)
        s.activated.connect(self._open_palette)
        for seq in ("Ctrl+Return", "Ctrl+Enter"):
            s = QShortcut(QKeySequence(seq), self)
            s.setContext(Qt.ShortcutContext.WindowShortcut)
            s.activated.connect(self._primary)

    def _primary(self) -> None:
        pg = self.pages.get(self.current_key())
        if pg is not None and hasattr(pg, "trigger_primary"):
            pg.trigger_primary()

    def _find(self) -> None:
        self.navigate("history")
        self.pages["history"].search.setFocus()
        self.pages["history"].search.selectAll()

    def _sheet_pos(self, e):
        return self.sheet.mapFrom(self, e.position().toPoint())

    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self.drop_overlay.begin(e.mimeData().urls(), self._sheet_pos(e))

    def dragMoveEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self.drop_overlay.move(self._sheet_pos(e))

    def dragLeaveEvent(self, e) -> None:  # noqa: N802
        self.drop_overlay.end()

    def dropEvent(self, e) -> None:  # noqa: N802
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            e.acceptProposedAction()
            sounds.play("drop")
            self.drop_overlay.drop(self._sheet_pos(e))
            self.navigate("upload")
            self.pages["upload"].add_paths(paths)
        else:
            self.drop_overlay.end()

    # ----------------------------------------------------------------- window
    _GRIP = 6

    def close(self, force: bool = False) -> bool:  # noqa: A003
        """force=True always quits; otherwise 'close to tray' can divert to hide."""
        self._force_close = force
        return super().close()

    def _edges_at(self, p):
        if self.isMaximized() or self.isFullScreen():
            return Qt.Edge(0)
        g, e = self._GRIP, Qt.Edge(0)
        if p.x() <= g:
            e |= Qt.Edge.LeftEdge
        elif p.x() >= self.width() - g:
            e |= Qt.Edge.RightEdge
        if p.y() <= g:
            e |= Qt.Edge.TopEdge
        elif p.y() >= self.height() - g:
            e |= Qt.Edge.BottomEdge
        return e

    _SILENT_BUTTONS = {"cap", "capclose", "nav", "toastclose", "palbtn"}   # these play their own sound

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        t = ev.type()
        if t == QEvent.Type.MouseButtonRelease and isinstance(obj, QAbstractButton) \
                and obj.isEnabled() and ev.button() == Qt.MouseButton.LeftButton \
                and obj.rect().contains(ev.position().toPoint()) \
                and obj.objectName() not in self._SILENT_BUTTONS:
            kind = type(obj).__name__
            sounds.play("toggle" if kind == "Switch"
                        else "select" if kind == "Swatch" or kind == "ThemeCard"
                        or obj.objectName() == "seg" or obj.objectName() == "flag"
                        else "click")
            return False
        if t in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress) and \
                isinstance(obj, QWidget) and obj.window() is self:
            p = self.mapFromGlobal(ev.globalPosition().toPoint())
            edges = self._edges_at(p)
            if t == QEvent.Type.MouseMove and not ev.buttons():
                shape = {Qt.Edge.LeftEdge: Qt.CursorShape.SizeHorCursor,
                         Qt.Edge.RightEdge: Qt.CursorShape.SizeHorCursor,
                         Qt.Edge.TopEdge: Qt.CursorShape.SizeVerCursor,
                         Qt.Edge.BottomEdge: Qt.CursorShape.SizeVerCursor,
                         Qt.Edge.LeftEdge | Qt.Edge.TopEdge: Qt.CursorShape.SizeFDiagCursor,
                         Qt.Edge.RightEdge | Qt.Edge.BottomEdge: Qt.CursorShape.SizeFDiagCursor,
                         Qt.Edge.RightEdge | Qt.Edge.TopEdge: Qt.CursorShape.SizeBDiagCursor,
                         Qt.Edge.LeftEdge | Qt.Edge.BottomEdge: Qt.CursorShape.SizeBDiagCursor,
                         }.get(edges)
                self.setCursor(shape) if shape else self.unsetCursor()
            elif t == QEvent.Type.MouseButtonPress and edges and \
                    ev.button() == Qt.MouseButton.LeftButton and self.windowHandle():
                self.windowHandle().startSystemResize(edges)
                return True
        return False

    def changeEvent(self, e) -> None:  # noqa: N802
        super().changeEvent(e)
        if e.type() == QEvent.Type.WindowStateChange:
            full = self.isMaximized() or self.isFullScreen()
            for w in (self.root, self.titlebar, self.status_bar):
                w.setProperty("max", full)
                w.style().unpolish(w)
                w.style().polish(w)
            self.titlebar.sync()

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        if self.dim.isVisible():
            self.dim.setGeometry(self.root.rect())
        if self.toast.isVisible():
            self.toast.place()

    def _restore_geometry(self) -> None:
        raw = self.ctx.settings.get("window_geometry", "")
        if raw:
            try:
                self.restoreGeometry(QByteArray(base64.b64decode(raw)))
            except Exception:  # noqa: BLE001
                pass

    def closeEvent(self, e) -> None:  # noqa: N802
        if not getattr(self, "_force_close", False) and self.tray.active \
                and bool(self.ctx.settings.get("minimize_to_tray", False)):
            e.ignore()
            self.hide()
            self.toast.show_message(tr("toast.tray_hidden"), "info")
            return
        busy = [p for p in self.pages.values() if not p.can_close()]
        if busy and not confirm(self, "Transfer in progress", "A transfer is still running.",
                                "Quit anyway? The transfer will be cancelled.",
                                yes="Quit", danger=True, cancel="Keep running"):
            self._force_close = False        # user stayed: a later X must behave normally
            e.ignore()
            return
        try:
            if not (self.isMaximized() or self.isFullScreen()):
                self.ctx.settings.set("window_geometry",
                                      base64.b64encode(bytes(self.saveGeometry())).decode())
        except Exception:  # noqa: BLE001
            pass
        if sounds.active:                         # let the short "close" cue finish
            sounds.play("close")
            loop = QEventLoop(self)
            QTimer.singleShot(170, loop.quit)
            loop.exec()
        QApplication.instance().removeEventFilter(self)
        self.hide()                               # gone from the screen/taskbar at once
        # Safety net: whatever happens during cleanup, the process ends within 15 s.
        if self.watchdog_enabled:
            wd = threading.Timer(15.0, procs.hard_exit)
            wd.daemon = True
            wd.start()
        if self.tray.tray is not None:
            self.tray.tray.hide()                 # remove the tray icon (no ghost icon)
        self._clip_timer.stop()
        procs.kill_all()                          # browsers first so worker threads end fast
        for p in self.pages.values():
            try:
                p.shutdown()
            except Exception:  # noqa: BLE001 — one stuck page must not block quitting
                pass
        procs.kill_all()
        e.accept()
