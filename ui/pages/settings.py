"""Settings — macOS System Settings style.

A nav rail on the left picks a focused sub-page; every sub-page scrolls on
its own: Appearance, Notifications, Transfers, Downloads, Desktop, Data and
About.  Changes are saved automatically.
"""
from __future__ import annotations

import os

from PyQt6.QtCore import QRectF, QUrl, Qt
from PyQt6.QtGui import QColor, QDesktopServices, QGuiApplication, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QFileDialog, QFrame, QGridLayout, QLabel, QPushButton, QScrollArea,
    QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from ..i18n import LANGS, i18n, tr
from ..theme import ACCENTS, DEFAULT_ACCENT, THEME_LABELS, THEMES, theme
from ..menus import LineEdit
from ..sounds import sounds
from ..widgets import (
    Button, ElidedLabel, FlagButton, NavButton, Segmented, Switch, Swatch,
    hbox, hline, label, vbox,
)
from .base import Page

from ..about import DeveloperHero, FeatureGrid, tech_string  # noqa: E402
from ..dialogs import (  # noqa: E402
    AUTHOR, FEATURES, VERSION, AboutDialog, ShortcutsDialog, confirm,
)

SECTIONS = [   # (key, title i18n key, icon name)
    ("appearance", "set.appearance", "sun"),
    ("notifications", "set.notifications", "bell"),
    ("transfers", "set.transfers", "send"),
    ("downloads", "set.downloads", "cloud-download"),
    ("desktop", "set.desktop", "monitor"),
    ("data", "set.data", "hard-drive"),
    ("about", "set.about", "info"),
]


def _section(title: str):
    card = QFrame()
    card.setObjectName("card")
    lay = vbox(card, (22, 18, 22, 10), 0)
    lay.addWidget(label(title, "h2"))
    lay.addSpacing(6)
    return card, lay


class ThemeCard(QPushButton):
    """Theme preview tile: two-tone mini mock-up + label drawn below it."""

    def __init__(self, key: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.key = key
        self.setObjectName("theme")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setText(THEME_LABELS.get(key, key))
        self.setMinimumWidth(96)
        self.setFixedHeight(92)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, e) -> None:  # noqa: N802
        pal = THEMES.get(self.key, THEMES["dark"])
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        checked = self.isChecked()
        c = theme.pal
        # tile background + border
        bg = QColor(c["accent_soft"]) if checked else QColor(pal["surface2"])
        if not checked:
            bg.setAlpha(235)
        border = QColor(pal["accent"] if "accent" in pal else c["accent"]) if checked \
            else QColor(c["border"])
        path = QPainterPath()
        path.addRoundedRect(QRectF(1, 1, w - 2, h - 2), 12, 12)
        p.fillPath(path, bg)
        pen = QPen(border, 2 if checked else 1)
        p.setPen(pen)
        p.drawPath(path)
        # mini window preview (top-centre)
        pw, ph = 56, 36
        px, py = int((w - pw) / 2), 12
        win = QPainterPath()
        win.addRoundedRect(px, py, pw, ph, 5, 5)
        p.fillPath(win, QColor(pal["bg"]))
        p.setPen(QColor(pal["border_strong"]))
        p.drawPath(win)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(pal["sidebar"]))
        p.drawRoundedRect(px + 3, py + 3, 12, ph - 6, 3, 3)
        p.setBrush(QColor(pal["surface_solid"] if "surface_solid" in pal else pal["surface2"]))
        p.drawRoundedRect(px + 19, py + 7, 24, 8, 2, 2)
        p.setBrush(QColor(pal["surface2"]))
        p.drawRoundedRect(px + 19, py + 19, 30, 10, 2, 2)
        accent = pal["accent"] if "accent" in pal else ACCENTS[DEFAULT_ACCENT][1]
        p.setBrush(QColor(accent))
        p.drawRoundedRect(px + 19, py + ph - 9, 12, 4, 2, 2)
        # label below the preview — readable on whichever bg it sits on
        if checked:
            tile_text = QColor(c["text"])          # accent_soft bg of the current theme
        else:
            tile_text = QColor(pal.get("text", c["text"]))   # tile's own theme
        p.setPen(tile_text)
        f = self.font()
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(0, py + ph + 4, w, h - py - ph - 6),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                   THEME_LABELS.get(self.key, self.key))


