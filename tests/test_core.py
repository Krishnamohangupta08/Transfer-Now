"""Offline tests: parsing, storage, downloader (local HTTP server), engine
(headless Chrome against a mock page) and the Qt upload flow.

    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import http.server
import os
import shutil
import socketserver
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
DATA = tempfile.mkdtemp(prefix="tn-test-data-")
os.environ["TN_DATA_DIR"] = DATA
MOCK = "file://" + os.path.join(ROOT, "tests", "mock_site", "index.html")

from core import downloader as dl  # noqa: E402
from core import store  # noqa: E402

PAYLOAD = os.urandom(3_000_000)


# ---------------------------------------------------------------- http server
class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    mode = "ok"            # ok | flaky (drop mid-stream once) | notfound_then_ok | slow
    hits = 0

    def log_message(self, *a):  # silence
        pass

    def do_GET(self):  # noqa: N802
        cls = type(self)
        cls.hits += 1
        if cls.mode == "notfound_then_ok" and cls.hits < 3:
            self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers()
            return
        start = 0
        rng = self.headers.get("Range")
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
        body = PAYLOAD[start:]
        self.send_response(206 if rng else 200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", "attachment; filename*=UTF-8''r%C3%A9sum%C3%A9%20v1.bin")
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{len(PAYLOAD)-1}/{len(PAYLOAD)}")
        self.end_headers()
        try:
            if cls.mode == "flaky" and not rng:
                self.wfile.write(body[:1_000_000]); self.wfile.flush()
                self.connection.close()
                return
            step = 65536
            for i in range(0, len(body), step):
                self.wfile.write(body[i:i + step])
                if cls.mode == "slow":
                    self.wfile.flush(); time.sleep(0.05)
        except (BrokenPipeError, ConnectionResetError):
            pass


class Server:
    def __enter__(self):
        socketserver.ThreadingTCPServer.allow_reuse_address = True
        self.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        Handler.hits = 0
        return f"http://127.0.0.1:{self.srv.server_address[1]}/f"

    def __exit__(self, *a):
        self.srv.shutdown(); self.srv.server_close()


# ------------------------------------------------------------------- parsing
class TestParsing(unittest.TestCase):
    def test_ids(self):
        self.assertEqual(dl.parse_transfer_id("https://www.transfernow.net/dl/202611022PyI3yji"),
                         "202611022PyI3yji")
        self.assertEqual(dl.parse_transfer_id("https://www.transfernow.net/en/d/abc123xyz"), "abc123xyz")
        self.assertEqual(dl.parse_transfer_id("abc123xyz"), "abc123xyz")
        self.assertIsNone(dl.parse_transfer_id("https://example.com/foo"))
        self.assertIsNone(dl.parse_transfer_id(""))
        self.assertIsNone(dl.parse_transfer_id("a b"))

    def test_safe_names(self):
        self.assertEqual(dl._safe_name("../../etc/passwd"), "passwd")
        self.assertEqual(dl._safe_name("a:b*c?.txt"), "a_b_c_.txt")
        self.assertEqual(dl._safe_name("CON.txt"), "_CON.txt")
        self.assertEqual(dl._safe_name("résumé.pdf"), "résumé.pdf")
        self.assertTrue(dl._safe_name("") )
        self.assertEqual(dl._filename_from("u", "attachment; filename=\"a b.zip\"", "x"), "a b.zip")

    def test_human_size(self):
        self.assertEqual(store.human_size(0), "0 B")
        self.assertEqual(store.human_size(1536), "1.5 KB")
        self.assertEqual(store.human_size(5 * 1024 ** 3), "5.0 GB")


# --------------------------------------------------------------------- store
class TestStore(unittest.TestCase):
    def test_history_roundtrip_and_cap(self):
        p = os.path.join(tempfile.mkdtemp(), "h.json")
        h = store.History(p)
        for i in range(250):
            h.add({"kind": "upload", "link": f"l{i}", "files": []})
        self.assertEqual(len(h.entries()), 200)
        self.assertEqual(len(set(e["id"] for e in h.entries())), 200)
        h2 = store.History(p)
        self.assertEqual(len(h2.entries()), 200)
        h2.remove_many([e["id"] for e in h2.entries()[:10]])
        self.assertEqual(len(store.History(p).entries()), 190)
        h2.clear()
        self.assertEqual(store.History(p).entries(), [])

    def test_corrupt_json_is_ignored(self):
        p = os.path.join(tempfile.mkdtemp(), "h.json")
        with open(p, "w") as f:
            f.write("{not json")
        self.assertEqual(store.History(p).entries(), [])
        s = store.Settings(p)
        self.assertEqual(s.get("theme"), "dark")

    def test_legacy_migration_gives_unique_ids(self):
        """Imported legacy records must not share one id (delete would wipe all)."""
        import json
        legacy = [{"when": f"2025-01-0{i} 10:00", "link": f"l{i}", "files": []}
                  for i in range(1, 6)]
        old = store.LEGACY_HISTORY
        try:
            store.LEGACY_HISTORY = os.path.join(tempfile.mkdtemp(), "history.json")
            with open(store.LEGACY_HISTORY, "w", encoding="utf-8") as f:
                json.dump(legacy, f)
            h = store.History(os.path.join(tempfile.mkdtemp(), "new.json"))
            ids = [e["id"] for e in h.entries()]
            self.assertEqual(len(ids), 5)
            self.assertEqual(len(set(ids)), 5)
        finally:
            store.LEGACY_HISTORY = old

    def test_active_logic(self):
        now = store.now_iso()
        self.assertTrue(store.is_active({"kind": "upload", "date": now, "link": "x", "expiry": "7 days"}))
        old = "2020-01-01T00:00:00+00:00"
        self.assertFalse(store.is_active({"kind": "upload", "date": old, "link": "x", "expiry": "7 days"}))
        self.assertFalse(store.is_active({"kind": "download", "date": now, "link": "x"}))


# ---------------------------------------------------------------- downloader
class TestDownloader(unittest.TestCase):
    def setUp(self):
        self.out = tempfile.mkdtemp()
        self._delays = dl.RETRY_DELAYS
        dl.RETRY_DELAYS = [0.05] * 4

    def tearDown(self):
        dl.RETRY_DELAYS = self._delays
        shutil.rmtree(self.out, ignore_errors=True)

    def test_plain(self):
        Handler.mode = "ok"
        with Server() as url:
            path = dl.download(url, self.out, "t")
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), PAYLOAD)
        self.assertEqual(os.path.basename(path), "résumé v1.bin")
        self.assertFalse([f for f in os.listdir(self.out) if f.endswith(".part")])

    def test_unique_name(self):
        Handler.mode = "ok"
        with Server() as url:
            a = dl.download(url, self.out, "t"); b = dl.download(url, self.out, "t")
        self.assertNotEqual(a, b)

    def test_resume_after_drop(self):
        Handler.mode = "flaky"
        with Server() as url:
            path = dl.download(url, self.out, "t", chunk=32768)
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), PAYLOAD)

    def test_retry_on_404(self):
        Handler.mode = "notfound_then_ok"
        with Server() as url:
            path = dl.download(url, self.out, "t")
        self.assertEqual(os.path.getsize(path), len(PAYLOAD))

    def test_cancel_cleans_part(self):
        Handler.mode = "slow"
        stop = threading.Event()
        with Server() as url:
            threading.Timer(0.4, stop.set).start()
            with self.assertRaises(dl.DownloadCancelled):
                dl.download(url, self.out, "t", should_stop=stop.is_set)
        self.assertEqual(os.listdir(self.out), [])

    def test_unreachable_is_readable_error(self):
        with self.assertRaises(dl.TransferLinkError):
            dl.download("http://127.0.0.1:9/x", self.out, "t")


# -------------------------------------------------------------------- engine
def _chrome_available() -> bool:
    return bool(shutil.which("google-chrome") or shutil.which("chromium")
                or shutil.which("chrome") or shutil.which("chromium-browser"))


class TestButtonMatching(unittest.TestCase):
    """The CTA label matching — the live site appends a decorative suffix to
    the buttons ("Get a link · 3 files · 3.9 MB"), which broke exact matching
    with "The 'Get a link' button was not found." (1.0.0.0 regression)."""

    def setUp(self):
        import core.engine as eng
        self.m = eng.TransferEngine._text_matches
        self.norm = eng.TransferEngine._norm

    def test_exact_label(self):
        self.assertTrue(self.m("Get a link", "Get a link"))
        self.assertTrue(self.m("Create a link", "Create a link"))

    def test_live_suffix_variants(self):
        # what transfernow.net renders on the CTA since the 2025-11 redesign
        self.assertTrue(self.m("Get a link · 3 files · 3.9 MB", "Get a link"))
        self.assertTrue(self.m("Get a link \u00a0· 1 file · 0.0 MB", "Get a link"))
        self.assertTrue(self.m("Get a link · 10 files · 1.2 GB", "Get a link"))

    def test_still_rejects_different_actions(self):
        self.assertFalse(self.m("Get a link with Premium", "Get a link"))
        self.assertFalse(self.m("Get Premium", "Get a link"))
        self.assertFalse(self.m("Send a link", "Get a link"))
        self.assertFalse(self.m("", "Get a link"))
        self.assertFalse(self.m("Get a link · " + "x" * 60, "Get a link"))

    def test_whitespace_is_normalised(self):
        self.assertEqual(self.norm(" \u00a0Get  a\nlink\t "), "Get a link")
        self.assertTrue(self.m("\u00a0Get a link\u00a0", "Get a link"))


class TestPageProgress(unittest.TestCase):
    """The DOM progress scraper in _wait_for_link — no browser needed:
    the driver is faked, so the loop's data flow is tested directly."""

    def setUp(self):
        import types
        import core.engine as eng
        self.eng = eng
        self.blob = os.path.join(tempfile.mkdtemp(), "blob.bin")
        with open(self.blob, "wb") as f:
            f.write(os.urandom(1_000_000))
        self.events: list = []
        self.e = eng.TransferEngine(eng.EngineOptions(files=[self.blob]),
                                    lambda n, p: self.events.append((n, p)))
        self.e._stop = types.SimpleNamespace(is_set=lambda: False, wait=lambda s: False)

    def tearDown(self):
        shutil.rmtree(os.path.dirname(self.blob), ignore_errors=True)

    def _run(self, script_result, link_on_call=3):
        import types
        calls = {"n": 0}

        class FakeDriver:
            def execute_script(self, js, *a):
                self.seen_js = js
                return script_result() if callable(script_result) else script_result

        drv = FakeDriver()

        def find_link(_d):
            calls["n"] += 1
            return "https://www.transfernow.net/dl/FAKE1" if calls["n"] >= link_on_call else None

        self.e._find_link = find_link
        link = self.e._wait_for_link(drv, os.path.getsize(self.blob))
        return link, drv

    def test_real_progress_and_bytes_reach_the_ui(self):
        data = {"pct": 88.4, "sent": 884_000, "total": 1_000_000}
        link, drv = self._run(lambda: dict(data))
        self.assertEqual(link, "https://www.transfernow.net/dl/FAKE1")
        pcts = [p for n, p in self.events if n == "progress"]
        self.assertEqual(pcts, [88], f"page % must win over the 62 % estimate: {pcts}")
        self.assertIn("bytes", [n for n, _ in self.events])
        self.assertEqual(self.last_bytes(), (884_000, 1_000_000))
        self.assertIn("progressbar", getattr(drv, "seen_js", ""))

    def test_bytes_only_page_derives_percent(self):
        self._run(lambda: {"pct": None, "sent": 500_000, "total": 1_000_000})
        pcts = [p for n, p in self.events if n == "progress"]
        self.assertEqual(pcts, [50], f"pct must derive from real bytes: {pcts}")

    def test_fallback_estimate_when_page_hides_progress(self):
        self._run(lambda: None)
        pcts = [p for n, p in self.events if n == "progress"]
        self.assertTrue(pcts)
        self.assertEqual(pcts[0], 62, "fallback estimate must start at 62 %")
        self.assertNotIn("bytes", [n for n, _ in self.events])

    def test_broken_driver_never_crashes(self):
        def boom(js, *a):
            raise RuntimeError("detached frame")
        self._run(boom)
        pcts = [p for n, p in self.events if n == "progress"]
        self.assertTrue(pcts, "fallback must still report progress")

    def last_bytes(self):
        return next(p for n, p in reversed(self.events) if n == "bytes")


