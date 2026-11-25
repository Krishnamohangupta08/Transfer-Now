"""Offline hardening tests for core.downloader: URL policy, redirect safety,
Range/ETag resume integrity, no-clobber destinations and cancellation.

    python -m unittest tests.test_downloader_hardening -v

All network I/O is bound to 127.0.0.1 mock servers (ThreadingTCPServer on an
ephemeral port).  Tests that need the documented test-mode escape hatch set
``TN_TEST_LOCAL_HTTP=1`` via ``unittest.mock.patch.dict`` so it can never
leak between tests.  ``RESUME_DELAYS``, ``RETRY_DELAYS`` and
``OPEN_TIMEOUTS`` are patched small; every test is deterministic and the
whole module runs well under a minute.
"""
from __future__ import annotations

import errno
import http.server
import ipaddress
import os
import shutil
import socket
import socketserver
import sys
import tempfile
import threading
import time
import unittest
from typing import ClassVar
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import downloader as dl

PAYLOAD = os.urandom(1_000_000)   # 1 MiB: real multi-chunk streaming, still fast
CD = 'attachment; filename="hardened.bin"'


# ---------------------------------------------------------------- http server
class Handler(http.server.BaseHTTPRequestHandler):
    """Step-scripted storage mock: each GET consumes the next step of
    ``cls.steps`` (the last one repeats forever).  Step keys:

    ``code``               response status (default 200)
    ``body``               exact bytes (default: PAYLOAD[start:])
    ``drop_after``         send body[:N], then shut the write side down —
                           a prompt early EOF the client reads as a drop
    ``stall``              send headers, then hold the socket without body
    ``ignore_range``       answer a Range request with 200 + full body
    ``wrong_range_start``  answer 206 but with a Content-Range claiming
                           this (wrong) start offset
    ``changed_etag``       when the request's If-Range equals this value,
                           serve the NEW entity (200 full body) instead
    ``headers``            extra response headers (Location, ETag, …)
    ``record``             store the request headers in ``cls.seen``
    """

    protocol_version = "HTTP/1.1"
    steps: ClassVar[list] = [{}]
    hits = 0
    seen: ClassVar[list] = []

    def log_message(self, *a):  # silence
        pass

    def do_GET(self):
        cls = type(self)
        step = cls.steps[min(cls.hits, len(cls.steps) - 1)]
        cls.hits += 1
        if step.get("record"):
            cls.seen.append({k.lower(): v for k, v in self.headers.items()})
        rng = self.headers.get("Range")
        start = 0
        if rng:
            try:
                start = int(rng.split("=", 1)[1].split("-")[0])
            except ValueError:
                start = 0
        code = step.get("code")
        if code is None:
            # mirror a well-behaved storage server: honor Range with 206
            code = 206 if (rng and not step.get("ignore_range")) else 200
        body = step.get("body")
        changed = step.get("changed_etag")
        if rng and changed and self.headers.get("If-Range") == changed:
            code, body, start = 200, PAYLOAD, 0        # entity changed
        elif rng and step.get("ignore_range"):
            code, start = 200, 0                       # Range ignored: full body
        elif rng and "wrong_range_start" in step:
            code, start = 206, step["wrong_range_start"]
        if body is None:
            body = PAYLOAD[start:] if code in (200, 206) else b""
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        if code == 206:
            self.send_header("Content-Range",
                             f"bytes {start}-{start + len(body) - 1}/{len(PAYLOAD)}")
        if step.get("location"):
            self.send_header("Location", step["location"])
        for k, v in (step.get("headers") or {}).items():
            self.send_header(k, v)
        if code in (200, 206):
            self.send_header("Content-Disposition", CD)
        self.end_headers()
        try:
            if step.get("stall"):
                time.sleep(8)          # hold the socket open, send nothing
                return
            drop = step.get("drop_after")
            out = body if drop is None else body[:drop]
            for i in range(0, len(out), 65536):
                self.wfile.write(out[i:i + 65536])
            if drop is not None:
                self.wfile.flush()
                # shutdown (not just close) delivers the FIN promptly so the
                # client sees the short body immediately — no timeout jitter
                self.connection.shutdown(socket.SHUT_WR)
                self.connection.close()
                self.close_connection = True
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


