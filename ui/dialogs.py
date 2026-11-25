"""About + keyboard-shortcut + schedule dialogs (glass style)."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from typing import Optional

from PyQt6.QtCore import QDate, Qt
from PyQt6.QtGui import QColor, QGuiApplication, QTextCharFormat
from PyQt6.QtWidgets import (
    QDialog, QFrame, QGraphicsDropShadowEffect, QCalendarWidget, QWidget,
)

from . import icons, motion
from .about import DeveloperHero, FeatureGrid, tech_string
from .i18n import tr
from .iconfx import AnimatedLogo
from .sounds import sounds
from .menus import LineEdit
from .widgets import Badge, Button, hbox, hline, kbd, label, vbox

from core.version import APP_VERSION as VERSION  # single source of truth
AUTHOR = "Krishna Mohan Gupta"

SHORTCUTS = [
    ("File", [("Ctrl+U", "New upload"), ("Ctrl+O", "Add files…"), ("Ctrl+D", "Download from link"),
              ("Ctrl+L", "Check saved links"), ("Ctrl+Q", "Quit")]),
    ("Transfer", [("Ctrl+Enter", "Get share link / start download"),
                  ("Ctrl+Shift+C", "Copy last link"), ("Ctrl+Shift+V", "Paste link & fetch")]),
    ("View", [("Ctrl+1…4", "Switch page"), ("Ctrl+H", "History"), ("Ctrl+F", "Search history"),
              ("Ctrl+,", "Settings"), ("Ctrl+B", "Toggle sidebar"), ("Ctrl+Shift+M", "Sound on / off"),
              ("F11", "Full screen")]),
    ("Help", [("Ctrl+K", "Command palette"), ("F1", "Keyboard shortcuts")]),
]

FEATURES = [
    ("arrow-up", "about.f1"),
    ("folder", "about.f2"),
    ("cloud-upload", "about.f3"),
    ("queue", "about.f4"),
    ("calendar-clock", "about.f5"),
    ("arrow-down", "about.f6"),
    ("shield-check", "about.f7"),
    ("tray", "about.f8"),
    ("sparkles", "about.f9"),
    ("keyboard", "about.f10"),
]


class FramelessDialog(QDialog):
    """Window-less, rounded, draggable dialog (no OS title bar)."""

    open_sound = "open"

    def __init__(self, parent: QWidget, width: int = 440, margins=(28, 24, 28, 22)) -> None:
        super().__init__(parent)
        self.setObjectName("fdialog")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setFixedWidth(width + 36)
        outer = vbox(self, (18, 14, 18, 22))
        self.frame = QFrame()
        self.frame.setObjectName("dlgframe")
        fx = QGraphicsDropShadowEffect(self.frame)
        fx.setBlurRadius(34)
        fx.setOffset(0, 6)
        fx.setColor(QColor(0, 0, 0, 150))
        self.frame.setGraphicsEffect(fx)
        outer.addWidget(self.frame)
        self.body = vbox(self.frame, margins, 6)

    def exec(self) -> int:
        """Run modally with the main window blurred + dimmed behind the dialog."""
        win = self.parentWidget().window() if self.parentWidget() is not None else None
        has_dim = win is not None and hasattr(win, "dim_in")
        if has_dim:
            win.dim_in()
        try:
            return super().exec()
        finally:
            if has_dim:
                win.dim_out()

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        sounds.play(self.open_sound)
        par = self.parentWidget()
        if par is not None:
            g = par.window().frameGeometry()
            self.move(g.center().x() - self.width() // 2, g.center().y() - self.height() // 2)
        # Shade entrance: fade + gentle rise on the signature curve
        motion.dialog_in(self)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.windowHandle():
            self.windowHandle().startSystemMove()
        super().mousePressEvent(e)


def confirm(parent: QWidget, title: str, text: str, info: str = "", yes: str = "OK",
            danger: bool = False, cancel: str = "Cancel") -> bool:
    """Custom frameless confirmation dialog. True when the user accepts."""
    d = FramelessDialog(parent, 420)
    d.open_sound = "warning" if danger else "open"
    head = hbox(spacing=14)
    head.addWidget(Badge("alert" if danger else "info", 46, "danger" if danger else "accent"),
                   0, Qt.AlignmentFlag.AlignTop)
    col = vbox(spacing=4)
    col.addWidget(label(title, "h2"))
    col.addWidget(label(text, "", wrap=True))
    if info:
        col.addWidget(label(info, "caption", wrap=True))
    head.addLayout(col, 1)
    d.body.addLayout(head)
    d.body.addSpacing(14)
    row = hbox(spacing=10)
    row.addStretch(1)
    no = Button(cancel)
    no.clicked.connect(d.reject)
    ok = Button(yes, "danger" if danger else "primary")
    ok.clicked.connect(d.accept)
    ok.setDefault(True)
    row.addWidget(no)
    row.addWidget(ok)
    d.body.addLayout(row)
    no.setFocus()
    return d.exec() == QDialog.DialogCode.Accepted


class AboutDialog(FramelessDialog):
    """Premium About: developer hero, app summary, feature grid, build info."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, 580, (22, 22, 22, 20))
        self.setWindowTitle("About Transfer Now")
        lay = self.body
        lay.setSpacing(0)

        # ---- developer hero (photo + name + version chips)
        lay.addWidget(DeveloperHero(AUTHOR, VERSION, compact=True))
        lay.addSpacing(18)

        # ---- app identity row
        ident = hbox(spacing=14)
        logo = AnimatedLogo(44, pad=4)
        ident.addWidget(logo, 0, Qt.AlignmentFlag.AlignTop)
        col = vbox(spacing=2)
        col.addWidget(label("Transfer Now", "h2"))
        col.addWidget(label(tr("dlg.about_line"), "muted", wrap=True))
        ident.addLayout(col, 1)
        lay.addLayout(ident)
        lay.addSpacing(18)

        # ---- what's inside
        lay.addWidget(label(tr("about.features").upper(), "overline"))
        lay.addSpacing(10)
        lay.addWidget(FeatureGrid(FEATURES))
        lay.addSpacing(16)
        lay.addWidget(hline())
        lay.addSpacing(12)

        # ---- build + legal
        tech = tech_string()
        t = label(tech, "caption")
        t.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(t)
        c = label(tr("about.copyright", author=AUTHOR), "caption")
        c.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(c)
        lay.addSpacing(16)

        row = hbox(spacing=10)
        copy = Button(tr("dlg.copy_info"), "", "copy")
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(
            f"Transfer Now {VERSION} — developed by {AUTHOR}\n{tech}"))
        close = Button(tr("dlg.close"), "primary")
        close.clicked.connect(self.accept)
        close.setDefault(True)
        row.addWidget(copy)
        row.addWidget(close, 1)
        lay.addLayout(row)


