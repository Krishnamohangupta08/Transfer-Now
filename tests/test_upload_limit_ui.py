"""Upload page: 20-item limit, smart ZIP, backend enforcement."""
from __future__ import annotations

import os
import shutil
import struct
import sys
import tempfile
import time
import unittest
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import limits, ziputil  # noqa: E402
from core.engine import EngineError, EngineOptions, TransferEngine  # noqa: E402
from tests.test_ui import Base, StallMonitor, wait_until  # noqa: E402

PE = b"MZ" + struct.pack("<H", 0x90) + struct.pack("<H", 3) + b"\x00\x00" + struct.pack("<H", 4) \
    + b"\x00" * 50 + struct.pack("<I", 0x80) + b"\x00" * 300


def _dirs() -> set:
    try:
        return set(os.listdir(ziputil.archive_root()))
    except OSError:
        return set()


class UploadSecurityBase(Base):
    def setUp(self):
        super().setUp()
        self.w.navigate("upload")
        self.up = self.w.pages["upload"]
        self.up.start = lambda: None                 # never launch a real transfer here
        self.calls: list = []
        self.answer = "add"

        def confirm(paths, plan):
            self.calls.append((list(paths), plan))
            return self.answer
        self.up._confirm_overflow = confirm
        self.dirs0 = _dirs()
        self.addCleanup(self._cleanup_archives)

    def _cleanup_archives(self):
        for n in _dirs() - self.dirs0:
            shutil.rmtree(os.path.join(ziputil.archive_root(), n), ignore_errors=True)

    def make(self, n, ext=".txt", prefix="f", data=b"hello", sub=""):
        d = os.path.join(self.tmp, sub) if sub else self.tmp
        os.makedirs(d, exist_ok=True)
        out = []
        for i in range(n):
            p = os.path.join(d, f"{prefix}{i:03d}{ext}")
            with open(p, "wb") as f:
                f.write(data + str(i).encode())
            out.append(p)
        return out

    def add(self, paths, timeout=20):
        self.up.add_paths(paths)
        self.assertTrue(wait_until(lambda: not self.up._scans and self.up._zip is None, timeout))


class TestLimitUi(UploadSecurityBase):
    def test_counter_and_remaining_slots(self):
        self.add(self.make(12))
        self.assertEqual(len(self.up.files), 12)
        self.assertIn("Files selected: 12 / 20", self.up.limit_label.text())
        self.assertIn("8 slots left", self.up.limit_label.text())
        self.assertEqual(self.calls, [])                       # no prompt when nothing is wrong

    def test_exactly_20_accepted_and_ui_locks(self):
        self.add(self.make(20))
        self.assertEqual(len(self.up.files), 20)
        self.assertEqual(self.calls, [])
        self.assertFalse(self.up.drop.isEnabled())             # adding more is disabled...
        self.assertIn("limit reached", self.up.limit_label.text().lower())   # ...and explained
        self.assertTrue(self.up.drop.toolTip())

    def test_backend_refuses_more_than_20_even_when_ui_is_bypassed(self):
        self.add(self.make(20))
        extra = self.make(5, prefix="x")
        self.up._commit([os.path.abspath(p) for p in extra],
                        {"sizes": {}, "flag_set": set(), "skipped": {"unreadable": 0, "empty": 0, "big": 0},
                         "source": ""})
        self.assertEqual(len(self.up.files), 20)
        self.add(extra)
        self.assertEqual(len(self.up.files), 20)
        self.assertEqual(len(self.up._sizes), 20)

    def test_remove_recalculates_and_reenables(self):
        paths = self.make(20)
        self.add(paths)
        self.up.remove_file(os.path.abspath(paths[0]))
        self.assertEqual(len(self.up.files), 19)
        self.assertIn("Files selected: 19 / 20", self.up.limit_label.text())
        self.assertTrue(self.up.drop.isEnabled())
        self.assertEqual(self.up.sum_files.text(), "19 / 20")

    def test_duplicates_rejected(self):
        paths = self.make(5)
        self.add(paths)
        self.add(paths + paths)
        self.assertEqual(len(self.up.files), 5)

    def test_start_refuses_over_limit_selection(self):
        from ui.pages.upload import UploadPage
        up = self.up
        up.start = UploadPage.start.__get__(up)
        up.files = [f"/x/{i}" for i in range(21)]               # state forced past the limit
        up._sizes = {p: 1 for p in up.files}
        up.start()
        self.assertEqual(up.jobs, [])


