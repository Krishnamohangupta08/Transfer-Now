"""Regression tests for the 1.0.x repair batch (UI/workflow/performance agent):

- big batch adds stay fast and keep exact totals (no per-file O(n²) total)
- whole-folder scans screen sensitive names via core.filesafety, with a
  non-blocking prompt for flagged files and a counted skip list
- the download page never acts on a stale preview (race, invalid text,
  cleared field, mismatched Download click)
- folder-scan threads report progress and are joined/cleared on shutdown
- untrusted strings render as plain text everywhere (filenames, titles, errors)

Offline like tests.test_ui: fetch threads are stubbed by patching
``ui.pages.download.FetchThread`` (the page's own module attribute), and no
upload thread is ever started.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import ClassVar

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import sip
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import QLabel, QPushButton

import ui.pages.download as download_mod
import ui.pages.upload as upload_mod
from core import tasks as tasks_mod
from core.store import human_size
from tests.test_ui import Base, wait_until
from ui.pages.download import S_EMPTY, S_INFO, S_LOADING
from ui.widgets import ElidedLabel

DL = "https://www.transfernow.net/dl/"


class FakeFetch(QThread):
    """Stub for ui.pages.download.FetchThread: scripted responses, real threads.

    ``script`` maps link -> (kind, payload, delay_s) with kind "info"/"failed";
    a missing link fails immediately, so an accidental network fetch is caught.
    """
    info = pyqtSignal(int, dict)
    failed = pyqtSignal(int, str)

    script: ClassVar[dict] = {}
    made: ClassVar[list] = []

    def __init__(self, token: int, link: str, parent=None) -> None:
        super().__init__(parent)
        self.token, self.link = token, link
        FakeFetch.made.append(self)

    def run(self) -> None:
        kind, payload, delay = FakeFetch.script.get(self.link, ("failed", "not found", 0.0))
        if delay:
            time.sleep(delay)
        if kind == "info":
            self.info.emit(self.token, payload)
        else:
            self.failed.emit(self.token, payload)


class _StubbedDownload(Base):
    """Base fixture with the download page's FetchThread replaced by FakeFetch."""

    def setUp(self):
        super().setUp()
        self._orig_fetch = download_mod.FetchThread
        download_mod.FetchThread = FakeFetch
        FakeFetch.made = []
        self.addCleanup(setattr, download_mod, "FetchThread", self._orig_fetch)
        self.dp = self.w.page("download")
        self.w.navigate("download")

    def tearDown(self):
        for th in list(FakeFetch.made):        # never destroy a running QThread
            try:
                if not sip.isdeleted(th):      # _fetch_done may already have cleaned it
                    th.wait(3000)
            except RuntimeError:
                pass
        super().tearDown()

    @staticmethod
    def info(tid: str, title: str = "Files are ready") -> dict:
        return {"transfer_id": tid, "link": DL + tid, "title": title, "ready": True,
                "files": [{"name": "a.bin", "size": 3}], "total_bytes": 3,
                "expiry": "Nov 09, 2026"}

    def _page(self):
        self.w.navigate("upload")
        return self.w.pages["upload"]