class SettingsPage(Page):
    key = "settings"
    overline_key = "nav.preferences"
    title_key = "set.title"
    subtitle_key = "set.subtitle"

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        s = ctx.settings

        # ---------------------------------------------------------- scaffold
        # nav rail + one scrollable pane per section (no page-level scrolling)
        row = QWidget()
        rl = hbox(row, (0, 0, 0, 0), 18)
        self.rail = QFrame()
        self.rail.setObjectName("card")
        self.rail.setProperty("flat", True)
        self.rail.setFixedWidth(216)
        rail_lay = vbox(self.rail, (8, 8, 8, 8), 4)
        self.nav_btns: dict[str, NavButton] = {}
        for key, tkey, ic in SECTIONS:
            b = NavButton(tr(tkey), ic)
            b.clicked.connect(lambda _=False, k=key: self._show_section(k))
            self.nav_btns[key] = b
            rail_lay.addWidget(b)
        rail_lay.addStretch(1)
        rl.addWidget(self.rail, 0, Qt.AlignmentFlag.AlignTop)

        self.stack = QStackedWidget()
        rl.addWidget(self.stack, 1)
        self.body.addSpacing(8)
        self.body.addWidget(row, 1)

        self.panes: dict[str, tuple[QScrollArea, QVBoxLayout]] = {}
        for key, tkey, _ic in SECTIONS:
            pane, body = self._make_pane()
            self.panes[key] = (pane, body)
            self.stack.addWidget(pane)

        # =================================================== APPEARANCE pane
        body = self.panes["appearance"][1]
        self.pane_titles: dict[str, QLabel] = {}
        self.pane_titles["appearance"] = label(tr("set.appearance"), "h2")
        body.addWidget(self.pane_titles["appearance"])
        body.addSpacing(4)

        card, lay = _section(tr("set.appearance"))
        self.theme_title = label(tr("set.theme"))
        self.theme_desc = label(tr("set.theme_desc"), "caption", wrap=True)
        grid_w = QWidget()
        grid = QGridLayout(grid_w)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(10)
        self.theme_cards: dict[str, ThemeCard] = {}
        order = ["dark", "black", "navy", "cream", "light", "system"]
        for i, key in enumerate(order):
            b = ThemeCard(key)
            b.setChecked(s.get("theme", "dark") == key)
            b.clicked.connect(lambda _=False, k=key: s.set("theme", k))
            self.theme_cards[key] = b
            grid.addWidget(b, i // 3, i % 3)
        lay.addWidget(self.theme_title)
        lay.addSpacing(2)
        lay.addWidget(self.theme_desc)
        lay.addSpacing(12)
        lay.addWidget(grid_w)
        lay.addSpacing(14)
        lay.addWidget(hline())

        sw = QWidget()
        swl = hbox(sw, spacing=4)
        self.swatches: dict[str, Swatch] = {}
        for key, (name, dk, lt) in ACCENTS.items():
            b = Swatch(dk if theme.dark else lt, name)
            b.setChecked(key == s.get("accent", "signal"))
            b.clicked.connect(lambda _=False, k=key: self._pick_accent(k))
            self.swatches[key] = b
            swl.addWidget(b)
        self.accent_title = label(tr("set.accent"))
        self.accent_desc = label(tr("set.accent_desc"), "caption", wrap=True)
        aw = QWidget()
        al = vbox(aw, (0, 0, 0, 0), 2)
        al.addWidget(self.accent_title)
        al.addWidget(self.accent_desc)
        row2 = QWidget()
        rl2 = hbox(row2, (0, 12, 0, 12), 16)
        rl2.addWidget(aw, 1)
        rl2.addWidget(sw, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(row2)
        body.addWidget(card)
        theme.changed.connect(self._recolor_swatches)

        card, lay = _section(tr("set.language"))
        self.lang_desc = label(tr("set.language_desc"), "caption", wrap=True)
        lay.addWidget(self.lang_desc)
        lay.addSpacing(4)
        gw = QWidget()
        grid = QGridLayout(gw)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(10)
        self.lang_btns: dict[str, FlagButton] = {}
        codes = list(LANGS)
        for i, code in enumerate(codes):
            b = FlagButton(code)
            b.setChecked(code == s.get("language", "en"))
            b.clicked.connect(lambda _=False, k=code: self._pick_lang(k))
            self.lang_btns[code] = b
            grid.addWidget(b, i // 4, i % 4)
            grid.setColumnStretch(i % 4, 1)
        lay.addWidget(gw)
        body.addWidget(card)
        body.addStretch(1)
        i18n.changed.connect(self._sync_lang_buttons)

        # ================================================= NOTIFICATIONS pane
        body = self.panes["notifications"][1]
        self.pane_titles["notifications"] = label(tr("set.notifications"), "h2")
        body.addWidget(self.pane_titles["notifications"])
        body.addSpacing(4)

        card, lay = _section(tr("set.notif_toasts"))
        self.pos_title = label(tr("set.notif_pos"))
        self.pos_desc = label(tr("set.notif_pos_desc"), "caption", wrap=True)
        self.pos_seg = Segmented(
            [("br", tr("set.pos_br")), ("bc", tr("set.pos_bc")),
             ("tr", tr("set.pos_tr")), ("tc", tr("set.pos_tc"))],
            s.get("toast_pos", "br"))
        self.pos_seg.changed.connect(lambda k: s.set("toast_pos", k))
        self._labeled_row(lay, tr("set.notif_pos"), tr("set.notif_pos_desc"),
                          self.pos_seg)
        lay.addWidget(hline())
        self.dur_title = label(tr("set.notif_dur"))
        self.dur_desc = label(tr("set.notif_dur_desc"), "caption", wrap=True)
        self.dur_seg = Segmented(
            [("short", tr("set.dur_short")), ("normal", tr("set.dur_normal")),
             ("long", tr("set.dur_long")), ("sticky", tr("set.dur_sticky"))],
            s.get("toast_dur", "normal"))
        self.dur_seg.changed.connect(lambda k: s.set("toast_dur", k))
        self._labeled_row(lay, tr("set.notif_dur"), tr("set.notif_dur_desc"),
                          self.dur_seg)
        lay.addWidget(hline())
        self.bar_sw = Switch(bool(s.get("toast_bar", True)))
        self.bar_sw.toggled.connect(lambda v: s.set("toast_bar", v))
        self.bar_title = label(tr("set.notif_bar"))
        self.bar_desc = label(tr("set.notif_bar_desc"), "caption", wrap=True)
        self._switch_row(lay, self.bar_sw, self.bar_title, self.bar_desc)
        lay.addWidget(hline())
        self.nsound_sw = Switch(bool(s.get("toast_sound", True)))
        self.nsound_sw.toggled.connect(lambda v: s.set("toast_sound", v))
        self.nsound_title = label(tr("set.notif_sound"))
        self.nsound_desc = label(tr("set.notif_sound_desc"), "caption", wrap=True)
        self._switch_row(lay, self.nsound_sw, self.nsound_title, self.nsound_desc,
                         divider=False)
        body.addWidget(card)

        card, lay = _section(tr("set.sound"))
        self.sound_title = label(tr("set.sound_effects"))
        self.sound_desc = label(tr("set.sound_desc"), "caption", wrap=True)
        self.sound_sw = Switch(bool(s.get("sounds", True)))
        self.sound_sw.toggled.connect(self._sound_toggled)
        self._switch_row(lay, self.sound_sw, self.sound_title, self.sound_desc)
        lay.addWidget(hline())
        self.vol_title = label(tr("set.volume"))
        self.vol_seg = Segmented([("low", tr("set.low")), ("medium", tr("set.medium")),
                                  ("high", tr("set.high"))],
                                 str(s.get("sound_volume", "medium")))
        self.vol_seg.changed.connect(self._volume_changed)
        self._labeled_row(lay, tr("set.volume"), "", self.vol_seg)
        body.addWidget(card)

        card, lay = _section(tr("set.test"))
        self.test_title = label(tr("set.test"))
        self.test_desc = label(tr("set.test_desc"), "caption", wrap=True)
        test = QWidget()
        tl = hbox(test, spacing=8)
        self.test_btns: list[Button] = []
        for text, kind, ic in ((tr("pill.active_ok"), "success", "check-circle"),
                               ("Info", "info", "info"),
                               ("Error", "error", "alert-circle")):
            b = Button(text, "", ic, "sm")
            b.clicked.connect(lambda _=False, k=kind: self._test_toast(k))
            tl.addWidget(b)
            self.test_btns.append(b)
        tw = QWidget()
        trl = hbox(tw, (0, 12, 0, 12), 16)
        tcol = vbox(spacing=2)
        tcol.addWidget(self.test_title)
        tcol.addWidget(self.test_desc)
        trl.addLayout(tcol, 1)
        trl.addWidget(test, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(tw)
        body.addWidget(card)
        body.addStretch(1)

        # ===================================================== TRANSFERS pane
        body = self.panes["transfers"][1]
        self.pane_titles["transfers"] = label(tr("set.transfers"), "h2")
        body.addWidget(self.pane_titles["transfers"])
        body.addSpacing(4)

        card, lay = _section(tr("set.transfers"))
        self.sender_title = label(tr("set.sender_email"))
        self.sender_desc = label(tr("set.sender_desc"), "caption", wrap=True)
        self.sender_seg = Segmented([("random", tr("up.random")), ("fixed", tr("up.my_email"))],
                                    s.get("sender_mode", "random"))
        self.sender_seg.changed.connect(self._sender_mode)
        sw_row = QWidget()
        srl = hbox(sw_row, (0, 12, 0, 12), 16)
        scol = vbox(spacing=2)
        scol.addWidget(self.sender_title)
        scol.addWidget(self.sender_desc)
        srl.addLayout(scol, 1)
        srl.addWidget(self.sender_seg, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(sw_row)
        lay.addWidget(hline())
        self.email_title = label(tr("set.my_email"))
        self.email_desc = label(tr("set.my_email_desc"), "caption", wrap=True)
        self.email = LineEdit(s.get("fixed_email", ""))
        self.email.setPlaceholderText("you@example.com")
        self.email.setMinimumWidth(240)
        self.email.editingFinished.connect(lambda: s.set("fixed_email", self.email.text().strip()))
        self._labeled_row(lay, tr("set.my_email"), tr("set.my_email_desc"), self.email)
        lay.addWidget(hline())
        self.par_title = label(tr("set.parallel"))
        self.par_desc = label(tr("set.parallel_desc"), "caption", wrap=True)
        self.par_seg = Segmented([("1", "1"), ("2", "2"), ("3", "3"), ("4", "4")],
                                 str(s.get("parallel_uploads", 2)))
        self.par_seg.changed.connect(lambda k: s.set("parallel_uploads", int(k)))
        self._labeled_row(lay, tr("set.parallel"), tr("set.parallel_desc"), self.par_seg)
        lay.addWidget(hline())
        self.copy_sw = Switch(bool(s.get("auto_copy_link", True)))
        self.copy_sw.toggled.connect(lambda v: s.set("auto_copy_link", v))
        self.copy_title = label(tr("set.auto_copy"))
        self.copy_desc = label(tr("set.auto_copy_desc"), "caption", wrap=True)
        self._switch_row(lay, self.copy_sw, self.copy_title, self.copy_desc)
        lay.addWidget(hline())
        self.pace_sw = Switch(bool(s.get("human_pace", True)))
        self.pace_sw.toggled.connect(lambda v: s.set("human_pace", v))
        self.pace_title = label(tr("set.pace"))
        self.pace_desc = label(tr("set.pace_desc"), "caption", wrap=True)
        self._switch_row(lay, self.pace_sw, self.pace_title, self.pace_desc)
        lay.addWidget(hline())
        self.retry_sw = Switch(bool(s.get("auto_retry", True)))
        self.retry_sw.toggled.connect(lambda v: s.set("auto_retry", v))
        self.retry_title = label(tr("set.auto_retry"))
        self.retry_desc = label(tr("set.auto_retry_desc"), "caption", wrap=True)
        self._switch_row(lay, self.retry_sw, self.retry_title, self.retry_desc,
                         divider=False)
        body.addWidget(card)
        body.addStretch(1)

        # ===================================================== DOWNLOADS pane
        body = self.panes["downloads"][1]
        self.pane_titles["downloads"] = label(tr("set.downloads"), "h2")
        body.addWidget(self.pane_titles["downloads"])
        body.addSpacing(4)

        card, lay = _section(tr("set.downloads"))
        ctrl = QWidget()
        cl = hbox(ctrl, spacing=8)
        self.folder = ElidedLabel(s.download_folder, "muted")
        self.folder.setMinimumWidth(220)
        cl.addWidget(self.folder, 1)
        self.folder_btn = Button(tr("dl.change"), "", "folder", "sm")
        self.folder_btn.clicked.connect(self._pick_folder)
        cl.addWidget(self.folder_btn)
        self.folder_title = label(tr("set.save_loc"))
        self.folder_desc = label(tr("set.save_loc_desc"), "caption", wrap=True)
        fw = QWidget()
        fwl = hbox(fw, (0, 12, 0, 12), 16)
        fcol = vbox(spacing=2)
        fcol.addWidget(self.folder_title)
        fcol.addWidget(self.folder_desc)
        fwl.addLayout(fcol, 1)
        fwl.addWidget(ctrl, 1, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(fw)
        lay.addWidget(hline())
        self.open_sw = Switch(bool(s.get("open_folder_after_download", True)))
        self.open_sw.toggled.connect(lambda v: s.set("open_folder_after_download", v))
        self.open_title = label(tr("set.open_after"))
        self._switch_row(lay, self.open_sw, self.open_title, None, divider=False)
        body.addWidget(card)
        body.addStretch(1)

        # ======================================================= DESKTOP pane
        body = self.panes["desktop"][1]
        self.pane_titles["desktop"] = label(tr("set.desktop"), "h2")
        body.addWidget(self.pane_titles["desktop"])
        body.addSpacing(4)

        card, lay = _section(tr("set.desktop"))
        self.tray_title = label(tr("set.tray_icon"))
        self.tray_desc = label(tr("set.tray_icon_desc"), "caption", wrap=True)
        self.tray_icon_sw = Switch(bool(s.get("tray_icon", True)))
        self.tray_icon_sw.toggled.connect(lambda v: s.set("tray_icon", v))
        self._switch_row(lay, self.tray_icon_sw, self.tray_title, self.tray_desc)
        lay.addWidget(hline())
        self.mintray_title = label(tr("set.min_tray"))
        self.mintray_desc = label(tr("set.min_tray_desc"), "caption", wrap=True)
        self.minimize_to_tray_sw = Switch(bool(s.get("minimize_to_tray", False)))
        self.minimize_to_tray_sw.toggled.connect(lambda v: s.set("minimize_to_tray", v))
        self._switch_row(lay, self.minimize_to_tray_sw, self.mintray_title,
                         self.mintray_desc)
        lay.addWidget(hline())
        self.notify_title = label(tr("set.notify"))
        self.notify_desc = label(tr("set.notify_desc"), "caption", wrap=True)
        self.tray_notifications_sw = Switch(bool(s.get("tray_notifications", True)))
        self.tray_notifications_sw.toggled.connect(lambda v: s.set("tray_notifications", v))
        self._switch_row(lay, self.tray_notifications_sw, self.notify_title,
                         self.notify_desc)
        lay.addWidget(hline())
        self.clip_title = label(tr("set.clip_watch"))
        self.clip_desc = label(tr("set.clip_watch_desc"), "caption", wrap=True)
        self.clip_watch_sw = Switch(bool(s.get("clip_watch", False)))
        self.clip_watch_sw.toggled.connect(lambda v: s.set("clip_watch", v))
        self._switch_row(lay, self.clip_watch_sw, self.clip_title, self.clip_desc,
                         divider=False)
        body.addWidget(card)
        body.addStretch(1)

        # ========================================================== DATA pane
        body = self.panes["data"][1]
        self.pane_titles["data"] = label(tr("set.data"), "h2")
        body.addWidget(self.pane_titles["data"])
        body.addSpacing(4)

        card, lay = _section(tr("set.data"))
        self.clr_btn = Button(tr("hist.clear"), "danger", "trash", "sm")
        self.clr_btn.clicked.connect(self._clear)
        self.hist_title, self.count = self._labeled_row(lay, tr("set.history"), " ", self.clr_btn)
        lay.addWidget(hline())
        self.exp_btn = Button(tr("set.export"), "", "inbox", "sm")
        self.exp_btn.clicked.connect(lambda: self.ctx.export_history(self))
        self.exp_title, self.exp_desc = self._labeled_row(lay, tr("set.export"),
                                                          tr("set.export_desc"), self.exp_btn)
        lay.addWidget(hline())
        self.loc_btn = Button(tr("btn.open_folder"), "", "folder", "sm")
        self.loc_btn.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(os.path.dirname(s.path))))
        self.appdata_title, self.appdata_label = self._labeled_row(
            lay, tr("set.app_data"), os.path.dirname(s.path), self.loc_btn, last=True)
        body.addWidget(card)
        body.addStretch(1)

        # ========================================================== ABOUT pane
        body = self.panes["about"][1]
        self.pane_titles["about"] = label(tr("set.about"), "h2")
        body.addWidget(self.pane_titles["about"])
        body.addSpacing(4)

        # developer hero — photo, name, version chips
        self.about_hero = DeveloperHero(AUTHOR, VERSION)
        body.addWidget(self.about_hero)

        # what's inside
        card, lay = _section(tr("about.features"))
        self.feat_card_title = lay.itemAt(0).widget()
        lay.addSpacing(6)
        lay.addWidget(FeatureGrid(FEATURES))
        lay.addSpacing(14)
        body.addWidget(card)

        # build details
        card, lay = _section(tr("about.build"))
        self.build_card_title = lay.itemAt(0).widget()
        self.build_btn = Button(tr("dlg.copy_info"), "", "copy", "sm")
        self.build_btn.clicked.connect(lambda: QGuiApplication.clipboard().setText(
            f"Transfer Now {VERSION} — developed by {AUTHOR}\n{tech_string()}"))
        self.build_title, self.build_desc = self._labeled_row(
            lay, f"Transfer Now {VERSION}", tech_string(), self.build_btn, last=True)
        body.addWidget(card)

        card, lay = _section(tr("set.about"))
        self.about_btn = Button("About…", "", "info", "sm")
        self.about_btn.clicked.connect(lambda: AboutDialog(self.window()).exec())
        self.about_title, self.about_desc = self._labeled_row(
            lay, tr("set.about_row", v=VERSION), tr("set.about_desc", author=AUTHOR),
            self.about_btn)
        lay.addWidget(hline())
        self.wiz_btn = Button(tr("wiz.run_again"), "", "sparkles", "sm")
        self.wiz_btn.clicked.connect(self._run_wizard)
        self.wiz_title, self.wiz_desc = self._labeled_row(
            lay, tr("wiz.again_title"), tr("wiz.again_desc"), self.wiz_btn)
        lay.addWidget(hline())
        self.sc_btn = Button(tr("set.view"), "", "keyboard", "sm")
        self.sc_btn.clicked.connect(lambda: ShortcutsDialog(self.window()).exec())
        self.sc_title, _dl = self._labeled_row(lay, tr("dash.shortcuts"), "", self.sc_btn, last=True)
        body.addWidget(card)
        body.addStretch(1)

        # --------------------------------------------------------------- glue
        ctx.history_changed.connect(self._update_count)
        self._update_count()
        s.on_change(self._external_change)
        self._sender_mode(s.get("sender_mode", "random"), save=False)
        self.vol_seg.setEnabled(bool(s.get("sounds", True)))
        i18n.changed.connect(self._retranslate)
        self._show_section(str(s.get("settings_section", "appearance")), save=False)

    # ------------------------------------------------------------- scaffold
    def _make_pane(self) -> tuple[QScrollArea, QVBoxLayout]:
        sa = QScrollArea()
        sa.setObjectName("settingspane")
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.Shape.NoFrame)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sa.setMinimumHeight(430)
        host = QWidget()
        host.setObjectName("page")
        sa.setWidget(host)
        body = vbox(host, (0, 2, 26, 18), 20)
        return sa, body

    def _show_section(self, key: str, save: bool = True) -> None:
        if key not in self.panes:
            key = "appearance"
        for k, b in self.nav_btns.items():
            b.setChecked(k == key)
        self.stack.setCurrentWidget(self.panes[key][0])
        if save:
            self.ctx.settings.set("settings_section", key)
            sounds.play("nav")

    def _switch_row(self, lay, sw: Switch, title, desc, divider: bool = True) -> None:
        """Switch row with title + optional description label (desc may be None)."""
        w = QWidget()
        r = hbox(w, (0, 12, 0, 12), 16)
        col = vbox(spacing=2)
        col.addWidget(title)
        if desc is not None:
            col.addWidget(desc)
        r.addLayout(col, 1)
        r.addWidget(sw, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(hline() if divider else _invisible())
        lay.addWidget(w)

    @staticmethod
    def _labeled_row(lay, title: str, desc: str, control: QWidget, last: bool = False,
                     desc_inline: bool = False):
        """Row with title + optional description on the left, control right.
        Returns (title_label, desc_label)."""
        w = QWidget()
        r = hbox(w, (0, 12, 0, 12), 16)
        col = vbox(spacing=2)
        tl = label(title)
        col.addWidget(tl)
        dl: QLabel | None = None
        if desc:
            if desc_inline:
                dl = ElidedLabel(desc, "caption")
                col.addWidget(dl)
            else:
                dl = label(desc, "caption", wrap=True)
                col.addWidget(dl)
        r.addLayout(col, 1)
        r.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(hline() if not last else _invisible())
        lay.addWidget(w)
        if dl is None:
            dl = label("", "caption")
        return tl, dl

    # ----------------------------------------------------------------- logic
    def _pick_accent(self, key: str) -> None:
        for k, b in self.swatches.items():
            b.setChecked(k == key)
        self.ctx.settings.set("accent", key)

    def _pick_lang(self, code: str) -> None:
        i18n.set_language(code)
        self.ctx.settings.set("language", code)
        sounds.play("toggle")

    def _sync_lang_buttons(self) -> None:
        for code, b in self.lang_btns.items():
            b.setChecked(code == i18n.lang)

    def _recolor_swatches(self) -> None:
        for key, b in self.swatches.items():
            _n, dk, lt = ACCENTS[key]
            b.set_color(dk if theme.dark else lt)

    def _sound_toggled(self, on: bool) -> None:
        self.ctx.settings.set("sounds", on)
        self.vol_seg.setEnabled(on)
        if on:
            sounds.play("toggle")

    def _volume_changed(self, key: str) -> None:
        self.ctx.settings.set("sound_volume", key)
        sounds.play("success", force=True)

    def _test_toast(self, kind: str) -> None:
        text = {"success": "This is a success notification",
                "info": "This is an info notification",
                "error": "This is an error notification"}[kind]
        self.ctx.toast(text, kind)

    def _sender_mode(self, key: str, save: bool = True) -> None:
        if save:
            self.ctx.settings.set("sender_mode", key)
        self.email.setEnabled(key == "fixed")

    def _external_change(self, key: str) -> None:
        if key == "download_dir":
            self.folder.set_full_text(self.ctx.settings.download_folder)
        if key == "theme":
            for k, b in self.theme_cards.items():
                b.setChecked(k == self.ctx.settings.get("theme", "dark"))

    def _pick_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Choose download folder",
                                             self.ctx.settings.download_folder)
        if d:
            self.ctx.settings.set("download_dir", d)
            self.folder.set_full_text(d)

    def _update_count(self) -> None:
        n = len(self.ctx.history.entries())
        self.count.setText(f"{n} record{'s' if n != 1 else ''} stored locally.")

    def _run_wizard(self) -> None:
        from ..wizard import SetupWizard
        SetupWizard(self.window(), self.ctx.settings).exec()

    def _clear(self) -> None:
        n = len(self.ctx.history.entries())
        if not n:
            self.ctx.toast("History is already empty", "info")
            return
        if confirm(self.window(), "Clear history", f"Remove all {n} records?",
                   "This only clears this list.", yes="Clear all", danger=True):
            self.ctx.clear_history()
            self.ctx.toast(tr("toast.history_cleared"), "info")

    def _retranslate(self) -> None:
        for key, tkey, _ic in SECTIONS:
            self.nav_btns[key].set_text(tr(tkey))
            self.pane_titles[key].setText(tr(tkey))
        # appearance
        self.theme_title.setText(tr("set.theme"))
        self.theme_desc.setText(tr("set.theme_desc"))
        self.accent_title.setText(tr("set.accent"))
        self.accent_desc.setText(tr("set.accent_desc"))
        self.lang_desc.setText(tr("set.language_desc"))
        # notifications
        self.pos_title.setText(tr("set.notif_pos"))
        self.pos_desc.setText(tr("set.notif_pos_desc"))
        self.pos_seg.set_option_text("br", tr("set.pos_br"))
        self.pos_seg.set_option_text("bc", tr("set.pos_bc"))
        self.pos_seg.set_option_text("tr", tr("set.pos_tr"))
        self.pos_seg.set_option_text("tc", tr("set.pos_tc"))
        self.dur_title.setText(tr("set.notif_dur"))
        self.dur_desc.setText(tr("set.notif_dur_desc"))
        self.dur_seg.set_option_text("short", tr("set.dur_short"))
        self.dur_seg.set_option_text("normal", tr("set.dur_normal"))
        self.dur_seg.set_option_text("long", tr("set.dur_long"))
        self.dur_seg.set_option_text("sticky", tr("set.dur_sticky"))
        self.bar_title.setText(tr("set.notif_bar"))
        self.bar_desc.setText(tr("set.notif_bar_desc"))
        self.nsound_title.setText(tr("set.notif_sound"))
        self.nsound_desc.setText(tr("set.notif_sound_desc"))
        self.sound_title.setText(tr("set.sound_effects"))
        self.sound_desc.setText(tr("set.sound_desc"))
        self.vol_title.setText(tr("set.volume"))
        self.vol_seg.set_option_text("low", tr("set.low"))
        self.vol_seg.set_option_text("medium", tr("set.medium"))
        self.vol_seg.set_option_text("high", tr("set.high"))
        self.test_title.setText(tr("set.test"))
        self.test_desc.setText(tr("set.test_desc"))
        # transfers
        self.sender_title.setText(tr("set.sender_email"))
        self.sender_desc.setText(tr("set.sender_desc"))
        self.sender_seg.set_option_text("random", tr("up.random"))
        self.sender_seg.set_option_text("fixed", tr("up.my_email"))
        self.email_title.setText(tr("set.my_email"))
        self.email_desc.setText(tr("set.my_email_desc"))
        self.par_title.setText(tr("set.parallel"))
        self.par_desc.setText(tr("set.parallel_desc"))
        self.copy_title.setText(tr("set.auto_copy"))
        self.copy_desc.setText(tr("set.auto_copy_desc"))
        self.pace_title.setText(tr("set.pace"))
        self.pace_desc.setText(tr("set.pace_desc"))
        self.retry_title.setText(tr("set.auto_retry"))
        self.retry_desc.setText(tr("set.auto_retry_desc"))
        # downloads
        self.folder_title.setText(tr("set.save_loc"))
        self.folder_desc.setText(tr("set.save_loc_desc"))
        self.folder_btn.setText(tr("dl.change"))
        self.open_title.setText(tr("set.open_after"))
        # desktop
        self.tray_title.setText(tr("set.tray_icon"))
        self.tray_desc.setText(tr("set.tray_icon_desc"))
        self.mintray_title.setText(tr("set.min_tray"))
        self.mintray_desc.setText(tr("set.min_tray_desc"))
        self.notify_title.setText(tr("set.notify"))
        self.notify_desc.setText(tr("set.notify_desc"))
        self.clip_title.setText(tr("set.clip_watch"))
        self.clip_desc.setText(tr("set.clip_watch_desc"))
        # data
        self.hist_title.setText(tr("set.history"))
        self.clr_btn.setText(tr("hist.clear"))
        self.exp_title.setText(tr("set.export"))
        self.exp_desc.setText(tr("set.export_desc"))
        self.exp_btn.setText(tr("set.export"))
        self.loc_btn.setText(tr("btn.open_folder"))
        # about
        self.feat_card_title.setText(tr("about.features"))
        self.build_card_title.setText(tr("about.build"))
        self.build_btn.setText(tr("dlg.copy_info"))
        self.about_title.setText(tr("set.about_row", v=VERSION))
        self.about_desc.setText(tr("set.about_desc", author=AUTHOR))
        self.about_btn.setText("About…")
        self.wiz_title.setText(tr("wiz.again_title"))
        self.wiz_desc.setText(tr("wiz.again_desc"))
        self.wiz_btn.setText(tr("wiz.run_again"))
        self.sc_title.setText(tr("dash.shortcuts"))
        self.sc_btn.setText(tr("set.view"))
        for k, b in self.theme_cards.items():
            b.setText(THEME_LABELS.get(k, k))

    def on_show(self) -> None:
        self._update_count()


def _invisible() -> QWidget:
    f = QFrame()
    f.setFixedHeight(0)
    return f
