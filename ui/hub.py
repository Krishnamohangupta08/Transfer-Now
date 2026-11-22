"""Tiny event hub that lets the Queue page observe the Upload page's jobs.

The Upload page owns the real job list (it drives the worker threads);
the hub simply mirrors "something changed" notifications so the dedicated
Queue page can render and control the very same transfers.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal


class TransferHub(QObject):
    jobs_changed = pyqtSignal()      # a job was added / removed / reordered
    jobs_updated = pyqtSignal()      # progress or state refresh (may be frequent)

    def __init__(self) -> None:
        super().__init__()
        self.page: Optional[object] = None   # UploadPage, once it is built

    def attach(self, page) -> None:
        self.page = page
        self.jobs_changed.emit()

    def detach(self, page) -> None:
        if self.page is page:
            self.page = None
        self.jobs_changed.emit()

    @property
    def jobs(self) -> list:
        return list(self.page.jobs) if self.page is not None else []


HUB = TransferHub()
