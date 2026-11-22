"""Rounded popup menus + a custom text-field context menu."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import QLineEdit, QMenu, QWidget

from .sounds import sounds


class RoundMenu(QMenu):
    """QMenu with a translucent, frameless window so QSS corners are truly round."""

    def __init__(self, title: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(title, parent)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        sounds.play("menu")


class LineEdit(QLineEdit):
    """QLineEdit with the app's rounded Cut / Copy / Paste menu."""

    def contextMenuEvent(self, e) -> None:  # noqa: N802
        m = RoundMenu(parent=self)
        ro = self.isReadOnly()
        sel = self.hasSelectedText()

        def add(text, slot, enabled=True, key=None):
            a = QAction(text, m)
            if key:
                a.setShortcut(QKeySequence(key))
            a.setEnabled(enabled)
            a.triggered.connect(slot)
            m.addAction(a)
            return a

        if not ro:
            add("Undo", self.undo, self.isUndoAvailable(), QKeySequence.StandardKey.Undo)
            add("Redo", self.redo, self.isRedoAvailable(), QKeySequence.StandardKey.Redo)
            m.addSeparator()
            add("Cut", self.cut, sel, QKeySequence.StandardKey.Cut)
        add("Copy", self.copy, sel, QKeySequence.StandardKey.Copy)
        if not ro:
            add("Paste", self.paste, True, QKeySequence.StandardKey.Paste)
            add("Delete", self.del_, sel)
        m.addSeparator()
        add("Select all", self.selectAll, bool(self.text()), QKeySequence.StandardKey.SelectAll)
        m.exec(e.globalPos())
