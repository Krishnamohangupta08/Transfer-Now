"""First-launch setup wizard — welcome → appearance → language → desktop → done.

A frameless, Shade-styled guide that opens the first time the app starts
(and can be re-run from Help ▸ Run Setup Wizard or Settings ▸ About).
Every choice applies live: theme, accent and language update the running
app behind the wizard, so the user sees the result before committing.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QFileDialog, QFrame, QGridLayout, QLabel,
    QStackedWidget, QWidget,
)

from . import icons
from .dialogs import FramelessDialog
from .i18n import LANGS, i18n, tr
from .menus import LineEdit
from .pages.settings import ThemeCard
from .iconfx import AnimatedLogo
from .sounds import sounds
from .theme import ACCENTS, THEME_LABELS, theme
from .widgets import (
    Badge, Button, ElidedLabel, FlagButton, Switch, Swatch, hbox, hline,
    label, vbox,
)

WIZARD_KEYS = ("dark", "black", "navy", "cream", "light")
STACK_H = 348                  # fixed page height: no jumping between steps


class SetupWizard(FramelessDialog):
    """Step-by-step onboarding. Parent is the AppWindow (or any widget)."""

    def __init__(self, parent: Optional[QWidget], settings=None) -> None:
        super().__init__(parent, 620, (26, 22, 26, 20))
        self.setWindowTitle("Setup wizard")
        self._s = settings
        if self._s is None and parent is not None and hasattr(parent, "ctx"):
            self._s = parent.ctx.settings
        self._theme_group = QButtonGroup(self)
        self._theme_group.setExclusive(True)
        self._accent_group = QButtonGroup(self)
        self._accent_group.setExclusive(True)
        self._lang_group = QButtonGroup(self)
        self._lang_group.setExclusive(True)
        self._build()

    # ------------------------------------------------------------ helpers
    def _get(self, key: str, default):
        return self._s.get(key, default) if self._s is not None else default

    def _set(self, key: str, value) -> None:
        if self._s is not None:
            self._s.set(key, value)

    def _apply_now(self) -> None:
        """Fallback live-apply when no Settings listener is installed."""
        app = QApplication.instance()
        if app is None:
            return
        theme.apply(app, str(self._get("theme", "dark")),
                    self._get("accent", None) or None)

    def _build(self) -> None:
        body = self.body

        # ---- header: overline · dots · skip
        head = hbox(spacing=10)
        self.overline = label(tr("wiz.setup").upper(), "overline")
        head.addWidget(self.overline, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        dots_row = hbox(spacing=6)
        self._dots: list[QFrame] = []
        for _ in range(5):
            d = QFrame()
            d.setObjectName("wizdot")
            d.setProperty("on", False)
            dots_row.addWidget(d)
            self._dots.append(d)
        head.addLayout(dots_row)
        head.addStretch(1)
        self.skip = Button(tr("wiz.skip"), "ghost", "", "sm")
        self.skip.clicked.connect(self.reject)
        head.addWidget(self.skip, 0, Qt.AlignmentFlag.AlignVCenter)
        body.addLayout(head)
        body.addSpacing(10)

        # ---- pages (fixed height so nothing jumps between steps)
        self.stack = QStackedWidget()
        self.stack.setFixedHeight(STACK_H)
        self.stack.addWidget(self._page_welcome())
        self.stack.addWidget(self._page_appearance())
        self.stack.addWidget(self._page_language())
        self.stack.addWidget(self._page_desktop())
        self.stack.addWidget(self._page_finish())
        body.addWidget(self.stack)
        body.addSpacing(12)
        body.addWidget(hline())
        body.addSpacing(10)

        # ---- footer: step caption · back · next
        foot = hbox(spacing=10)
        self.step_lbl = label("", "caption")
        foot.addWidget(self.step_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        foot.addStretch(1)
        self.back = Button(tr("wiz.back"), "ghost", "arrow-left")
        self.back.clicked.connect(self._go_back)
        foot.addWidget(self.back, 0, Qt.AlignmentFlag.AlignVCenter)
        self.next = Button("", "primary", "arrow-right")
        self.next.setMinimumWidth(148)
        self.next.clicked.connect(self._go_next)
        foot.addWidget(self.next, 0, Qt.AlignmentFlag.AlignVCenter)
        body.addLayout(foot)

        i18n.changed.connect(self._retranslate)
        self.stack.setCurrentIndex(0)
        self._sync()

    # --------------------------------------------------------------- pages
    def _page_welcome(self) -> QWidget:
        p = QWidget()
        col = vbox(p, (16, 4, 16, 0), 0)
        logo = AnimatedLogo(84, pad=26, glow=True, breathe=True)
        col.addWidget(logo, 0, Qt.AlignmentFlag.AlignHCenter)
        col.addSpacing(14)
        self.welcome_title = label(tr("wiz.welcome"), "h1")
        self.welcome_title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        col.addWidget(self.welcome_title)
        col.addSpacing(6)
        self.welcome_sub = label(tr("wiz.welcome_sub"), "muted", wrap=True)
        self.welcome_sub.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        col.addWidget(self.welcome_sub)
        col.addStretch(1)

        chips = hbox(spacing=10)
        chips.addStretch(1)
        self._chip_lbls: list[QLabel] = []
        self._chip_icons: list[QLabel] = []
        for icon_name, key in (("sun", "wiz.chip_themes"),
                               ("languages", "wiz.chip_langs"),
                               ("activity", "wiz.chip_transfers")):
            chip = QFrame()
            chip.setObjectName("pill")
            cl = hbox(chip, (12, 7, 12, 7), 8)
            ic = label("")
            ic.setPixmap(icons.pixmap(icon_name, theme.pal["accent"], 15, 1.7))
            ic.setFixedSize(15, 15)
            cl.addWidget(ic, 0, Qt.AlignmentFlag.AlignVCenter)
            lb = label(tr(key))
            cl.addWidget(lb, 0, Qt.AlignmentFlag.AlignVCenter)
            chips.addWidget(chip, 0, Qt.AlignmentFlag.AlignVCenter)
            self._chip_icons.append(ic)
            self._chip_lbls.append(lb)
        chips.addStretch(1)
        col.addLayout(chips)
        col.addStretch(1)
        return p

    def _page_appearance(self) -> QWidget:
        p = QWidget()
        col = vbox(p, (4, 4, 6, 0), 8)
        self.s1_title = label(tr("wiz.s1_title"), "h2")
        col.addWidget(self.s1_title)
        self.s1_sub = label(tr("wiz.s1_sub"), "muted", wrap=True)
        col.addWidget(self.s1_sub)
        col.addSpacing(6)

        grid = QGridLayout()
        grid.setSpacing(12)
        current = str(self._get("theme", "dark"))
        for i, key in enumerate(WIZARD_KEYS):
            card = ThemeCard(key)
            group = self._theme_group
            group.addButton(card)
            if key == current or (current not in WIZARD_KEYS and key == "dark"):
                card.setChecked(True)          # checked after joining the group
            card.toggled.connect(lambda on, k=key: self._pick_theme(k, on))
            grid.addWidget(card, i // 3, i % 3)
        for c in range(3):
            grid.setColumnStretch(c, 1)
        col.addLayout(grid)
        col.addSpacing(4)

        self.acc_cap = label(tr("set.accent").upper(), "overline")
        col.addWidget(self.acc_cap)
        arow = hbox(spacing=12)
        cur_acc = str(self._get("accent", "signal"))
        for name, (_lbl, dk, lt) in ACCENTS.items():
            sw = Swatch(dk if theme.dark else lt, name)
            self._accent_group.addButton(sw)
            if name == cur_acc:
                sw.setChecked(True)
            sw.toggled.connect(lambda on, n=name: self._pick_accent(n, on))
            arow.addWidget(sw, 0, Qt.AlignmentFlag.AlignVCenter)
        arow.addStretch(1)
        col.addLayout(arow)
        col.addStretch(1)
        return p

    def _page_language(self) -> QWidget:
        p = QWidget()
        col = vbox(p, (4, 4, 6, 0), 8)
        self.s2_title = label(tr("wiz.s2_title"), "h2")
        col.addWidget(self.s2_title)
        self.s2_sub = label(tr("wiz.s2_sub"), "muted", wrap=True)
        col.addWidget(self.s2_sub)
        col.addSpacing(10)

        grid = QGridLayout()
        grid.setSpacing(10)
        current = str(self._get("language", "en"))
        for i, code in enumerate(LANGS):
            b = FlagButton(code)
            b.setStyleSheet("font-size: 13px;")   # keeps long native names unclipped
            self._lang_group.addButton(b)
            if code == current:
                b.setChecked(True)
            b.toggled.connect(lambda on, c=code: self._pick_lang(c, on))
            grid.addWidget(b, i // 4, i % 4)
        for c in range(4):
            grid.setColumnStretch(c, 1)
        col.addLayout(grid)
        col.addStretch(1)
        return p

    def _page_desktop(self) -> QWidget:
        p = QWidget()
        col = vbox(p, (4, 4, 6, 0), 8)
        self.s3_title = label(tr("wiz.s3_title"), "h2")
        col.addWidget(self.s3_title)
        self.s3_sub = label(tr("wiz.s3_sub"), "muted", wrap=True)
        col.addWidget(self.s3_sub)
        col.addSpacing(12)

        self.folder_cap = label(tr("wiz.s3_folder").upper(), "overline")
        col.addWidget(self.folder_cap)
        col.addSpacing(4)
        frame = QFrame()
        frame.setObjectName("inner")
        fl = hbox(frame, (12, 9, 12, 9), 10)
        ic = label("")
        ic.setPixmap(icons.pixmap("folder", theme.pal["text2"], 17, 1.6))
        ic.setFixedSize(17, 17)
        fl.addWidget(ic, 0, Qt.AlignmentFlag.AlignVCenter)
        self.folder_edit = LineEdit()
        self.folder_edit.setReadOnly(True)
        self.folder_edit.setFrame(False)
        self.folder_edit.setText(self._s.download_folder if self._s else "")
        self.folder_edit.setStyleSheet("QLineEdit { background: transparent; border: none; padding: 0; }")
        fl.addWidget(self.folder_edit, 1)
        browse = Button(tr("wiz.browse"), "", "folder", "sm")
        browse.clicked.connect(self._pick_folder)
        fl.addWidget(browse, 0, Qt.AlignmentFlag.AlignVCenter)
        col.addWidget(frame)
        col.addSpacing(12)

        self.tray_sw = Switch(bool(self._get("tray_icon", True)))
        self.tray_sw.toggled.connect(lambda v: self._set("tray_icon", v))
        self.tray_lbl = label(tr("wiz.s3_tray"))
        row1 = hbox(spacing=10)
        row1.addWidget(self.tray_lbl, 1, Qt.AlignmentFlag.AlignVCenter)
        row1.addWidget(self.tray_sw, 0, Qt.AlignmentFlag.AlignVCenter)
        col.addLayout(row1)
        col.addSpacing(4)
        col.addWidget(hline())
        col.addSpacing(4)
        self.notify_sw = Switch(bool(self._get("tray_notifications", True)))
        self.notify_sw.toggled.connect(lambda v: self._set("tray_notifications", v))
        self.notify_lbl = label(tr("wiz.s3_notify"))
        row2 = hbox(spacing=10)
        row2.addWidget(self.notify_lbl, 1, Qt.AlignmentFlag.AlignVCenter)
        row2.addWidget(self.notify_sw, 0, Qt.AlignmentFlag.AlignVCenter)
        col.addLayout(row2)
        col.addStretch(1)
        return p

    def _page_finish(self) -> QWidget:
        p = QWidget()
        col = vbox(p, (10, 4, 10, 0), 0)
        head = hbox(spacing=14)
        head.addStretch(1)
        self._badge = Badge("check-circle", 54, "success")
        head.addWidget(self._badge, 0, Qt.AlignmentFlag.AlignVCenter)
        hcol = vbox(spacing=2)
        self.s4_title = label(tr("wiz.s4_title"), "h1")
        hcol.addWidget(self.s4_title)
        self.s4_sub = label(tr("wiz.s4_sub"), "muted", wrap=True)
        hcol.addWidget(self.s4_sub)
        head.addLayout(hcol)
        head.addStretch(1)
        col.addLayout(head)
        col.addSpacing(14)

        card = QFrame()
        card.setObjectName("inner")
        cl = vbox(card, (14, 10, 14, 10), 8)
        self.sum_cap = label(tr("wiz.s4_summary").upper(), "overline")
        cl.addWidget(self.sum_cap)
        # Theme · accent
        r1 = hbox(spacing=10)
        self.sum_theme_cap = label(tr("set.theme"), "muted")
        r1.addWidget(self.sum_theme_cap, 0, Qt.AlignmentFlag.AlignVCenter)
        r1.addStretch(1)
        self.sum_theme_val = label("", "")
        self.sum_theme_val.setAlignment(Qt.AlignmentFlag.AlignRight
                                        | Qt.AlignmentFlag.AlignVCenter)
        r1.addWidget(self.sum_theme_val, 0, Qt.AlignmentFlag.AlignVCenter)
        cl.addLayout(r1)
        cl.addWidget(hline())
        # Language
        r2 = hbox(spacing=10)
        self.sum_lang_cap = label(tr("set.language"), "muted")
        r2.addWidget(self.sum_lang_cap, 0, Qt.AlignmentFlag.AlignVCenter)
        r2.addStretch(1)
        self.sum_lang_val = label("", "")
        r2.addWidget(self.sum_lang_val, 0, Qt.AlignmentFlag.AlignVCenter)
        cl.addLayout(r2)
        cl.addWidget(hline())
        # Download folder
        r3 = hbox(spacing=10)
        self.sum_dl_cap = label(tr("set.downloads"), "muted")
        r3.addWidget(self.sum_dl_cap, 0, Qt.AlignmentFlag.AlignVCenter)
        r3.addStretch(1)
        self.sum_dl_val = ElidedLabel("")
        self.sum_dl_val.setAlignment(Qt.AlignmentFlag.AlignRight
                                     | Qt.AlignmentFlag.AlignVCenter)
        r3.addWidget(self.sum_dl_val, 1, Qt.AlignmentFlag.AlignVCenter)
        cl.addLayout(r3)
        col.addWidget(card)
        col.addStretch(1)
        return p

    # ------------------------------------------------------------- actions
    def _pick_theme(self, key: str, on: bool) -> None:
        if not on:
            return
        self._set("theme", key)
        self._apply_now()

    def _pick_accent(self, name: str, on: bool) -> None:
        if not on:
            return
        self._set("accent", name)
        self._apply_now()

    def _pick_lang(self, code: str, on: bool) -> None:
        if not on:
            return
        self._set("language", code)
        i18n.set_language(code)

    def _pick_folder(self) -> None:
        start = self.folder_edit.text() or ""
        path = QFileDialog.getExistingDirectory(self, tr("wiz.s3_folder"), start)
        if path:
            self.folder_edit.setText(path)
            self._set("download_dir", path)

    def _go_next(self) -> None:
        i = self.stack.currentIndex()
        if i >= self.stack.count() - 1:
            sounds.play("success")
            self.accept()
            return
        self.stack.setCurrentIndex(i + 1)
        sounds.play("nav")
        if self.stack.currentIndex() == self.stack.count() - 1:
            self._refresh_summary()
        self._sync()

    def _go_back(self) -> None:
        i = self.stack.currentIndex()
        if i > 0:
            self.stack.setCurrentIndex(i - 1)
            sounds.play("nav")
            self._sync()

    def _refresh_summary(self) -> None:
        tkey = str(self._get("theme", "dark"))
        tname = THEME_LABELS.get(tkey, tkey)
        akey = str(self._get("accent", "signal"))
        aname = ACCENTS.get(akey, ("", ""))[0]
        self.sum_theme_val.setText(f"{tname} · {aname}" if aname else tname)
        self.sum_lang_val.setText(i18n.native_name())
        self.sum_dl_val.set_full_text(self._s.download_folder if self._s else "")

    def _sync(self) -> None:
        i = self.stack.currentIndex()
        last = i == self.stack.count() - 1
        self.back.setVisible(i > 0)
        self.next.setText(tr("wiz.finish") if last
                          else tr("wiz.get_started") if i == 0 else tr("wiz.next"))
        self.next.set_icon_name("" if last else "arrow-right")
        self.skip.setVisible(not last)
        self.step_lbl.setText(tr("wiz.step", n=i + 1, total=self.stack.count()))
        if last:
            self._refresh_summary()
        for n, d in enumerate(self._dots):
            d.setProperty("on", n == i)
            d.style().unpolish(d)
            d.style().polish(d)

    # ---------------------------------------------------------- lifecycle
    def done(self, result: int) -> None:  # noqa: N802
        """Any exit — accept, reject, Esc or skip — marks the wizard done."""
        self._set("wizard_done", True)
        super().done(result)

    def _retranslate(self) -> None:
        self.overline.setText(tr("wiz.setup").upper())
        self.skip.setText(tr("wiz.skip"))
        self.back.setText(tr("wiz.back"))
        self.welcome_title.setText(tr("wiz.welcome"))
        self.welcome_sub.setText(tr("wiz.welcome_sub"))
        for lb, key in zip(self._chip_lbls,
                           ("wiz.chip_themes", "wiz.chip_langs", "wiz.chip_transfers")):
            lb.setText(tr(key))
        self.s1_title.setText(tr("wiz.s1_title"))
        self.s1_sub.setText(tr("wiz.s1_sub"))
        self.acc_cap.setText(tr("set.accent").upper())
        self.s2_title.setText(tr("wiz.s2_title"))
        self.s2_sub.setText(tr("wiz.s2_sub"))
        self.s3_title.setText(tr("wiz.s3_title"))
        self.s3_sub.setText(tr("wiz.s3_sub"))
        self.folder_cap.setText(tr("wiz.s3_folder").upper())
        self.tray_lbl.setText(tr("wiz.s3_tray"))
        self.notify_lbl.setText(tr("wiz.s3_notify"))
        self.s4_title.setText(tr("wiz.s4_title"))
        self.s4_sub.setText(tr("wiz.s4_sub"))
        self.sum_cap.setText(tr("wiz.s4_summary").upper())
        self.sum_theme_cap.setText(tr("set.theme"))
        self.sum_lang_cap.setText(tr("set.language"))
        self.sum_dl_cap.setText(tr("set.downloads"))
        self._sync()
        self._refresh_summary()


def run_wizard_if_needed(parent: QWidget, settings) -> None:
    """Open the wizard once, on first launch (called from main.py)."""
    if settings.get("wizard_done", False):
        return
    SetupWizard(parent, settings).exec()