GROUP_ICON = {"File": "folder", "Transfer": "send", "View": "monitor", "Help": "help"}


class ShortcutsDialog(FramelessDialog):
    """Searchable cheat-sheet: four group cards in two columns, key-caps on the right."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, 820, (30, 26, 30, 22))
        self.setWindowTitle(tr("sc.title"))
        lay = self.body
        lay.setSpacing(0)

        # ---- header: icon + title/subtitle + live search
        head = hbox(spacing=14)
        head.addWidget(Badge("keyboard", 46, round_=False), 0, Qt.AlignmentFlag.AlignVCenter)
        hc = vbox(spacing=1)
        hc.addWidget(label(tr("sc.title"), "h1"))
        hc.addWidget(label(tr("sc.sub"), "subtitle"))
        head.addLayout(hc, 1)
        self.search = LineEdit()
        self.search.setPlaceholderText(tr("sc.search"))
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(240)
        head.addWidget(self.search, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(head)
        lay.addSpacing(18)

        # ---- group cards
        self._groups: list[tuple[QFrame, list[tuple[QWidget, str]]]] = []

        def group_card(group: str, rows) -> QFrame:
            card = QFrame()
            card.setObjectName("inner")
            col = vbox(card, (18, 16, 18, 10), 0)
            gh = hbox(spacing=10)
            gh.addWidget(Badge(GROUP_ICON.get(group, "info"), 28, round_=False))
            gh.addWidget(label(group, "h2"), 1)
            col.addLayout(gh)
            col.addSpacing(8)
            items: list[tuple[QWidget, str]] = []
            for i, (keys, what) in enumerate(rows):
                if i:
                    pass
                w = QWidget()
                r = hbox(w, (0, 8, 0, 8), 8)
                r.addWidget(label(what, "muted"), 1)
                for j, k in enumerate(keys.split("+")):
                    if j:
                        r.addWidget(label("+", "caption"), 0, Qt.AlignmentFlag.AlignVCenter)
                    r.addWidget(kbd(k), 0, Qt.AlignmentFlag.AlignVCenter)
                col.addWidget(w)
                items.append((w, f"{group} {keys} {what}".lower()))
            col.addStretch(1)
            self._groups.append((card, items))
            return card

        groups = dict(SHORTCUTS)
        cols = hbox(spacing=14)
        for names in (("File", "Transfer"), ("View", "Help")):
            col = vbox(spacing=14)
            for n in names:
                col.addWidget(group_card(n, groups[n]))
            col.addStretch(1)
            cols.addLayout(col, 1)
        self.cols_host = QWidget()
        self.cols_host.setLayout(cols)
        lay.addWidget(self.cols_host)

        self.none = label(tr("sc.none"), "muted")
        self.none.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.none.setContentsMargins(0, 40, 0, 40)
        self.none.hide()
        lay.addWidget(self.none)
        self.search.textChanged.connect(self._filter)

        # ---- footer
        lay.addSpacing(16)
        foot = hbox(spacing=8)
        foot.addWidget(kbd("F1"))
        foot.addWidget(label(tr("sc.f1"), "caption"))
        foot.addStretch(1)
        close = Button(tr("dlg.close"), "primary")
        close.clicked.connect(self.accept)
        close.setDefault(True)
        close.setMinimumWidth(120)
        foot.addWidget(close)
        lay.addLayout(foot)
        self.search.setFocus()

    def _filter(self, text: str) -> None:
        q = text.strip().lower()
        any_vis = False
        for card, items in self._groups:
            vis = 0
            for w, hay in items:
                show = not q or q in hay
                w.setVisible(show)
                vis += show
            card.setVisible(bool(vis))
            any_vis = any_vis or bool(vis)
        self.cols_host.setVisible(any_vis)
        self.none.setVisible(not any_vis)


class _Stepper(QFrame):
    """macOS-style value stepper: [ − ] 09 [ + ] with an HH/MM caption."""

    def __init__(self, caption: str, lo: int, hi: int, step: int, value: int) -> None:
        super().__init__()
        self.setObjectName("stepper")
        self.setFixedSize(118, 84)
        self.lo, self.hi, self.step = lo, hi, step
        col = vbox(self, (10, 8, 10, 8), 2)
        cap = label(caption, "caption")
        cap.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        col.addWidget(cap)
        row = hbox(spacing=6)
        self.minus = Button("", "ghost", "minus", "icon")
        self.minus.clicked.connect(lambda: self.bump(-1))
        row.addWidget(self.minus)
        self.val = label(f"{value:02d}")
        self.val.setObjectName("stepval")
        self.val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self.val, 1)
        self.plus = Button("", "ghost", "plus", "icon")
        self.plus.clicked.connect(lambda: self.bump(+1))
        row.addWidget(self.plus)
        col.addLayout(row)

    def bump(self, direction: int) -> None:
        v = int(self.val.text()) + direction * self.step
        span = self.hi - self.lo + 1
        v = (v - self.lo) % span + self.lo          # wrap around
        self.val.setText(f"{v:02d}")

    def value(self) -> int:
        return int(self.val.text())

    def set_value(self, v: int) -> None:
        self.val.setText(f"{max(self.lo, min(self.hi, v)):02d}")


class ScheduleDialog(FramelessDialog):
    """Custom date & time picker — themed month calendar + HH/MM steppers."""

    def __init__(self, parent: QWidget, initial: datetime) -> None:
        super().__init__(parent, 400, (26, 22, 26, 20))
        self.setWindowTitle(tr("sched.title"))
        self._build(initial)

    def _build(self, initial: datetime) -> None:
        # ---- title
        head = hbox(spacing=12)
        head.addWidget(Badge("calendar-clock", 42), 0, Qt.AlignmentFlag.AlignTop)
        col = vbox(spacing=1)
        col.addWidget(label(tr("sched.title"), "h2"))
        col.addWidget(label(tr("up.scheduled") + " · TransferNow", "muted"))
        head.addLayout(col, 1)
        self.body.addLayout(head)
        self.body.addSpacing(10)

        # ---- date: themed month calendar
        self.body.addWidget(label(tr("sched.date").upper(), "overline"))
        self.cal = QCalendarWidget()
        self.cal.setGridVisible(False)
        self.cal.setVerticalHeaderFormat(
            QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.cal.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        self.cal.setMinimumDate(QDate.currentDate())
        self.cal.setSelectedDate(QDate(initial.year, initial.month, initial.day))
        # weekends in the theme text colour, not Qt's default red
        plain = QTextCharFormat()
        for day in (Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday):
            self.cal.setWeekdayTextFormat(day, plain)
        self.body.addWidget(self.cal)
        self.body.addSpacing(6)

        # ---- time: custom HH / MM steppers
        self.body.addWidget(label(tr("sched.time").upper(), "overline"))
        trow = hbox(spacing=12)
        self.hour = _Stepper("HH", 0, 23, 1, initial.hour)
        self.minute = _Stepper("MM", 0, 59, 5, initial.minute)
        trow.addWidget(self.hour)
        trow.addWidget(self.minute)
        trow.addStretch(1)
        self.body.addLayout(trow)
        self.body.addSpacing(4)

        # ---- live preview of the exact start moment
        self.preview = label("", "muted")
        self.body.addWidget(self.preview)

        # ---- buttons
        self.body.addSpacing(8)
        row = hbox(spacing=10)
        row.addStretch(1)
        no = Button(tr("dlg.cancel"))
        no.clicked.connect(self.reject)
        ok = Button(tr("dlg.ok"), "primary", "check")
        ok.clicked.connect(self.accept)
        ok.setDefault(True)
        row.addWidget(no)
        row.addWidget(ok)
        self.body.addLayout(row)
        no.setFocus()

        self.cal.selectionChanged.connect(self._update_preview)
        self.hour.minus.clicked.connect(self._update_preview)
        self.hour.plus.clicked.connect(self._update_preview)
        self.minute.minus.clicked.connect(self._update_preview)
        self.minute.plus.clicked.connect(self._update_preview)
        self._update_preview()

    def _update_preview(self) -> None:
        dt = self.value()
        text = dt.strftime("%a %d %b · %H:%M")
        if dt <= datetime.now():
            text += "   →  +1 min"          # will be nudged to the future
        self.preview.setText(tr("sched.starts", t=text))

    def value(self) -> datetime:
        d = self.cal.selectedDate()
        return datetime(d.year(), d.month(), d.day(), self.hour.value(),
                        self.minute.value())

    def accepted_value(self) -> datetime:
        """Selected slot, nudged at least one minute into the future."""
        dt = self.value()
        if dt <= datetime.now():
            dt = datetime.now().replace(second=0, microsecond=0) + timedelta(minutes=1)
        return dt


def pick_date_time(parent: QWidget, initial: datetime) -> Optional[datetime]:
    """Open the schedule picker; returns the chosen datetime or None."""
    d = ScheduleDialog(parent, initial)
    return d.accepted_value() if d.exec() == QDialog.DialogCode.Accepted else None