@unittest.skipUnless(_chrome_available(), "Chrome not installed")
class TestEngine(unittest.TestCase):
    def setUp(self):
        os.environ["TN_SITE_URL"] = MOCK
        import importlib, core.engine as eng
        importlib.reload(eng)
        self.eng = eng
        self.f1 = os.path.join(tempfile.mkdtemp(), "a.txt")
        open(self.f1, "w").write("hello")
        self.events: list = []

    def run_engine(self, **kw):
        e = self.eng.TransferEngine(self.eng.EngineOptions(files=[self.f1], human_pace=False, **kw),
                                    lambda n, p: self.events.append((n, p)))
        return e

    def last(self, name):
        return next((p for n, p in reversed(self.events) if n == name), None)

    def test_success(self):
        e = self.run_engine(email="me@example.com")
        e.run()
        res = self.last("success")
        self.assertIsNotNone(res, self.events)
        self.assertEqual(res["link"], "https://www.transfernow.net/dl/MOCK123abc")
        self.assertEqual(res["email"], "me@example.com")
        self.assertEqual(res["expiry"], "7 days")
        self.assertEqual(res["files"][0]["name"], "a.txt")

    def test_random_email_used(self):
        e = self.run_engine()
        e.run()
        self.assertIn("@", self.last("success")["email"])

    def test_real_progress_scraped_from_page(self):
        """The mock page animates a real progressbar: the engine must read it
        (fast, far above the 62 % start of the fallback estimate) instead of
        guessing from the clock."""
        e = self.run_engine(email="me@example.com")
        e.run()
        self.assertIsNotNone(self.last("success"), self.events)
        pcts = [p for n, p in self.events if n == "progress"]
        self.assertTrue(pcts, "no progress events at all")
        self.assertGreaterEqual(max(pcts), 90,
                                f"real page progress not scraped: {pcts}")
        # the scraper must beat the slow fallback estimate during the window
        self.assertLess(pcts[0], 62 + 8,
                        "progress jumped like the estimate, not the page")

    def test_cancel_is_fast_and_kills_browser(self):
        os.environ["TN_SITE_URL"] = MOCK + "#60000"       # link appears after 60 s
        import importlib; importlib.reload(self.eng)
        e = self.run_engine(email="me@example.com")
        t = threading.Thread(target=e.run); t.start()
        while not any(n == "step" and p[0] == "upload" for n, p in self.events):
            time.sleep(0.1)
        time.sleep(0.5)
        t0 = time.time(); e.request_stop(); t.join(20)
        self.assertFalse(t.is_alive(), "engine hung on cancel")
        self.assertLess(time.time() - t0, 12)
        self.assertEqual(self.last("failed"), "cancelled")
        time.sleep(0.5)
        self.assertIsNone(e._driver)

    def test_validation(self):
        e = self.eng.TransferEngine(self.eng.EngineOptions(files=["/nonexistent/x"]),
                                    lambda n, p: self.events.append((n, p)))
        e.run()
        self.assertIn("File not found", self.last("failed"))
        e = self.eng.TransferEngine(self.eng.EngineOptions(files=[]),
                                    lambda n, p: self.events.append((n, p)))
        e.run()
        self.assertIn("No files", self.last("failed"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
