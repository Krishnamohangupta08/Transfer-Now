"""QThread workers — every network / browser operation runs off the UI thread.

Each worker is a ``QThread`` subclass that only talks to the UI through
queued signals, and exposes a ``stop()`` that is safe to call from the UI
thread without blocking.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal

from . import downloader
from .engine import EngineOptions, TransferEngine


class UploadThread(QThread):
    step = pyqtSignal(str, str, int)       # key, label, percent
    progress = pyqtSignal(int)
    uploaded = pyqtSignal(int, int)        # real bytes sent so far, total
    log = pyqtSignal(str)
    success = pyqtSignal(dict)
    failed = pyqtSignal(str)               # "cancelled" for user cancel

    def __init__(self, opts: EngineOptions, parent=None) -> None:
        super().__init__(parent)
        self._engine = TransferEngine(opts, self._on_event)

    def _on_event(self, name: str, payload) -> None:
        if name == "step":
            self.step.emit(*payload)
        elif name == "progress":
            self.progress.emit(int(payload))
        elif name == "bytes":
            self.uploaded.emit(int(payload[0]), int(payload[1]))
        elif name == "log":
            self.log.emit(str(payload))
        elif name == "success":
            self.success.emit(payload)
        elif name == "failed":
            self.failed.emit(str(payload))

    def run(self) -> None:
        try:
            self._engine.run()
        except Exception as exc:  # noqa: BLE001 — never let a thread die silently
            self.failed.emit(f"{type(exc).__name__}: {exc}")

    def stop(self) -> None:
        self._engine.request_stop()


class DownloadThread(QThread):
    progress = pyqtSignal(int, int, float)   # done, total, speed B/s
    status = pyqtSignal(str)
    success = pyqtSignal(str)                # saved path
    failed = pyqtSignal(str)                 # "cancelled" for user cancel

    def __init__(self, transfer_id: str, dest_dir: str, parent=None) -> None:
        super().__init__(parent)
        self.transfer_id = transfer_id
        self.dest_dir = dest_dir
        self._stop = threading.Event()
        self._running = threading.Event()
        self._running.set()                  # set = not paused

    def stop(self) -> None:
        self._stop.set()
        self._running.set()                  # release a paused wait

    def set_paused(self, paused: bool) -> None:
        if paused:
            self._running.clear()
        else:
            self._running.set()

    @property
    def paused(self) -> bool:
        return not self._running.is_set()

    def _wait_if_paused(self) -> None:
        while not self._running.wait(0.2):
            if self._stop.is_set():
                raise downloader.DownloadCancelled()

    def run(self) -> None:
        try:
            tid = self.transfer_id
            url = downloader.resolve_download_url(tid)
            path = downloader.download(
                url, self.dest_dir, transfer_id=tid,
                progress=lambda d, t, s: self.progress.emit(d, t, s),
                should_stop=self._stop.is_set,
                wait_if_paused=self._wait_if_paused,
                resolver=lambda: downloader.resolve_download_url(tid),
                status=self.status.emit)
            self.success.emit(path)
        except downloader.DownloadCancelled:
            self.failed.emit("cancelled")
        except downloader.TransferLinkError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class FetchThread(QThread):
    """Look up a share link's metadata."""
    info = pyqtSignal(int, dict)             # token, data
    failed = pyqtSignal(int, str)

    def __init__(self, token: int, link: str, parent=None) -> None:
        super().__init__(parent)
        self.token = token
        self.link = link

    def run(self) -> None:
        try:
            self.info.emit(self.token, downloader.fetch_info(self.link))
        except downloader.TransferLinkError as exc:
            self.failed.emit(self.token, str(exc))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(self.token, f"{type(exc).__name__}: {exc}")


class ZipThread(QThread):
    """Zip a folder into a temp file so it can be uploaded as one file."""
    done = pyqtSignal(str, str)              # folder, zip path
    failed = pyqtSignal(str, str)            # folder, message

    def __init__(self, folder: str, parent=None) -> None:
        super().__init__(parent)
        self.folder = folder
        self.tmp_dir = ""

    def run(self) -> None:
        try:
            self.tmp_dir = tempfile.mkdtemp(prefix="transfernow-zip-")
            name = os.path.basename(os.path.normpath(self.folder)) or "folder"
            base = os.path.join(self.tmp_dir, name)
            path = shutil.make_archive(base, "zip", root_dir=self.folder)
            self.done.emit(self.folder, path)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(self.folder, f"{type(exc).__name__}: {exc}")


# Housekeeping files nobody means to share when a whole folder is picked.
JUNK_FILES = {"thumbs.db", "desktop.ini", ".ds_store", "ehthumbs.db"}


def scan_folder(folder: str, stop=lambda: False) -> list[str]:
    """Every readable, non-empty file under ``folder`` (recursive, sorted, no symlink loops)."""
    found: list[str] = []
    for root, dirs, names in os.walk(folder, followlinks=False):
        if stop():
            break
        dirs.sort()
        for n in sorted(names):
            if n.lower() in JUNK_FILES:
                continue
            p = os.path.join(root, n)
            try:
                if os.path.isfile(p) and os.access(p, os.R_OK) and os.path.getsize(p) > 0:
                    found.append(p)
            except OSError:
                continue
    return found


class FolderScanThread(QThread):
    """Walk a folder off the UI thread and hand back the files inside it."""
    done = pyqtSignal(str, list)             # folder, file paths
    failed = pyqtSignal(str, str)            # folder, message

    def __init__(self, folder: str, parent=None) -> None:
        super().__init__(parent)
        self.folder = folder
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        try:
            self.done.emit(self.folder, scan_folder(self.folder, lambda: self._stop))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(self.folder, f"{type(exc).__name__}: {exc}")


class LinkCheckThread(QThread):
    """Check a batch of share links off the UI thread (5 in parallel).

    Emits ``checked(entry_id, state, valid_until)`` per link, ``progress(done,
    total)`` alongside it, and a final ``done_all(active, expired, unknown)``.
    """
    checked = pyqtSignal(str, str, str)
    progress = pyqtSignal(int, int)
    done_all = pyqtSignal(int, int, int)

    WORKERS = 5

    def __init__(self, pairs: list[tuple[str, str]], parent=None) -> None:
        super().__init__(parent)
        self.pairs = list(pairs)
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from . import linkcheck

        counts = {"active": 0, "expired": 0, "unknown": 0}
        total = len(self.pairs)

        def work(entry_id: str, link: str, date: str = ""):
            if self._stop:
                return entry_id, None
            return entry_id, linkcheck.check_link_detail(link, fresh=linkcheck.is_fresh(date))

        done = 0
        with ThreadPoolExecutor(max_workers=min(self.WORKERS, max(1, total))) as pool:
            futs = [pool.submit(work, *p) for p in self.pairs]
            for fut in as_completed(futs):
                try:
                    entry_id, res = fut.result()
                except Exception:  # noqa: BLE001
                    continue
                if res is None:
                    continue
                done += 1
                state = res["state"] if res["state"] in counts else "unknown"
                counts[state] += 1
                self.checked.emit(entry_id, state, res.get("valid_until", ""))
                self.progress.emit(done, total)
        self.done_all.emit(counts["active"], counts["expired"], counts["unknown"])


def wait_all(threads: list[Optional[QThread]], ms: int = 3000) -> None:
    """Stop-and-join helper for application shutdown."""
    for t in threads:
        if t is not None and t.isRunning():
            if hasattr(t, "stop"):
                t.stop()
    for t in threads:
        if t is not None and t.isRunning():
            t.wait(ms)
