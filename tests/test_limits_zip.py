"""20-item limit and smart ZIP packaging (Qt-free)."""
from __future__ import annotations

import errno
import gzip
import io
import os
import shutil
import struct
import sys
import tarfile
import tempfile
import threading
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import limits, ziputil  # noqa: E402

# a minimal but structurally valid DOS/PE header
PE = b"MZ" + struct.pack("<H", 0x90) + struct.pack("<H", 3) + b"\x00\x00" + struct.pack("<H", 4) + b"\x00" * 50 \
    + struct.pack("<I", 0x80) + b"\x00" * 64 + b"PE\x00\x00" + b"\x00" * 200
ELF = b"\x7fELF\x02\x01\x01" + b"\x00" * 100
MACHO = struct.pack("<I", 0xFEEDFACF) + b"\x00" * 100
FAT = b"\xca\xfe\xba\xbe" + struct.pack(">I", 2) + b"\x00" * 100
JCLASS = b"\xca\xfe\xba\xbe" + struct.pack(">HH", 0, 52) + b"\x00" * 100
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
PDF = b"%PDF-1.4\n" + b"x" * 100


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tn-sec-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def w(self, name, data=b"hello", sub=""):
        d = os.path.join(self.tmp, sub) if sub else self.tmp
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    @staticmethod
    def w_raw(p, data):
        with open(p, "wb") as f:
            f.write(data)

    @staticmethod
    def rd(p):
        with open(p, "rb") as f:
            return f.read()

    def zip_of(self, entries, name="a.zip", **kw):
        p = os.path.join(self.tmp, name)
        with zipfile.ZipFile(p, "w", **kw) as z:
            for n, d in entries.items():
                z.writestr(n, d)
        return p