class TestSmartZip(UploadSecurityBase):
    def test_21_files_become_one_zip_item(self):
        paths = self.make(21)
        self.answer = "zip"
        self.add(paths)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.calls[0][1].overflow), 1)
        self.assertEqual(len(self.up.files), 1)
        arch = self.up.files[0]
        self.assertEqual(os.path.basename(arch), "TransferNow_Archive.zip")
        with zipfile.ZipFile(arch) as z:
            self.assertEqual(sorted(z.namelist()), sorted(os.path.basename(p) for p in paths))
            for p in paths:
                with open(p, "rb") as f:
                    self.assertEqual(z.read(os.path.basename(p)), f.read())
        self.assertIn("Files selected: 1 / 20", self.up.limit_label.text())
        for p in paths:                                         # originals untouched
            self.assertTrue(os.path.exists(p))

    def test_zip_then_upload_calls_start(self):
        started = []
        self.up.start = lambda: started.append(1)
        self.answer = "zip"
        self.add(self.make(25))
        self.assertEqual(started, [1])

    def test_existing_queue_plus_archive_never_exceeds_limit(self):
        self.add(self.make(19, prefix="e"))
        self.answer = "zip"
        self.add(self.make(5, prefix="n", sub="more"))
        self.assertEqual(len(self.up.files), 20)                # 19 + 1 archive
        self.assertEqual(sum(1 for f in self.up.files if f.endswith(".zip")), 1)

    def test_full_queue_cannot_take_an_archive(self):
        self.add(self.make(20, prefix="e"))
        self.answer = "zip"
        self.add(self.make(30, prefix="n", sub="more"))
        self.assertEqual(len(self.up.files), 20)
        self.assertFalse([f for f in self.up.files if f.endswith(".zip")])

    def test_user_can_cancel_before_archive_creation(self):
        self.answer = "cancel"
        self.add(self.make(30))
        self.assertEqual(self.up.files, [])
        self.assertEqual(_dirs() - self.dirs0, set())           # nothing was built

    def test_removing_the_archive_deletes_it(self):
        self.answer = "zip"
        self.add(self.make(22))
        arch = self.up.files[0]
        self.assertTrue(os.path.exists(arch))
        self.up.remove_file(arch)
        self.assertFalse(os.path.exists(arch))

    def test_cancel_during_packaging_cleans_up(self):
        old = ziputil.CHUNK
        ziputil.CHUNK = 1024
        self.addCleanup(setattr, ziputil, "CHUNK", old)
        paths = self.make(30, data=os.urandom(1) * 300_000)
        self.answer = "zip"
        self.up.add_paths(paths)
        self.assertTrue(wait_until(lambda: self.up._zip is not None, 20))
        self.up._cancel_zip()
        self.assertTrue(wait_until(lambda: self.up._zip is None, 20))
        self.assertEqual(self.up.files, [])
        self.assertEqual(_dirs() - self.dirs0, set())
        self.assertFalse(self.up.pack_row.isVisible())

    def test_ui_thread_stays_responsive_while_packaging(self):
        paths = self.make(300, data=b"")
        for p in paths:                               # realistic (incompressible) content
            with open(p, "wb") as f:
                f.write(os.urandom(150_000))
        self.answer = "zip"
        mon = StallMonitor()
        mon.start()
        self.add(paths, timeout=60)
        mon.stop()
        self.assertEqual(len(self.up.files), 1)
        self.assertLess(mon.max_gap, 1.0, f"UI stalled for {mon.max_gap:.2f}s")


class TestEngineBackend(unittest.TestCase):
    """The engine enforces the item limit, so a direct worker call cannot bypass the UI."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tn-eng-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def mk(self, name, data=b"hello"):
        p = os.path.join(self.tmp, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def validate(self, files):
        TransferEngine(EngineOptions(files=files), lambda *_: None)._validate()

    def test_21_items_rejected(self):
        with self.assertRaises(EngineError) as cm:
            self.validate([self.mk(f"f{i}.txt") for i in range(21)])
        self.assertIn("at most 20", str(cm.exception))

    def test_20_items_accepted(self):
        self.validate([self.mk(f"f{i}.txt") for i in range(20)])

    def test_duplicates_rejected(self):
        p = self.mk("a.txt")
        with self.assertRaises(EngineError):
            self.validate([p, p])

    def test_limit_is_configurable(self):
        from unittest import mock
        with mock.patch.object(limits, "MAX_FILES_PER_TRANSFER", 3):
            self.assertEqual(limits.MAX_FILES_PER_TRANSFER, 3)
            with self.assertRaises(ValueError):
                limits.check_item_count(["/a", "/b", "/c", "/d"], limits.MAX_FILES_PER_TRANSFER)


if __name__ == "__main__":
    unittest.main()
