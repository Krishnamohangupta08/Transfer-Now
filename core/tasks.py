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
import time
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



# Housekeeping files nobody means to share when a whole folder is picked.
JUNK_FILES = {"thumbs.db", "desktop.ini", ".ds_store", "ehthumbs.db"}


# Untrusted-name screening: whole-folder sweeps must not silently ingest
# credentials, keys or hidden files.  The policy lives in ``core/filesafety.py``
# (security patch); a minimal fallback keeps the behaviour sane while absent.
try:
    from .filesafety import screen_batch
except ImportError:  # graceful: falls back until the security patch lands

    def _fb_within(child: str, parent: str) -> bool:
        try:
            return os.path.commonpath([child, parent]) == os.path.normpath(parent)
        except ValueError:
            return False

    def screen_batch(paths: list[str], root: str = "") -> tuple[list[str], list[str]]:
        """Stand-in for ``filesafety.screen_batch`` (same contract): SKIP links
        / unreadable files, FLAG obvious secrets.  ``allowed`` holds every
        non-SKIP path; ``flagged`` reports FLAG entries as
        ``"path — reason"`` strings (em dash)."""
        allowed: list[str] = []
        flagged: list[str] = []
        secret_dirs = {".ssh", ".aws", ".gnupg", ".kube", ".password-store"}
        secret_exts = (".pem", ".key", ".p12", ".pfx", ".kdbx", ".keystore", ".jks")
        for p in paths or []:
            try:
                if not os.path.isfile(p) or not os.access(p, os.R_OK):
                    continue                        # SKIP: never enters a transfer
                if os.path.islink(p):
                    base = os.path.realpath(root) if root else \
                        os.path.dirname(os.path.realpath(p))
                    if not _fb_within(os.path.realpath(p), base):
                        continue                    # SKIP: link escapes the root
                lower = os.path.basename(p).lower()
                parts = os.path.normpath(p).split(os.sep)
                reason = ""
                if lower.startswith("."):
                    reason = "hidden file"
                elif lower.endswith(".env"):
                    reason = "environment / secrets file"
                elif (lower.startswith(("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"))
                        or lower.endswith(secret_exts)):
                    reason = "private key / credential file"
                elif any(part.lower() in secret_dirs for part in parts[:-1]):
                    reason = "credential store folder"
                elif lower in ("thumbs.db", "desktop.ini", ".ds_store", "ehthumbs.db"):
                    reason = "system junk"
                allowed.append(p)                   # FLAG stays addable (explicit decision)
                if reason:
                    flagged.append(f"{p} — {reason}")
            except OSError:
                continue                            # SKIP: unreadable
        return allowed, flagged

def scan_folder(folder: str, stop=lambda: False,
                progress=None) -> tuple[list[str], list[str], int]:
    """Screened contents of ``folder``: ``(allowed, flagged, skipped_count)``.

    Every readable, non-empty file under ``folder`` (recursive, sorted, no
    symlink loops) passes through ``filesafety.screen_batch``: risky files come
    back as ``"path — reason"`` strings (not in ``allowed``), links/junk are
    counted and dropped.  ``progress(files_found_so_far)`` is called at most
    every ~250 ms of work.
    """
    found: list[str] = []
    skipped = 0
    last_report = 0.0
    for root, dirs, names in os.walk(folder, followlinks=False):
        if stop():
            break
        dirs.sort()
        for n in sorted(names):
            if stop():
                break
            if n.lower() in JUNK_FILES:
                skipped += 1
                continue
            p = os.path.join(root, n)
            try:
                if os.path.isfile(p) and os.access(p, os.R_OK) and os.path.getsize(p) > 0:
                    found.append(p)
                else:
                    skipped += 1
            except OSError:
                skipped += 1
                continue
            if progress is not None:
                now = time.monotonic()
                if now - last_report >= 0.25:       # cheap: ≤4 emissions / second
                    last_report = now
                    progress(len(found))
    allowed, flagged = screen_batch(found, folder)
    skipped += len(found) - len(allowed)            # SKIP drops: links, unreadable, ...
    flagged_paths = {s.rsplit(" — ", 1)[0] for s in flagged}
    return [p for p in allowed if p not in flagged_paths], flagged, skipped


class FolderScanThread(QThread):
    """Walk a folder off the UI thread and hand back the files inside it."""
    progress = pyqtSignal(str, int)          # folder, files found so far
    done = pyqtSignal(str, list, list, int)  # folder, files, flagged, skipped count
    failed = pyqtSignal(str, str)            # folder, message

    def __init__(self, folder: str, parent=None) -> None:
        super().__init__(parent)
        self.folder = folder
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        try:
            files, flagged, skipped = scan_folder(
                self.folder, lambda: self._stop,
                lambda n: self.progress.emit(self.folder, n))
            self.done.emit(self.folder, files, flagged, skipped)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(self.folder, f"{type(exc).__name__}: {exc}")


class ZipThread(QThread):
    """Build the smart ZIP archive off the UI thread (progress + cancel)."""
    progress = pyqtSignal(int, int, str)     # bytes done, bytes total, current file name
    done = pyqtSignal(object)                # core.ziputil.ZipResult
    failed = pyqtSignal(str)                 # user-facing message
    cancelled = pyqtSignal()

    def __init__(self, paths: list[str], parent=None) -> None:
        super().__init__(parent)
        self.paths = list(paths)
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        from . import ziputil
        try:
            res = ziputil.build_archive(
                self.paths,
                progress=lambda d, t, n: self.progress.emit(d, t, n),
                should_stop=self._stop.is_set)
            if self._stop.is_set():                       # cancelled right at the end
                ziputil.cleanup_archive(res.path)
                self.cancelled.emit()
            else:
                self.done.emit(res)
        except ziputil.ZipCancelled:
            self.cancelled.emit()
        except ziputil.ZipError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"{type(exc).__name__}: {exc}")


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