class TestLimits(Base):
    def test_default_is_twenty(self):
        self.assertEqual(limits.MAX_FILES_PER_TRANSFER, 20)

    def test_boundaries(self):
        for n, zipneeded in ((1, False), (19, False), (20, False), (21, True), (100, True)):
            plan = limits.plan_admission([], [f"/x/{i}" for i in range(n)])
            self.assertEqual(plan.needs_zip, zipneeded, n)
            self.assertEqual(len(plan.accepted), min(n, 20))

    def test_existing_plus_new_and_duplicates(self):
        existing = [f"/x/{i}" for i in range(15)]
        plan = limits.plan_admission(existing, ["/x/3", "/x/new1", "/x/new1"] + [f"/y/{i}" for i in range(10)])
        self.assertEqual(plan.free_slots, 5)
        self.assertEqual(len(plan.accepted), 5)
        self.assertEqual(len(plan.duplicates), 2)
        self.assertEqual(len(plan.overflow), 6)

    def test_backend_guard(self):
        limits.check_item_count([f"/x/{i}" for i in range(20)])
        with self.assertRaises(ValueError):
            limits.check_item_count([f"/x/{i}" for i in range(21)])
        with self.assertRaises(ValueError):
            limits.check_item_count(["/x/a", "/x/a"])

    def test_concurrent_admission_never_exceeds(self):
        """Threads admitting under one lock (the UploadPage pattern) cannot overshoot."""
        lock, held = threading.Lock(), []

        def worker(k):
            for j in range(10):
                with lock:
                    plan = limits.plan_admission(held, [f"/t{k}/{j}-{i}" for i in range(7)])
                    held.extend(plan.accepted)
        ts = [threading.Thread(target=worker, args=(k,)) for k in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(len(held), 20)


class TestZip(Base):
    def build(self, paths, **kw):
        return ziputil.build_archive(paths, dest_dir=os.path.join(self.tmp, "_out"), **kw)

    def test_35_files_become_one_zip_with_exact_contents(self):
        src = []
        for i in range(35):
            src.append(self.w(f"doc{i}.pdf" if i % 2 else f"img{i}.png", (PDF if i % 2 else PNG) + bytes([i]) * 50))
        res = self.build(src)
        self.assertEqual(os.path.basename(res.path), "TransferNow_Archive.zip")
        with zipfile.ZipFile(res.path) as z:
            self.assertEqual(len(z.namelist()), 35)
            self.assertIsNone(z.testzip())
            for p in src:
                self.assertEqual(z.read(os.path.basename(p)), self.rd(p))
        # originals untouched
        self.assertEqual(self.rd(src[0]), PNG + bytes([0]) * 50)

    def test_structure_unicode_and_collisions(self):
        a = self.w("résumé—日本.txt", b"1", sub="A/sub")
        b = self.w("same.txt", b"2", sub="A")
        c = self.w("same.txt", b"3", sub="B")
        res = self.build([a, b, c])
        self.assertIn("A/sub/résumé—日本.txt", res.entries)
        self.assertEqual(len(set(n.casefold() for n in res.entries)), 3)
        names = zipfile.ZipFile(res.path).namelist()
        self.assertIn("A/same.txt", names)
        self.assertIn("B/same.txt", names)

    def test_basename_collision_same_folder_impossible_but_planned(self):
        names = ziputil.plan_names(["/a/x.txt", "/b/x.txt"])
        self.assertEqual(names, ["a/x.txt", "b/x.txt"])
        names = ziputil.plan_names(["/a/x.txt"] * 2)
        self.assertEqual(len(set(names)), 2)

    def test_missing_source(self):
        with self.assertRaises(ziputil.ZipError):
            self.build([self.w("a.txt"), os.path.join(self.tmp, "gone.txt")])

    def test_source_disappears_during_packaging(self):
        a, b = self.w("a.txt", b"x" * 10), self.w("b.txt", b"y" * 10)
        real = open

        def flaky(path, *a, **k):
            if path == b:
                os.remove(b)
            return real(path, *a, **k)
        with mock.patch("builtins.open", flaky):
            with self.assertRaises(ziputil.ZipError):
                self.build([a, b])
        self.assertEqual(os.listdir(os.path.join(self.tmp, "_out")), [])      # temp cleaned

    def test_cancel_cleans_up(self):
        srcs = [self.w(f"f{i}.txt", b"x" * 1000) for i in range(5)]
        n = {"c": 0}

        def stop():
            n["c"] += 1
            return n["c"] > 3
        with self.assertRaises(ziputil.ZipCancelled):
            self.build(srcs, should_stop=stop)
        self.assertEqual(os.listdir(os.path.join(self.tmp, "_out")), [])

    def test_disk_full(self):
        srcs = [self.w("a.txt", b"x" * 1000)]
        with mock.patch("shutil.disk_usage", return_value=shutil._ntuple_diskusage(10, 10, 0)):
            with self.assertRaises(ziputil.ZipError) as cm:
                self.build(srcs)
        self.assertIn("disk space", str(cm.exception))
        real_open = zipfile.ZipFile.open

        def enospc(self_, *a, **k):
            raise OSError(errno.ENOSPC, "No space left")
        with mock.patch.object(zipfile.ZipFile, "open", enospc):
            with self.assertRaises(ziputil.ZipError) as cm:
                self.build(srcs)
        self.assertIn("disk space", str(cm.exception))
        self.assertEqual(os.listdir(os.path.join(self.tmp, "_out")), [])

    def test_progress_reported(self):
        seen = []
        self.build([self.w("a.txt", b"x" * 5000)], progress=lambda d, t, n: seen.append((d, t)))
        self.assertEqual(seen[-1][0], 5000)

    def test_archive_cannot_contain_itself(self):
        out = os.path.join(self.tmp, "_out")
        os.makedirs(out)
        res = ziputil.build_archive([self.w("a.txt")], dest_dir=out)
        res2 = ziputil.build_archive([res.path, self.w("b.txt")], dest_dir=out)
        self.assertEqual(len(res2.entries), 2)             # a different output file; never itself

    def test_cleanup_only_touches_managed_archives(self):
        outside = self.w("keep.zip", b"PK")
        ziputil.cleanup_archive(outside)
        self.assertTrue(os.path.exists(outside))
        res = ziputil.build_archive([self.w("a.txt")])
        self.assertTrue(ziputil.is_managed_archive(res.path))
        ziputil.cleanup_archive(res.path)
        self.assertFalse(os.path.exists(res.path))




if __name__ == "__main__":
    unittest.main()
