"""Qt-level tests: full upload/download flows through the real UI classes,
with an event-loop stall monitor to prove the UI never freezes.

    TN_CHROMEDRIVER=/path/to/chromedriver python -m unittest tests.test_ui -v
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
DATA = tempfile.mkdtemp(prefix="tn-ui-data-")
os.environ["TN_DATA_DIR"] = DATA
MOCK = "file://" + os.path.join(ROOT, "tests", "mock_site", "index.html")
os.environ["TN_SITE_URL"] = MOCK

from PyQt6.QtCore import QEvent, QEventLoop, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core import downloader  # noqa: E402
from core.store import History, Settings  # noqa: E402
from tests import _deps  # dependency policy: explicit skips only, never silent ones
from tests.test_core import PAYLOAD, Handler, Server  # noqa: E402
from ui.ctx import Ctx  # noqa: E402
from ui.theme import theme  # noqa: E402
from ui.window import AppWindow  # noqa: E402

app = QApplication.instance() or QApplication([])


class StallMonitor:
    """Measures the longest gap between 10 ms timer ticks (UI responsiveness)."""

    def __init__(self) -> None:
        self.max_gap = 0.0
        self._last = time.monotonic()
        self._t = QTimer()
        self._t.setInterval(10)
        self._t.timeout.connect(self._tick)

    def _tick(self) -> None:
        now = time.monotonic()
        self.max_gap = max(self.max_gap, now - self._last)
        self._last = now

    def start(self) -> None:
        self._last = time.monotonic()
        self._t.start()

    def stop(self) -> None:
        self._t.stop()


def wait_until(cond, timeout: float = 60.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
        if cond():
            return True
        time.sleep(0.005)
    return False


def make_window():
    d = tempfile.mkdtemp(prefix="tn-ui-case-")          # isolated per test
    s = Settings(os.path.join(d, "settings.json"))
    s.set("human_pace", False); s.set("auto_copy_link", False)
    ctx = Ctx(s, History(os.path.join(d, "history.json")))
    theme.apply(app, "dark", None)
    w = AppWindow(ctx)
    w.show()
    return w, ctx


class Base(unittest.TestCase):
    def setUp(self):
        self.w, self.ctx = make_window()
        self.tmp = tempfile.mkdtemp()
        self.f = os.path.join(self.tmp, "report.txt")
        open(self.f, "w").write("hello world")
        self.mon = StallMonitor()

    def tearDown(self):
        self.mon.stop()
        self.w.pages.get("upload") and self.w.pages["upload"].shutdown()
        self.w.pages.get("download") and self.w.pages["download"].shutdown()
        app.removeEventFilter(self.w)
        self.w.hide()
        self.w.deleteLater()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestFilesAndOverlay(Base):
    """20-item limit (1.0.0.1), folders expand to their files, drop overlay is fully opaque."""

    def _page(self):
        self.w.navigate("upload")
        return self.w.pages["upload"]

    def test_more_than_ten_files(self):
        """Up to MAX_FILES_PER_TRANSFER (20) files are accepted individually; the rest
        are never added silently (the overflow prompt is answered "add" here)."""
        up = self._page()
        up._confirm_overflow = lambda paths, plan: "add"
        paths = []
        for i in range(120):
            p = os.path.join(self.tmp, f"f{i:03d}.bin")
            open(p, "wb").write(b"x" * (i + 1))
            paths.append(p)
        up.add_paths(paths)
        self.assertTrue(wait_until(lambda: True, 15))
        self.assertEqual(len(up.files), 20)
        self.assertTrue(up.go.isEnabled())
        self.assertEqual(up.sum_files.text(), "20 / 20")

    def test_folder_is_expanded_not_zipped(self):
        up = self._page()
        root = os.path.join(self.tmp, "photos")
        os.makedirs(os.path.join(root, "2026", "trip"))
        names = []
        for i in range(15):                      # more than the old 10-file cap, within the 20 limit
            d = os.path.join(root, "2026", "trip") if i % 2 else root
            p = os.path.join(d, f"img{i}.jpg")
            open(p, "wb").write(b"jpg" * 10)
            names.append(p)
        open(os.path.join(root, "empty.txt"), "w").close()          # empty: skipped
        open(os.path.join(root, "Thumbs.db"), "wb").write(b"junk")  # junk: skipped
        up.add_paths([root])
        self.assertTrue(wait_until(lambda: not up._scans and len(up.files) == 15, 15))
        self.assertEqual(sorted(up.files), sorted(os.path.abspath(p) for p in names))
        self.assertTrue(up.go.isEnabled())

    def test_size_limit_still_applies(self):
        from core import engine
        up = self._page()
        old = engine.MAX_BYTES
        import ui.pages.upload as upm
        upm.MAX_BYTES = 100
        try:
            a = os.path.join(self.tmp, "a.bin"); open(a, "wb").write(b"x" * 60)
            b = os.path.join(self.tmp, "b.bin"); open(b, "wb").write(b"x" * 60)
            up.add_paths([a, b])
            self.assertEqual(up.files, [os.path.abspath(a)])
        finally:
            upm.MAX_BYTES = old

    def test_drop_overlay_snapshot_is_opaque(self):
        from PyQt6.QtCore import QUrl
        ov = self.w.drop_overlay
        ov.begin([QUrl.fromLocalFile(self.tmp)])
        app.processEvents()
        img = ov._snap.toImage()
        w, h = img.width(), img.height()
        for x, y in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1), (w - 3, h // 2), (w // 2, h - 3)):
            self.assertEqual(img.pixelColor(x, y).alpha(), 255, f"transparent pixel at {x},{y}")
        fr = ov._frost.toImage()
        self.assertEqual(fr.pixelColor(w - 2, h - 2).alpha(), 255)
        ov.end()


@unittest.skipUnless(_deps.has_chrome_driver_capability(), _deps.chrome_skip_reason())
class TestUploadFlow(Base):
    def _page(self):
        self.w.navigate("upload")
        return self.w.pages["upload"]

    def _finished(self, up, n):
        return lambda: len(up.jobs) == n and all(j.state in ("done", "failed") for j in up.jobs)

    def test_full_upload_no_stall(self):
        up = self._page()
        sub = os.path.join(self.tmp, "album")
        os.makedirs(os.path.join(sub, "deep"))
        open(os.path.join(sub, "a.txt"), "w").write("a")
        open(os.path.join(sub, "deep", "b.txt"), "w").write("b")
        up.add_paths([self.f, self.f, sub])               # duplicate ignored, folder expanded
        self.assertTrue(wait_until(lambda: not up._scans and len(up.files) == 3, 15))
        self.assertFalse(any(f.endswith(".zip") for f in up.files))
        self.assertTrue(up.go.isEnabled())
        self.mon.start()
        up.start()
        self.assertEqual(up.files, [])                    # form is free for the next transfer
        self.assertTrue(wait_until(self._finished(up, 1), 90))
        self.mon.stop()
        job = up.jobs[0]
        self.assertEqual(job.state, "done", job.error)
        self.assertEqual(up.cards[job.id].link_edit.text(), "https://www.transfernow.net/dl/MOCK123abc")
        hist = self.ctx.history.entries()
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["kind"], "upload")
        self.assertLess(self.mon.max_gap, 0.25, f"UI stalled {self.mon.max_gap:.3f}s")
        self.assertTrue(wait_until(lambda: not up.is_running(), 15))
        up.clear_finished()
        self.assertEqual(up.jobs, [])

    def test_parallel_queue(self):
        """Three transfers queued back-to-back; two run at once, one waits."""
        self.ctx.settings.set("parallel_uploads", 2)
        up = self._page()
        self.mon.start()
        for i in range(3):
            f = os.path.join(self.tmp, f"doc{i}.txt")
            open(f, "w").write("x" * (i + 1))
            up.add_paths([f])
            up.start()                                    # compose is free again immediately
        states = sorted(j.state for j in up.jobs)
        self.assertEqual(states, ["queued", "running", "running"])
        self.assertEqual(sum(1 for j in up.jobs if j.state == "running"), 2)
        self.assertTrue(wait_until(self._finished(up, 3), 120))
        self.mon.stop()
        self.assertEqual([j.state for j in up.jobs], ["done"] * 3, [j.error for j in up.jobs])
        self.assertEqual(len(self.ctx.history.entries()), 3)
        self.assertEqual(len({e["id"] for e in self.ctx.history.entries()}), 3)
        self.assertLess(self.mon.max_gap, 0.25)

    def test_cancel_running_and_queued(self):
        os.environ["TN_SITE_URL"] = MOCK + "#60000"
        import importlib, core.engine as eng, core.tasks as tasks, ui.pages.upload as upm
        for m in (eng, tasks, upm):
            importlib.reload(m)
        self.ctx.settings.set("parallel_uploads", 1)
        self.w.pages.clear()
        up = upm.UploadPage(self.ctx)
        self.w.stack.addWidget(up)
        for i in range(2):
            f = os.path.join(self.tmp, f"c{i}.txt")
            open(f, "w").write("hi")
            up.add_paths([f]); up.start()
        self.assertEqual([j.state for j in up.jobs], ["running", "queued"])
        self.assertTrue(wait_until(lambda: up.jobs[0].step >= 5, 60))
        t0 = time.monotonic()
        queued, running = up.jobs[1], up.jobs[0]
        up.cancel_job(queued.id)                          # queued: removed instantly
        self.assertNotIn(queued, up.jobs)
        up.cancel_job(running.id)
        self.assertTrue(wait_until(lambda: not up.jobs and not up.is_running(), 20))
        self.assertLess(time.monotonic() - t0, 12)
        self.assertEqual(self.ctx.history.entries(), [])
        os.environ["TN_SITE_URL"] = MOCK
        up.shutdown()
        for m in (eng, tasks):
            importlib.reload(m)

    def test_close_while_running_quits_cleanly(self):
        up = self._page()
        up.add_paths([self.f])
        up.start()
        wait_until(lambda: up.jobs[0].step >= 2, 30)
        import ui.window as uw
        orig = uw.confirm
        uw.confirm = lambda *a, **k: True
        try:
            t0 = time.monotonic()
            self.w.close()
            self.assertLess(time.monotonic() - t0, 15)
        finally:
            uw.confirm = orig
        self.assertFalse(any(j.thread is not None and j.thread.isRunning() for j in up.jobs))


class TestRealtimeBytes(Base):
    """Real-time upload percentage: bytes scraped by the engine land in the
    job model and surface instantly on the Upload card and the Queue page."""

    def test_bytes_flow_instantly(self):
        from core.store import human_size
        from ui.pages import upload as upm
        up = self.w.page("upload")
        self.w.navigate("upload")
        job = upm.UploadJob([self.f], {self.f: 3_000_000}, "me@example.com")
        job.state = "running"                       # _pump() must not start a thread
        job.started = time.monotonic()
        up._add_job(job)
        self.assertEqual(up.jobs, [job])

        up._on_bytes(job, 1_500_000, 3_000_000)
        self.assertEqual(job.sent, 1_500_000)
        self.assertEqual(job.pct, 50)               # derived from real bytes
        card = up.cards[job.id]
        self.assertIn("50%", card.pill.text())
        self.assertIn(human_size(1_500_000), card.sub.text())

        # monotonic: older counters are ignored, 100 % is capped until the link
        up._on_bytes(job, 900_000, 3_000_000)
        self.assertEqual(job.sent, 1_500_000)
        up._on_bytes(job, 3_000_000, 3_000_000)
        self.assertEqual(job.sent, 3_000_000)
        self.assertEqual(job.pct, 98, "must not claim 100 % before the link is ready")

        # thread-level signal routing: engine "bytes" event -> uploaded(s, t)
        th = upm.UploadThread(upm.EngineOptions(files=[self.f]), None)
        got = []
        th.uploaded.connect(lambda s, t: got.append((s, t)))
        th._on_event("bytes", (2048, 4096))
        self.assertEqual(got, [(2048, 4096)])
        th.deleteLater()

        # the Queue page mirrors the very same job, live
        qp = self.w.page("queue")
        qp.rebuild()
        app.processEvents()
        row = qp.rows[job.id]
        self.assertIn("98%", row.pill.text())
        self.assertIn(human_size(3_000_000), row.meta.text())

    def test_speed_and_eta_track_real_bytes(self):
        from ui.pages import upload as upm
        up = self.w.page("upload")
        job = upm.UploadJob([self.f], {self.f: 4_000_000}, "me@example.com")
        job.state = "running"
        job.started = time.monotonic()
        up._add_job(job)
        up._on_bytes(job, 1_000_000, 4_000_000)
        time.sleep(0.12)
        up._on_bytes(job, 2_000_000, 4_000_000)
        self.assertGreater(job.speed, 0.0, "speed never computed from real bytes")
        self.assertGreater(job.eta, 0.0)
        self.assertLessEqual(job.eta, 4_000_000 / max(job.speed, 1.0) + 1.0)
        # the pill/bar show the site's honest percentage instantly
        self.assertEqual(up.cards[job.id].bar.value(), 50)


class TestDownloadFlow(Base):
    def setUp(self):
        super().setUp()
        self.out = tempfile.mkdtemp()
        self.ctx.settings.set("download_dir", self.out)
        self.ctx.settings.set("open_folder_after_download", False)
        self._orig = (downloader.fetch_info, downloader.resolve_download_url)
        self.srv = Server()
        self.url = self.srv.__enter__()
        downloader.resolve_download_url = lambda tid, timeout=20: self.url
        downloader.fetch_info = lambda link, timeout=20: {
            "transfer_id": "abc123xyz", "link": link, "title": "Files are ready!",
            "ready": True, "files": [{"name": "résumé v1.bin", "size": len(PAYLOAD)}],
            "total_bytes": len(PAYLOAD), "expiry": "Nov 09, 2026"}

    def tearDown(self):
        downloader.fetch_info, downloader.resolve_download_url = self._orig
        self.srv.__exit__()
        shutil.rmtree(self.out, ignore_errors=True)
        super().tearDown()

    def _page(self):
        self.w.navigate("download")
        return self.w.pages["download"]

    def test_fetch_and_download(self):
        Handler.mode = "ok"
        dp = self._page()
        dp.link.setText("https://www.transfernow.net/dl/abc123xyz")      # auto-fetch (debounced)
        self.assertTrue(wait_until(lambda: dp._state == 2, 10))
        self.mon.start()
        dp.start()
        self.assertTrue(wait_until(lambda: dp._state in (4, 5), 30))
        self.mon.stop()
        self.assertEqual(dp._state, 4, dp.err_text.text())
        files = os.listdir(self.out)
        self.assertEqual(files, ["résumé v1.bin"])
        self.assertEqual(open(os.path.join(self.out, files[0]), "rb").read(), PAYLOAD)
        self.assertEqual(self.ctx.history.entries()[0]["kind"], "download")
        self.assertLess(self.mon.max_gap, 0.25)

    def test_pause_resume_cancel(self):
        Handler.mode = "slow"
        dp = self._page()
        dp.prefill("https://www.transfernow.net/dl/abc123xyz")
        self.assertTrue(wait_until(lambda: dp._state == 2, 10))
        dp.start()
        self.assertTrue(wait_until(lambda: dp.run_bar.value() > 50, 20))
        dp.toggle_pause()
        time.sleep(0.4)
        v = dp.run_bar.value()
        wait_until(lambda: False, 0.6)
        self.assertLessEqual(dp.run_bar.value() - v, 70, "kept downloading while paused")
        dp.toggle_pause()
        self.assertTrue(wait_until(lambda: dp.run_bar.value() > v + 150, 20))
        dp.cancel()
        self.assertTrue(wait_until(lambda: not dp.is_running(), 10))
        self.assertEqual(dp._state, 2)
        self.assertEqual([f for f in os.listdir(self.out)], [])

    def test_bad_link_feedback(self):
        dp = self._page()
        dp.link.setText("hello world")
        self.assertIn("doesn't look", dp.hint.text())
        dp.link.setText("")
        self.assertEqual(dp._state, 0)


class TestHistoryScale(Base):
    def test_200_rows_fast(self):
        for i in range(200):
            self.ctx.history.add({"kind": "upload" if i % 2 else "download", "link": f"l{i}",
                                  "files": [{"name": f"file number {i}.zip", "size": 1000 * i}],
                                  "total_bytes": 1000 * i})
        t0 = time.monotonic()
        hp = self.w.page("history")
        self.w.navigate("history")
        hp.refresh()
        app.processEvents()
        self.assertLess(time.monotonic() - t0, 1.0)
        hp.search.setText("number 19")
        wait_until(lambda: len(hp._rows) < 200, 2)
        self.assertEqual(len(hp._rows), 11)          # 19, 190-199
        hp.filter.set_value("upload", True)
        self.assertTrue(all(e["kind"] == "upload" for e in hp._rows))

    def test_theme_switching_all_pages(self):
        for k in ("dashboard", "upload", "download", "history", "settings"):
            self.w.navigate(k)
        t0 = time.monotonic()
        for mode in ("light", "dark", "system", "light", "dark"):
            self.ctx.settings.set("theme", mode)
            theme.apply(app, mode, None)
            app.processEvents()
        for acc in ("teal", "violet", "rose", "signal"):
            theme.apply(app, "dark", acc)
            app.processEvents()
        self.assertLess((time.monotonic() - t0) / 9, 0.9)   # per switch, 5 pages + 200 rows


if __name__ == "__main__":
    unittest.main(verbosity=2)