class ScriptServer:
    """Context manager yielding the base URL of a step-scripted mock server."""

    def __init__(self, steps: list):
        self.steps = steps

    def __enter__(self):
        self.handler = type("ScriptedHandler", (Handler,),
                            {"steps": list(self.steps), "hits": 0, "seen": []})
        socketserver.ThreadingTCPServer.allow_reuse_address = True
        self.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), self.handler)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.srv.server_address[1]}/f"

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()

    @property
    def hits(self) -> int:
        return self.handler.hits

    @property
    def seen(self) -> list:
        return self.handler.seen


class FakeResp:
    """Minimal requests.Response stand-in for offline fetch_info tests."""

    def __init__(self, status_code=200, url="", chunks=(), headers=None):
        self.status_code = status_code
        self.url = url
        self._chunks = list(chunks)
        self.headers = headers or {}
        self.closed = False

    def iter_content(self, chunk_size=None):
        yield from self._chunks

    def json(self):
        import json
        return json.loads(b"".join(self._chunks).decode("utf-8"))

    def close(self):
        self.closed = True


# ----------------------------------------------------------- download tests
class TestDownloadHardening(unittest.TestCase):
    def setUp(self):
        self.out = tempfile.mkdtemp()
        for name, value in (("RETRY_DELAYS", [0.02] * 3),
                            ("RESUME_DELAYS", [0.02] * 4),
                            ("OPEN_TIMEOUTS", (3, 3))):
            p = mock.patch.object(dl, name, value)
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        shutil.rmtree(self.out, ignore_errors=True)

    def _read(self, path: str) -> bytes:
        with open(path, "rb") as fh:
            return fh.read()

    def test_1_plain_download_is_byte_identical(self):
        with ScriptServer([{}]) as url:
            path = dl.download(url, self.out, "tid1")
        self.assertEqual(self._read(path), PAYLOAD)
        self.assertEqual(os.path.basename(path), "hardened.bin")
        self.assertEqual(os.listdir(self.out), ["hardened.bin"])  # no .part

    def test_2_resume_after_drop_is_byte_identical(self):
        with ScriptServer([{"drop_after": len(PAYLOAD) // 2}, {}]) as url:
            path = dl.download(url, self.out, "tid2", chunk=16384)
        self.assertEqual(self._read(path), PAYLOAD)
        self.assertEqual(os.listdir(self.out), [os.path.basename(path)])

    def test_3_wrong_content_range_restarts_from_zero(self):
        msgs: list[str] = []
        steps = [{"drop_after": 200_000},
                 {"wrong_range_start": 0},   # 206 claiming bytes 0-… for a
                 {}]                         # resume that asked for 200000
        with ScriptServer(steps) as url:
            path = dl.download(url, self.out, "tid3", chunk=16384,
                               status=msgs.append)
        self.assertEqual(self._read(path), PAYLOAD)
        self.assertTrue(any("restarting the download" in m for m in msgs),
                        msgs)
        self.assertEqual(os.listdir(self.out), [os.path.basename(path)])

    def test_4_changed_etag_restarts_from_zero(self):
        srv = ScriptServer([{"headers": {"ETag": '"entity-v1"'}, "drop_after": 200_000},
                            {"changed_etag": '"entity-v1"', "record": True,
                             "headers": {"ETag": '"entity-v2"'}},
                            {}])
        with srv as url:
            path = dl.download(url, self.out, "tid4", chunk=16384)
        self.assertEqual(self._read(path), PAYLOAD)   # fresh copy, not a mix
        self.assertEqual(len(srv.seen), 1)            # only the resume is recorded
        resume = srv.seen[0]
        self.assertEqual(resume.get("if-range"), '"entity-v1"')
        offset = int(resume.get("range", "bytes=0-").split("=")[1].split("-")[0])
        self.assertGreater(offset, 0)                # resumed mid-stream
        self.assertLessEqual(offset, 200_000)        # from the last written byte
        self.assertEqual(os.listdir(self.out), [os.path.basename(path)])

    def test_5_server_ignoring_range_restarts(self):
        steps = [{"drop_after": 200_000},
                 {"ignore_range": True},   # 200 + full body despite Range
                 {}]
        with ScriptServer(steps) as url:
            path = dl.download(url, self.out, "tid5", chunk=16384)
        self.assertEqual(self._read(path), PAYLOAD)
        self.assertEqual(os.listdir(self.out), [os.path.basename(path)])

    def test_6_truncated_content_fails_clean(self):
        steps = [{"drop_after": len(PAYLOAD) // 2},
                 {"code": 503}]          # every resume attempt fails too
        with ScriptServer(steps) as url:  # noqa: SIM117 — the assertRaises body reads clearer nested
            with self.assertRaises(dl.TransferLinkError):
                dl.download(url, self.out, "tid6", chunk=16384)
        self.assertEqual(os.listdir(self.out), [])   # .part removed

    def test_7_redirect_to_loopback_rejected_then_allowed_with_flag(self):
        with ScriptServer([{}]) as b_url:
            a = ScriptServer([{"code": 302, "location": b_url}])
            a_url = a.__enter__()
            with self.assertRaises(dl.TransferLinkError):
                dl.download(a_url, self.out, "tid7")
            self.assertEqual(os.listdir(self.out), [])
            # the documented test-mode escape hatch allows the same hop
            with mock.patch.dict(os.environ, {dl.TEST_LOCAL_HTTP_ENV: "1"}):
                path = dl.download(a_url, self.out, "tid7")
            a.__exit__(None, None, None)
        self.assertEqual(self._read(path), PAYLOAD)

    def test_7b_redirect_to_private_or_ipv6_loopback_rejected(self):
        for location in ("http://10.9.9.9/x", "http://[::1]/x",
                         "https://192.168.0.7/x", "https://169.254.1.1/x"):
            a = ScriptServer([{"code": 302, "location": location}])
            a_url = a.__enter__()
            with self.assertRaises(dl.TransferLinkError, msg=location):
                dl.download(a_url, self.out, "tid7b")
            a.__exit__(None, None, None)
            self.assertEqual(os.listdir(self.out), [])

    def test_7c_flag_does_not_allow_private_targets(self):
        a = ScriptServer([{"code": 302, "location": "http://10.9.9.9/x"}])
        a_url = a.__enter__()
        with mock.patch.dict(os.environ, {dl.TEST_LOCAL_HTTP_ENV: "1"}):  # noqa: SIM117 — the assertRaises body reads clearer nested
            with self.assertRaises(dl.TransferLinkError):
                dl.download(a_url, self.out, "tid7c")
        a.__exit__(None, None, None)

    def test_7d_cross_origin_referer_dropped(self):
        # 127.0.0.1 -> localhost: different host (same machine).  The Referer
        # must ride on the initial hop but be stripped on the cross-origin one.
        b = ScriptServer([{"record": True}])
        b_url = b.__enter__()
        self.addCleanup(b.__exit__, None, None, None)
        location = b_url.replace("127.0.0.1", "localhost")
        a = ScriptServer([{"code": 302, "location": location, "record": True}])
        a_url = a.__enter__()
        self.addCleanup(a.__exit__, None, None, None)
        with mock.patch.dict(os.environ, {dl.TEST_LOCAL_HTTP_ENV: "1"}):
            path = dl.download(a_url, self.out, "tid7d")
        self.assertEqual(self._read(path), PAYLOAD)
        self.assertIn("referer", a.seen[0])      # initial storage hop carries it
        self.assertNotIn("referer", b.seen[0])   # cross-origin hop: stripped
        self.assertTrue(b.seen[0]["host"].startswith("localhost"))
        self.assertEqual(os.listdir(self.out), [os.path.basename(path)])

    def test_8_concurrent_same_name_downloads(self):
        with ScriptServer([{}]) as url:
            results, errors = [], []

            def run():
                try:
                    results.append(dl.download(url, self.out, "tid8"))
                except Exception as exc:  # noqa: BLE001 — collected below
                    errors.append(exc)

            threads = [threading.Thread(target=run) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(30)
        self.assertFalse(errors, errors)
        self.assertEqual(len(results), 2)
        self.assertEqual(len({os.path.basename(p) for p in results}), 2)
        for p in results:
            self.assertEqual(self._read(p), PAYLOAD)
        self.assertEqual(sorted(os.listdir(self.out)),
                         sorted(os.path.basename(p) for p in results))

    def test_9_disk_write_failure_is_readable_and_clean(self):
        real_fdopen = os.fdopen

        class BoomFile:
            def __init__(self, f):
                self._f = f

            def write(self, data):
                raise OSError(errno.ENOSPC, "No space left on device")

            def seek(self, *a):
                return self._f.seek(*a)

            def truncate(self, *a):
                return self._f.truncate(*a)

            def close(self):
                return self._f.close()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return self._f.__exit__(*a)

        def boom_fdopen(fd, mode="r", *a, **kw):
            return BoomFile(real_fdopen(fd, mode, *a, **kw))

        with ScriptServer([{}]) as url, mock.patch("os.fdopen", boom_fdopen):  # noqa: SIM117 — the assertRaises body reads clearer nested
            with self.assertRaises(dl.TransferLinkError) as ctx:
                dl.download(url, self.out, "tid9")
        self.assertIn("disk space", str(ctx.exception).lower())
        self.assertEqual(os.listdir(self.out), [])   # .part removed

    def test_10_cancel_during_stalled_response_is_fast(self):
        with mock.patch.object(dl, "OPEN_TIMEOUTS", (1, 2)):  # noqa: SIM117 — the assertRaises body reads clearer nested
            with ScriptServer([{"stall": True}]) as url:
                stop = threading.Event()
                threading.Timer(0.3, stop.set).start()
                t0 = time.monotonic()
                with self.assertRaises(dl.DownloadCancelled):
                    dl.download(url, self.out, "tid10", should_stop=stop.is_set)
                self.assertLess(time.monotonic() - t0, 5.0,
                                "cancel must surface within seconds")
        self.assertEqual(os.listdir(self.out), [])


# ------------------------------------------------------------ policy tests
class TestPolicies(unittest.TestCase):
    """Unit tests for the URL/validation layer — no network, no servers."""

    def test_parse_transfer_id_accepts_official_forms(self):
        good = {
            "https://www.transfernow.net/dl/202611022PyI3yji": "202611022PyI3yji",
            "https://transfernow.net/dl/abc123xyz": "abc123xyz",
            "https://www.transfernow.net/en/d/abc123xyz": "abc123xyz",
            "https://www.transfernow.net/en-US/dl/abc123xyz": "abc123xyz",
            "https://www.transfernow.net/dl/abc123xyz/": "abc123xyz",
            "https://www.transfernow.net/dl/abc123xyz?utm=x": "abc123xyz",
            "transfernow.net/dl/abc123xyz": "abc123xyz",       # scheme-less paste
            "WWW.TRANSFERNOW.NET/DL/ABC123XYZ": "ABC123XYZ",
            "abc123xyz": "abc123xyz",                          # bare id
        }
        for link, tid in good.items():
            self.assertEqual(dl.parse_transfer_id(link), tid, link)

    def test_parse_transfer_id_rejects_lookalikes_and_tricks(self):
        bad = [
            "https://evil.com/?u=transfernow.net/dl/abc123",   # substring trick
            "https://www.transfernow.net.evil.com/dl/abc123x", # suffix spoof
            "https://www2.transfernow.net/dl/abc123xyz",       # subdomain trick
            "http://www.transfernow.net/dl/abc123xyz",         # not https
            "https://user:pass@www.transfernow.net/dl/abc123xyz",
            "https://www.transfernow.net:8443/dl/abc123xyz",   # port
            "https://example.com/foo",
            "https://www.transfernow.net/dl/",                 # no id
            "", "   ", "a b", "short",
        ]
        for link in bad:
            self.assertIsNone(dl.parse_transfer_id(link), link)

    def test_assert_public_https_rejections(self):
        cases = [
            "http://download.transfernow.net/x",                # not https
            "https://user:pass@download.transfernow.net/x",     # credentials
            "https://download.transfernow.net:8443/x",          # port
            "https://evil.com/x",                               # untrusted host
            "https://1.2.3.4/x",                                # literal IP
            "https://127.0.0.1/x",                              # loopback IP
            "https://[::1]/x",                                  # IPv6 loopback
            "https://10.0.0.5/x",                               # private
            "https://192.168.1.5/x",
            "https://169.254.9.9/x",                            # link-local
            "https://storage.transfernow.net.evil.tld/x",
        ]
        for url in cases:
            with self.assertRaises(dl.TransferLinkError, msg=url):
                dl._assert_public_https(url)

    def test_assert_public_https_accepts_allowlist_and_family(self):
        public = [ipaddress.ip_address("93.184.216.34")]
        with mock.patch.object(dl, "_resolve_host_ips", return_value=public):
            dl._assert_public_https("https://download.transfernow.net/x")
            dl._assert_public_https("https://cdn.transfernow.net/a/b?sig=1")
            # documented escape hatch: any *.transfernow.net subdomain
            dl._assert_public_https("https://mirror-cdn.transfernow.net/x")

    def test_assert_public_https_dns_rebinding_guard(self):
        # all resolved addresses must be public — a private answer rejects
        with mock.patch.object(dl, "_resolve_host_ips",  # noqa: SIM117 — the assertRaises body reads clearer nested
                               return_value=[ipaddress.ip_address("10.1.2.3")]):
            with self.assertRaises(dl.TransferLinkError):
                dl._assert_public_https("https://download.transfernow.net/x")
        # and a host that cannot be verified fails closed
        with mock.patch.object(dl, "_resolve_host_ips",  # noqa: SIM117 — the assertRaises body reads clearer nested
                               side_effect=socket.gaierror(1, "offline")):
            with self.assertRaises(dl.TransferLinkError):
                dl._assert_public_https("https://download.transfernow.net/x")

    def test_assert_public_https_test_escape_hatch(self):
        # the flag admits ONLY http/https loopback destinations
        with mock.patch.dict(os.environ, {dl.TEST_LOCAL_HTTP_ENV: "1"}):
            dl._assert_public_https("http://127.0.0.1:12345/f")
            dl._assert_public_https("http://localhost/f")
            with self.assertRaises(dl.TransferLinkError):
                dl._assert_public_https("http://10.0.0.5/f")     # private
            with self.assertRaises(dl.TransferLinkError):
                dl._assert_public_https("ftp://127.0.0.1/f")     # scheme

    def test_stream_target_policy(self):
        # download() keeps 127.0.0.1/localhost reachable for test harnesses
        dl._assert_stream_target("http://127.0.0.1:12345/f")
        dl._assert_stream_target("http://localhost/f")
        dl._assert_stream_target("https://download.transfernow.net/x")
        for url in ("ftp://127.0.0.1/f", "file:///etc/passwd",
                    "http://10.0.0.5/x", "http://[::1]/x",
                    "http://user@127.0.0.1/f"):
            with self.assertRaises(dl.TransferLinkError, msg=url):
                dl._assert_stream_target(url)
        # redirect hops additionally require https (unless flagged loopback);
        # the named-host resolution is steered through the patchable seam —
        # no live DNS in unit tests
        with mock.patch.object(dl, "_resolve_host_ips",  # noqa: SIM117 — the assertRaises body reads clearer nested
                               return_value=[ipaddress.ip_address("93.184.216.34")]):
            dl._assert_stream_target("https://mirror.transfernow.net/x",
                                     https_required=True, resolve_named=True)
        with self.assertRaises(dl.TransferLinkError):
            dl._assert_stream_target("http://example.com/x", https_required=True)

    def test_redirect_header_hygiene(self):
        h, sent = dl._redirect_headers("https://files.transfernow.net/a",
                                       "https://files.transfernow.net/b",
                                       500, '"etag-1"')
        self.assertEqual(h.get("Range"), "bytes=500-")
        self.assertEqual(h.get("If-Range"), '"etag-1"')
        self.assertIn("Referer", h)
        self.assertEqual(sent, 500)
        # cross-origin: no Referer, no Range, no If-Range — fresh stream
        h, sent = dl._redirect_headers("https://files.transfernow.net/a",
                                       "https://cdn.example.com/b",
                                       500, '"etag-1"')
        self.assertNotIn("Range", h)
        self.assertNotIn("If-Range", h)
        self.assertNotIn("Referer", h)
        self.assertEqual(sent, 0)
        # a transfernow.net-family target keeps the hotlink-protection Referer
        h, sent = dl._redirect_headers("https://files.transfernow.net/a",
                                       "https://www.transfernow.net/en/d/start",
                                       0, None)
        self.assertIn("Referer", h)
        self.assertEqual(sent, 0)

    # -------------------------------------------------- resolve_download_url
    def _fake_api(self, payload, status_code=200):
        calls = []

        def fake_get(url, params=None, headers=None, timeout=None):
            calls.append({"url": url, "params": params})
            body = payload() if callable(payload) else payload
            return FakeResp(status_code=status_code,
                            chunks=[(body.encode() if isinstance(body, str)
                                     else body)])

        return fake_get, calls

    def _resolve(self, fake_get, transfer_id="abc123xyz"):
        with mock.patch.object(dl.requests, "get", fake_get):
            return dl.resolve_download_url(transfer_id)

    def test_resolve_download_url_validates_schema_and_target(self):
        fake, calls = self._fake_api('{"url": "https://download.transfernow.net/s"}')
        public = [ipaddress.ip_address("93.184.216.34")]
        with mock.patch.object(dl, "_resolve_host_ips", return_value=public):
            self.assertEqual(self._resolve(fake),
                             "https://download.transfernow.net/s")
        self.assertEqual(calls[0]["params"]["transferId"], "abc123xyz")
        self.assertEqual(calls[0]["params"]["preview"], "false")

        bad_cases = [
            ("status", {"status": 500}),                       # HTTP error
            ("list", "[1, 2, 3]"),                             # JSON list
            ("null", "null"),                                  # JSON null
            ("missing", "{}"),                                 # no url key
            ("empty", '{"url": ""}'),                          # empty url
            ("wrongtype", '{"url": 42}'),                      # non-string
            ("http", '{"url": "http://download.transfernow.net/x"}'),
            ("evil", '{"url": "https://evil.com/x"}'),
            ("ip", '{"url": "https://10.0.0.5/x"}'),
        ]
        for name, body in bad_cases:
            status = 500 if name == "status" else 200
            fake, _ = self._fake_api(body, status_code=status)
            with self.assertRaises(dl.TransferLinkError, msg=name):
                self._resolve(fake)

    def test_resolve_download_url_never_leaks_the_url(self):
        fake, _ = self._fake_api('{"url": "https://evil.com/signed?token=SECRET"}')
        with self.assertRaises(dl.TransferLinkError) as ctx:
            self._resolve(fake)
        self.assertNotIn("evil.com", str(ctx.exception))
        self.assertNotIn("SECRET", str(ctx.exception))

    # ------------------------------------------------------------ fetch_info
    def test_fetch_info_rejects_offsite_redirect(self):
        fake_resp = FakeResp(status_code=200, url="https://evil.com/dl/abc123xyz",
                             chunks=[b"<html>phish</html>"])

        def fake_get(url, headers=None, timeout=None, allow_redirects=True,
                     stream=False):
            return fake_resp

        with mock.patch.object(dl.requests, "get", fake_get):  # noqa: SIM117 — the assertRaises body reads clearer nested
            with self.assertRaises(dl.TransferLinkError):
                dl.fetch_info("https://www.transfernow.net/dl/abc123xyz")
        self.assertTrue(fake_resp.closed)

    def test_fetch_info_caps_page_size(self):
        big = [b"x" * (1 << 20)] * 6     # 6 MiB > MAX_PAGE_BYTES (5 MiB)
        fake_resp = FakeResp(status_code=200,
                             url="https://www.transfernow.net/dl/abc123xyz",
                             chunks=big)

        def fake_get(url, headers=None, timeout=None, allow_redirects=True,
                     stream=False):
            return fake_resp

        with mock.patch.object(dl.requests, "get", fake_get):  # noqa: SIM117 — the assertRaises body reads clearer nested
            with self.assertRaises(dl.TransferLinkError):
                dl.fetch_info("https://www.transfernow.net/dl/abc123xyz")
        self.assertTrue(fake_resp.closed)

    def test_fetch_info_rejects_bad_links_before_any_request(self):
        def boom(*a, **kw):
            raise AssertionError("no request may be made for a bad link")

        with mock.patch.object(dl.requests, "get", boom):
            for link in ("http://www.transfernow.net/dl/abc123xyz",
                         "https://evil.com/?u=transfernow.net/dl/abc123"):
                with self.assertRaises(dl.TransferLinkError):
                    dl.fetch_info(link)

    def test_fetch_info_parses_a_page(self):
        html = ('<h1 class="downloader_title__x">My transfer</h1>'
                '"validity":{"from":"2026-01-01T00:00:00Z",'
                '"to":"2026-02-01T00:00:00Z"}'
                '"files":[{"name":"a.txt","size":10},{"name":"b.bin","size":20}]'
                'Files are ready')
        fake_resp = FakeResp(status_code=200,
                             url="https://www.transfernow.net/dl/abc123xyz",
                             chunks=[html.encode()])

        def fake_get(url, headers=None, timeout=None, allow_redirects=True,
                     stream=False):
            return fake_resp

        with mock.patch.object(dl.requests, "get", fake_get):
            info = dl.fetch_info("https://www.transfernow.net/dl/abc123xyz")
        self.assertEqual(info["transfer_id"], "abc123xyz")
        self.assertEqual(info["title"], "My transfer")
        self.assertEqual([f["name"] for f in info["files"]], ["a.txt", "b.bin"])
        self.assertEqual(info["total_bytes"], 30)
        self.assertTrue(info["ready"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