# ----------------------------------------------------------------- task 1
class TestBatchPerformance(_StubbedDownload):
    """Adding big batches is O(n), exact, and stays correct after remove/clear."""

    def _make_files(self, n: int, prefix: str = "tn-batch-") -> list:
        root = tempfile.mkdtemp(prefix=prefix)
        paths = []
        for i in range(n):
            p = os.path.join(root, f"f{i:05d}.bin")
            with open(p, "wb") as fh:
                fh.write(b"x" * (1 + i % 17))
            paths.append(p)
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        return paths

    def test_add_1000_files_under_2s(self):
        """1000 files are scanned off the UI thread and (limit = 20) offered as one ZIP."""
        up = self._page()
        up.start = lambda: None                                   # don't launch a real transfer
        up._confirm_overflow = lambda paths, plan: "zip"
        paths = self._make_files(1000)
        t0 = time.monotonic()
        up.add_paths(paths)
        self.assertLess(time.monotonic() - t0, 2.0, "add_paths must return quickly (scan is async)")
        self.assertTrue(wait_until(lambda: len(up.files) == 1 and up._zip is None, 30))
        archive = up.files[0]
        import zipfile
        with zipfile.ZipFile(archive) as z:
            self.assertEqual(len(z.namelist()), 1000)
        self.assertEqual(up.sum_files.text(), "1 / 20")
        self.assertEqual(up._sizes[archive], os.path.getsize(archive))
        self.assertEqual(sum(up.capacity._sizes), os.path.getsize(archive))
        self.assertTrue(up.go.isEnabled())
        up.clear_files()                                          # removes the generated ZIP
        self.assertFalse(os.path.exists(archive))

    def test_duplicates_not_re_added(self):
        up = self._page()
        paths = self._make_files(15)
        up.add_paths(paths)
        up.add_paths(paths + paths[:5])                           # all duplicates
        self.assertEqual(len(up.files), 15)
        self.assertEqual(len(up._sizes), 15)

    def test_total_updates_after_remove_and_clear(self):
        up = self._page()
        root = tempfile.mkdtemp(prefix="tn-rem-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        paths = []
        for i, n in enumerate((10, 20, 30)):
            p = os.path.join(root, f"r{i}.bin")
            Path(p).write_bytes(b"x" * n)
            paths.append(os.path.abspath(p))
        up.add_paths(paths)
        self.assertEqual(sum(up._sizes.values()), 60)
        self.assertEqual(up.sum_files.text(), "3 / 20")
        up.remove_file(paths[1])
        self.assertEqual(sum(up._sizes.values()), 40)
        self.assertEqual(len(up.files), 2)
        self.assertEqual(up.sum_files.text(), "2 / 20")
        self.assertEqual(sum(up.capacity._sizes), 40)
        up.clear_files()
        self.assertEqual(sum(up._sizes.values()), 0)
        self.assertEqual(up.files, [])
        self.assertEqual(up.sum_size.text(), "—")
        self.assertFalse(up.files_card.isVisible())

    def test_huge_drop_is_chunked(self):
        up = self._page()
        up._confirm_overflow = lambda paths, plan: "add"
        paths = self._make_files(2100, prefix="tn-chunk-")
        t0 = time.monotonic()
        up.add_paths(paths)                                       # > 2000: chunked path
        self.assertTrue(wait_until(lambda: True, 20))
        self.assertEqual(len(up.files), 20)                       # the limit holds on huge drops
        self.assertEqual(sum(up._sizes.values()), sum(os.path.getsize(p) for p in up.files))
        self.assertLess(time.monotonic() - t0, 10.0)


# ----------------------------------------------------------------- task 2
class TestFolderScreening(_StubbedDownload):
    """Folder scans route sensitive names to a prompt; explicit picks warn once."""

    def _mixed_folder(self):
        root = tempfile.mkdtemp(prefix="tn-mixed-")
        Path(root, "normal.txt").write_text("ok")
        Path(root, ".hidden").write_text("h")
        Path(root, ".env").write_text("SECRET=1")
        Path(root, "id_rsa").write_text("-----BEGIN")
        Path(root, "Thumbs.db").write_bytes(b"junk")
        outside = tempfile.mkdtemp(prefix="tn-outside-")
        secret = os.path.join(outside, "secret.txt")
        Path(secret).write_text("s")
        os.symlink(secret, os.path.join(root, "escape.lnk"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        return root, secret

    def test_scan_folder_split(self):
        root, secret = self._mixed_folder()
        allowed, flagged, skipped = tasks_mod.scan_folder(root)
        self.assertEqual(allowed, [os.path.join(root, "normal.txt")])
        self.assertEqual(len(flagged), 3)
        joined = " | ".join(flagged)
        for name in (".hidden", ".env", "id_rsa"):
            self.assertIn(os.path.join(root, name), joined)
            self.assertIn(" — ", joined)                          # "path — reason"
        for bad in (secret, "escape", "Thumbs"):
            self.assertNotIn(bad, joined + "".join(allowed))      # SKIP/junk: nowhere
        self.assertEqual(skipped, 2)                              # junk + outside link

    def test_scan_thread_signals(self):
        root, _ = self._mixed_folder()
        progress, done = [], []
        t = tasks_mod.FolderScanThread(root)
        t.progress.connect(lambda f, n: progress.append((f, n)))
        t.done.connect(lambda f, files, flagged, skipped:
                       done.append((f, files, flagged, skipped)))
        t.start()
        self.assertTrue(wait_until(lambda: bool(done), 10))
        folder, files, flagged, skipped = done[0]
        self.assertEqual(folder, root)
        self.assertEqual(len(files), 1)
        self.assertEqual(len(flagged), 3)
        self.assertEqual(skipped, 2)
        self.assertGreater(len(progress), 0)
        self.assertEqual(progress[0][0], root)
        t.wait(3000)

    def test_scan_done_adds_only_allowed_by_default(self):
        up = self._page()
        root, _ = self._mixed_folder()
        up._confirm_flagged = lambda folder, flagged: False       # user keeps them out
        up._scan_done(None, "mixed", root, [os.path.join(root, "normal.txt")],
                      [f"{os.path.join(root, '.env')} — may contain secrets (.env)"], 2)
        self.assertEqual(up.files, [os.path.join(root, "normal.txt")])
        self.assertNotIn(os.path.join(root, ".env"), up._sizes)

    def test_scan_done_honors_add_anyway(self):
        up = self._page()
        root, _unused = self._mixed_folder()
        env = os.path.join(root, ".env")
        up._confirm_flagged = lambda folder, flagged: True        # explicit decision
        up._scan_done(None, "mixed", root, [os.path.join(root, "normal.txt")],
                      [f"{env} — may contain secrets (.env)"], 2)
        self.assertIn(os.path.join(root, "normal.txt"), up._sizes)
        self.assertIn(env, up._sizes)                             # added without re-prompting

    def test_individual_sensitive_add_warns_once(self):
        up = self._page()
        root, _ = self._mixed_folder()
        up.add_paths([os.path.join(root, "id_rsa"), os.path.join(root, "normal.txt")])
        self.assertIn(os.path.join(root, "id_rsa"), up._sizes)    # explicit picks are included
        self.assertIn(os.path.join(root, "normal.txt"), up._sizes)
        cards = [c for c in self.w.toast._cards if not c._closing]
        self.assertTrue(any("look sensitive" in c.text for c in cards))

    def test_symlink_escape_skipped_in_scans(self):
        """An escaping symlink never enters via a folder scan (only explicit
        individual picks may include links — filesafety's own contract)."""
        root, _secret = self._mixed_folder()
        allowed, flagged, _skipped = tasks_mod.scan_folder(root)
        self.assertNotIn(os.path.join(root, "escape.lnk"), allowed)
        self.assertFalse(any("escape" in s for s in flagged))

    def test_unreadable_individual_picks_skipped(self):
        up = self._page()
        up.add_paths([os.path.join(self.tmp, "nope.bin")])        # does not exist
        self.assertEqual(up.files, [])
        cards = [c for c in self.w.toast._cards if not c._closing]
        self.assertTrue(any("couldn't be read" in c.text for c in cards))

    def test_flagged_prompt_is_nonblocking_and_can_add(self):
        up = self._page()
        root, _ = self._mixed_folder()
        key = os.path.join(root, "id_rsa")
        choice = up._confirm_flagged("mixed", [f"{key} — looks like a private key"])
        self.assertFalse(choice)                                  # returns immediately
        d = getattr(up, "_flag_dialog", None)
        self.assertIsNotNone(d)
        self.assertTrue(d.isVisible())
        labels = " | ".join(l.text() for l in d.findChildren(QLabel))
        self.assertIn("Skipped 1 sensitive file from mixed", labels)
        self.assertIn(key, labels)
        add_btn = next(b for b in d.findChildren(QPushButton) if b.text() == "Add them anyway")
        add_btn.click()
        self.assertIn(key, up._sizes)
        d.close()


# ----------------------------------------------------------------- task 4
class TestScanLifecycle(_StubbedDownload):
    """Scan threads report progress and are joined + cleared on shutdown."""

    def _make_many(self, n: int, prefix: str) -> str:
        root = tempfile.mkdtemp(prefix=prefix)
        for i in range(n):
            Path(root, f"s{i:05d}.bin").write_bytes(b"x" * (1 + i % 5))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        return root

    def test_scan_progress_done_and_speed(self):
        root = self._make_many(3000, "tn-scan-")
        up = self._page()
        made, progress, dones = [], [], []
        orig = upload_mod.FolderScanThread

        class Spy(orig):
            def __init__(self, folder, parent=None):
                super().__init__(folder, parent)
                made.append(self)
                self.progress.connect(lambda f, n: progress.append(n))
                self.done.connect(lambda f, files, flagged, skipped:
                                  dones.append((len(files), len(flagged), skipped)))

        upload_mod.FolderScanThread = Spy
        self.addCleanup(setattr, upload_mod, "FolderScanThread", orig)
        up._confirm_overflow = lambda paths, plan: "add"
        t0 = time.monotonic()
        up.add_paths([root])
        self.assertTrue(wait_until(lambda: not up._scans and len(up.files) == 20, 20))
        self.assertLess(time.monotonic() - t0, 10.0, "3000-file scan+security check must stay fast")
        self.assertGreater(len(progress), 0, "progress signal never fired")
        self.assertEqual(dones, [(3000, 0, 0)])
        self.assertEqual(len(up.files), 20)
        self.assertTrue(up.go.isEnabled())
        up.shutdown()
        self.assertEqual(up._scans, [])

        def alive(t):
            try:
                return t.isRunning()
            except RuntimeError:                  # already deleted == finished
                return False
        self.assertTrue(all(not alive(t) for t in made))

    def test_shutdown_joins_running_scan(self):
        root = self._make_many(3000, "tn-shut-")
        up = self._page()
        up.add_paths([root])                 # scan starts in the background
        running = list(up._scans)
        self.assertTrue(running)
        t0 = time.monotonic()
        up.shutdown()
        self.assertLess(time.monotonic() - t0, 16.0)
        self.assertEqual(up._scans, [])
        self.assertTrue(all(not t.isRunning() for t in running))


# ----------------------------------------------------------------- task 3
class TestDownloadStalePreview(_StubbedDownload):
    """A download page never mixes an old fetch's result into a new link."""

    def test_slow_A_after_fast_B_ignored(self):
        a, b = DL + "aaaaaa", DL + "bbbbbb"
        FakeFetch.script = {a: ("info", self.info("aaaaaa", "A title"), 0.8),
                            b: ("info", self.info("bbbbbb", "B title"), 0.05)}
        self.dp.prefill(a)
        self.dp.prefill(b)
        self.assertTrue(wait_until(lambda: self.dp._state == S_INFO
                                   and self.dp._info
                                   and self.dp._info.get("transfer_id") == "bbbbbb", 5))
        wait_until(lambda: False, 1.0)         # let A's late response land
        self.assertEqual(self.dp._info.get("transfer_id"), "bbbbbb")
        self.assertEqual(self.dp.info_title.text(), "B title")

    def test_invalid_text_clears_info(self):
        FakeFetch.script = {DL + "cccccc": ("info", self.info("cccccc"), 0.0)}
        self.dp.prefill(DL + "cccccc")
        self.assertTrue(wait_until(lambda: self.dp._state == S_INFO, 5))
        self.dp.link.setText("total garbage")
        self.assertEqual(self.dp._state, S_EMPTY)
        self.assertIsNone(self.dp._info)
        self.dp.start()                        # must not start or download anything
        self.assertIsNone(self.dp._dl)

    def test_cleared_during_fetch_ignores_late_response(self):
        FakeFetch.script = {DL + "dddddd": ("info", self.info("dddddd"), 0.5)}
        self.dp.prefill(DL + "dddddd")
        self.assertTrue(wait_until(lambda: self.dp._state == S_LOADING, 2))
        self.dp.link.setText("")
        self.assertEqual(self.dp._state, S_EMPTY)
        self.assertIsNone(self.dp._info)
        wait_until(lambda: False, 1.0)         # the response arrives late
        self.assertIsNone(self.dp._info)
        self.assertEqual(self.dp._state, S_EMPTY)

    def test_text_change_while_info_leaves_state_at_once(self):
        FakeFetch.script = {DL + "eeeeee": ("info", self.info("eeeeee"), 0.0),
                            DL + "ffffff": ("info", self.info("ffffff", "F title"), 0.2)}
        self.dp.prefill(DL + "eeeeee")
        self.assertTrue(wait_until(lambda: self.dp._state == S_INFO, 5))
        self.dp.link.setText(DL + "ffffff")
        self.assertEqual(self.dp._state, S_EMPTY)      # immediately out of S_INFO
        self.assertIsNone(self.dp._info)
        self.assertTrue(wait_until(lambda: self.dp._state == S_INFO
                                   and self.dp._info.get("transfer_id") == "ffffff", 5))
        self.assertEqual(self.dp.info_title.text(), "F title")

    def test_start_with_mismatched_text_refetches(self):
        FakeFetch.script = {DL + "aaaa11": ("info", self.info("aaaa11"), 0.0)}
        self.dp.prefill(DL + "aaaa11")
        self.assertTrue(wait_until(lambda: self.dp._state == S_INFO, 5))
        self.dp.link.setText(DL + "bbbb22")    # field now shows another link
        self.dp._info = self.info("aaaa11")    # …but the stale card pretends otherwise
        token_before = self.dp._token
        self.dp.start()
        self.assertEqual(self.dp._state, S_LOADING)    # refetch, not a download
        self.assertIsNone(self.dp._dl)
        self.assertEqual(self.dp._token, token_before + 1)
        self.assertTrue(wait_until(lambda: self.dp._state == S_EMPTY, 5))  # script misses

    def test_rapid_paste_edit_cycle_ends_on_last_url(self):
        ids = ("g1aaaa", "g2bbbb", "g3cccc")
        FakeFetch.script = {
            DL + ids[0]: ("info", self.info(ids[0], "One"), 0.30),
            DL + ids[1]: ("info", self.info(ids[1], "Two"), 0.15),
            DL + ids[2]: ("info", self.info(ids[2], "Three"), 0.0),
        }
        for tid in ids:
            self.dp.prefill(DL + tid)
        self.assertTrue(wait_until(lambda: self.dp._state == S_INFO
                                   and self.dp._info
                                   and self.dp._info.get("transfer_id") == "g3cccc", 5))
        wait_until(lambda: False, 0.6)         # older responses land afterwards
        self.assertEqual(self.dp._info.get("transfer_id"), "g3cccc")
        self.assertEqual(self.dp.info_title.text(), "Three")


# ------------------------------------------------------------- tasks 5 + 6
class TestPlainTextRendering(_StubbedDownload):
    """Untrusted strings render as plain text, never as rich text."""

    def test_elided_label_is_plain(self):
        hostile = "<b>bold</b> <img src=x onerror=1>"
        lb = ElidedLabel(hostile)
        self.addCleanup(lb.deleteLater)
        self.assertEqual(lb.textFormat(), Qt.TextFormat.PlainText)
        self.assertEqual(lb.full_text(), hostile)
        lb.set_full_text(hostile)
        self.assertEqual(lb.textFormat(), Qt.TextFormat.PlainText)

    def test_file_row_renders_hostile_names(self):
        names = [
            "report<script>alert(1)</script>.pdf",
            "<img src=x onerror=1>.png",
            "報告 🚀 焰 — meeting notes.txt",
            "الملفات עברית.txt",
            "x" * 250 + ".pdf",
            "line\nbreak.txt",
        ]
        for nm in names:
            row = upload_mod.FileRow(os.path.join(tempfile.gettempdir(), nm), 4096)
            self.addCleanup(row.deleteLater)
            self.assertEqual(row._path, os.path.abspath(os.path.join(tempfile.gettempdir(), nm)))
            for lb in row.findChildren(QLabel):
                self.assertEqual(lb.textFormat(), Qt.TextFormat.PlainText, f"label of {nm[:20]}")

    def test_job_card_labels_plain(self):
        up = self._page()
        path = os.path.join(self.tmp, "weird<script>alert(1)</script>.pdf")
        job = upload_mod.UploadJob([path], {path: 123}, "me@example.com")
        job.state, job.started = "running", time.monotonic()   # never starts a thread
        up._add_job(job)
        card = up.cards[job.id]
        for lb in (card.title, card.sub, card.step):
            self.assertEqual(lb.textFormat(), Qt.TextFormat.PlainText)
        up._remove_job(job)

    def test_download_info_renders_hostile_payload(self):
        self.dp.link.setText(DL + "aaa111")    # so the response matches the field
        hostile = {"transfer_id": "aaa111", "link": DL + "aaa111",
                   "title": "<b>evil</b><img src=x onerror=1>", "ready": True,
                   "files": [{"name": "report<script>alert(1)</script>.pdf", "size": 5}],
                   "total_bytes": 5, "expiry": "Nov 09, 2026"}
        self.dp._on_info(self.dp._token, hostile)
        self.assertEqual(self.dp._state, S_INFO)
        self.assertEqual(self.dp.info_title.text(), "<b>evil</b><img src=x onerror=1>")
        self.assertEqual(self.dp.info_title.textFormat(), Qt.TextFormat.PlainText)
        self.assertEqual(self.dp.err_text.textFormat(), Qt.TextFormat.PlainText)
        for lb in self.dp.info_card.findChildren(ElidedLabel):
            self.assertEqual(lb.textFormat(), Qt.TextFormat.PlainText)


if __name__ == "__main__":
    unittest.main(verbosity=2)
