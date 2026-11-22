"""Command palette (Ctrl+K): search every menu command and run it from the keyboard."""
from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import QFrame, QListWidget, QListWidgetItem, QWidget

from .dialogs import FramelessDialog
from .menus import LineEdit
from .widgets import IconLabel, hbox, hline, kbd, label

ROW_H = 42
VISIBLE_ROWS = 8


def _score(query: str, title: str, group: str) -> int | None:
    """Higher is better; None = no match. Substring beats scattered letters."""
    q = query.lower()
    t = title.lower()
    if q in t:
        return 1000 - t.index(q) * 5 - len(t)
    if q in group.lower():
        return 400
    it = iter(t)                                   # in-order subsequence ("cll" -> "Copy Last Link")
    if all(ch in it for ch in q if not ch.isspace()):
        return 100 - len(t)
    return None


class CommandPalette(FramelessDialog):
    """commands: list of (title, group, shortcut, QAction). The chosen action is in ``chosen``."""

    def __init__(self, parent: QWidget, commands: list) -> None:
        super().__init__(parent, 580, (16, 16, 16, 12))
        self.setWindowTitle("Command palette")
        self.chosen = None
        self._all = commands
        self._shown: list = []

        row = hbox(spacing=10)
        row.setContentsMargins(6, 0, 6, 0)
        row.addWidget(IconLabel("search", 18, "muted"))
        self.edit = LineEdit()
        self.edit.setObjectName("paletteinput")
        self.edit.setPlaceholderText("Type a command…")
        self.edit.setClearButtonEnabled(False)
        self.edit.textChanged.connect(self._filter)
        self.edit.returnPressed.connect(self._run_current)
        row.addWidget(self.edit, 1)
        self.body.addLayout(row)
        self.body.addSpacing(8)
        self.body.addWidget(hline())
        self.body.addSpacing(8)

        self.list = QListWidget()
        self.list.setObjectName("palette")
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setFixedHeight(ROW_H * VISIBLE_ROWS)
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.itemClicked.connect(lambda _i: self._run_current())
        self.body.addWidget(self.list)

        self.empty = label("No matching commands", "muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body.addWidget(self.empty)

        self.body.addSpacing(8)
        self.body.addWidget(hline())
        self.body.addSpacing(6)
        foot = hbox(spacing=4)
        foot.setContentsMargins(6, 0, 6, 0)
        self.count = label("", "caption")
        foot.addWidget(self.count, 1)
        # keycaps with real SVG arrow glyphs — no unicode icons anywhere
        foot.addWidget(kbd(icon_name="arrow-up"))
        foot.addWidget(kbd(icon_name="arrow-down"))
        foot.addWidget(label("navigate", "caption"))
        foot.addSpacing(6)
        foot.addWidget(kbd("Enter"))
        foot.addWidget(label("run", "caption"))
        foot.addSpacing(6)
        foot.addWidget(kbd("Esc"))
        foot.addWidget(label("close", "caption"))
        self.body.addLayout(foot)

        self._filter("")
        self.edit.setFocus()

    # --------------------------------------------------------------- filtering
    def _filter(self, text: str) -> None:
        q = text.strip()
        if not q:
            hits = list(self._all)
        else:
            scored = [(s, i, c) for i, c in enumerate(self._all)
                      if (s := _score(q, c[0], c[1])) is not None]
            scored.sort(key=lambda t: (-t[0], t[1]))
            hits = [c for _s, _i, c in scored]
        self._shown = hits
        self.list.clear()
        for title, group, shortcut, _act in hits:
            item = QListWidgetItem()
            item.setSizeHint(QSize(1, ROW_H))
            self.list.addItem(item)
            self.list.setItemWidget(item, self._row(title, group, shortcut))
        if hits:
            self.list.setCurrentRow(0)
        self.list.setVisible(bool(hits))
        self.empty.setVisible(not hits)
        self.empty.setMinimumHeight(ROW_H * 3)
        n = len(hits)
        self.count.setText(f"{n} command{'s' if n != 1 else ''}")

    def _row(self, title: str, group: str, shortcut: str) -> QWidget:
        w = QFrame()
        w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        r = hbox(w, (14, 0, 22, 0), 10)          # right gap keeps pills clear of the scrollbar
        r.addWidget(label(title), 1)
        g = label(group, "caption")
        g.setFixedWidth(48)                      # fixed columns: every row lines up
        r.addWidget(g, 0, Qt.AlignmentFlag.AlignVCenter)
        keys = QWidget()
        keys.setFixedWidth(138)
        kl = hbox(keys, spacing=5)
        kl.addStretch(1)                         # right-aligned
        for k in (shortcut.split("+") if shortcut else []):
            kl.addWidget(kbd(k), 0, Qt.AlignmentFlag.AlignVCenter)
        r.addWidget(keys, 0, Qt.AlignmentFlag.AlignVCenter)
        return w

    # -------------------------------------------------------------------- keys
    def _move(self, delta: int) -> None:
        n = self.list.count()
        if n:
            self.list.setCurrentRow(max(0, min(n - 1, self.list.currentRow() + delta)))

    def keyPressEvent(self, e) -> None:  # noqa: N802
        k = e.key()
        if k == Qt.Key.Key_Down:
            self._move(1)
        elif k == Qt.Key.Key_Up:
            self._move(-1)
        elif k == Qt.Key.Key_PageDown:
            self._move(VISIBLE_ROWS - 1)
        elif k == Qt.Key.Key_PageUp:
            self._move(-(VISIBLE_ROWS - 1))
        else:
            super().keyPressEvent(e)

    def _run_current(self) -> None:
        i = self.list.currentRow()
        if 0 <= i < len(self._shown):
            self.chosen = self._shown[i][3]
            self.accept()
