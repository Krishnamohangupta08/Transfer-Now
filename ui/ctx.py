"""Shared application context handed to every page."""
from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QObject, pyqtSignal

from core.store import History, Settings


class Ctx(QObject):
    history_changed = pyqtSignal()
    activity = pyqtSignal(str, int)          # label ("" = idle), percent (-1 = n/a)

    def __init__(self, settings: Settings, history: History) -> None:
        super().__init__()
        self.settings = settings
        self.history = history
        self.navigate: Callable[[str], None] = lambda _k: None
        self.toast: Callable[[str, str], None] = lambda _t, _k="success": None

    def export_history(self, parent=None) -> None:
        from datetime import datetime
        from PyQt6.QtWidgets import QFileDialog
        from core.store import export_csv
        entries = self.history.entries()
        if not entries:
            self.toast("Nothing to export yet", "info")
            return
        name = f"transfer-history-{datetime.now():%Y%m%d}.csv"
        path, _ = QFileDialog.getSaveFileName(parent, "Export history", name, "CSV (*.csv)")
        if not path:
            return
        try:
            n = export_csv(entries, path)
            self.toast(f"Exported {n} records", "success")
        except OSError as exc:
            self.toast(f"Export failed: {exc.strerror or exc}", "error")

    def add_history(self, entry: dict) -> dict:
        e = self.history.add(entry)
        self.history_changed.emit()
        return e

    def remove_history(self, ids) -> None:
        self.history.remove_many(ids)
        self.history_changed.emit()

    def clear_history(self) -> None:
        self.history.clear()
        self.history_changed.emit()
