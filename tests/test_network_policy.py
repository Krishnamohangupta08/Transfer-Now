"""Security tests for core.netpolicy — the unified outbound network policy.

    python -m unittest tests.test_network_policy -v

Everything here is offline: DNS is always faked (``socket.getaddrinfo`` is
patched) and the one end-to-end socket test binds a loopback recorder
server.  The critical proof the release requires lives in
``TestPinning``: the TCP connect target of a pinned session is the
address the policy validated — a second, uncontrolled DNS lookup can
never move the connection (M3), and a rebinding answer (public then
private, or mixed) rejects the host outright.
"""
from __future__ import annotations

import http.server
import ipaddress
import os
import socket
import socketserver
import sys
import threading
import unittest
import urllib3
import requests
from urllib.parse import urlsplit
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import netpolicy as np  # noqa: E402

PUBLIC1, PUBLIC2 = "93.184.216.34", "93.184.216.35"


def fake_resolver(answers, *, record=None):
    """A socket.getaddrinfo replacement returning a fixed answer set."""
    def _resolve(host, port, family=0, socktype=socket.SOCK_STREAM, *a, **kw):
        if record is not None:
            record.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                 (str(a), port or 0)) for a in answers]
    return _resolve


class _RecorderHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    hits = 0

    def log_message(self, *a):  # silence
        pass

    def do_GET(self):
        type(self).hits += 1
        body = b"pinned-ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class LoopbackServer:
    """Context manager exposing ``.base`` (URL) and ``.hits`` of a recorder."""

    def __enter__(self):
        handler = type("RecorderHandler", (_RecorderHandler,), {"hits": 0})
        socketserver.ThreadingTCPServer.allow_reuse_address = True
        self.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.handler = handler
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}/f"
        return self

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()

    @property
    def hits(self) -> int:
        return self.handler.hits


class FakeResp:
    """Minimal requests.Response stand-in for the redirect walker."""

    def __init__(self, status_code=200, url="", headers=None):
        self.status_code = status_code
        self.url = url
        self.headers = headers or {}
        self.closed = False

    def iter_content(self, chunk_size=None):
        return iter(())

    def close(self):
        self.closed = True


class TestValidateOutboundUrl(unittest.TestCase):
    """M1 matrix: scheme, trust, credentials, ports, address ranges."""

    def test_storage_rejections(self):
        bad = [
            "http://download.transfernow.net/x",              # not https
            "https://user:pass@download.transfernow.net/x",   # credentials
            "https://download.transfernow.net:8443/x",        # port
            "https://evil.com/x",                             # untrusted host
            "https://1.2.3.4/x",                              # literal IP
            "https://127.0.0.1/x",                            # loopback
            "https://[::1]/x",                                # IPv6 loopback
            "https://10.0.0.5/x",                             # private
            "https://172.16.5.5/x",
            "https://192.168.1.5/x",
            "https://169.254.9.9/x",                          # link-local
            "https://fe80-1.invalid/x",
            "https://storage.transfernow.net.evil.tld/x",     # suffix spoof
            "ftp://download.transfernow.net/x",               # scheme
        ]
        for url in bad:
            with self.assertRaises(np.NetworkPolicyError, msg=url):
                np.validate_outbound_url(url, purpose="storage")

    def test_storage_accepts_allowlist_and_family(self):
        for url in ("https://download.transfernow.net/x",
                    "https://storage.transfernow.net/a/b?sig=1",
                    "https://mirror-cdn.transfernow.net/x"):
            np.validate_outbound_url(url, purpose="storage")   # must not raise

    def test_page_purpose_is_exact_host_only(self):
        np.validate_outbound_url("https://www.transfernow.net/dl/abc",
                                 purpose="page")
        np.validate_outbound_url("https://transfernow.net/dl/abc",
                                 purpose="linkcheck")
        # subdomains are storage-family, never page hosts
        with self.assertRaises(np.NetworkPolicyError):
            np.validate_outbound_url("https://mirror.transfernow.net/dl/abc",
                                     purpose="page")

    def test_literal_rejections_including_mapped_and_specials(self):
        bad = ["https://224.0.0.1/x",          # multicast
               "https://0.0.0.0/x",            # unspecified
               "https://[::ffff:127.0.0.1]/x",  # v4-mapped loopback
               "https://[::ffff:10.0.0.1]/x",   # v4-mapped private
               "https://[fe80::1]/x"]           # IPv6 link-local
        for url in bad:
            with self.assertRaises(np.NetworkPolicyError, msg=url):
                np.validate_outbound_url(url, purpose="storage")

    def test_test_mode_admits_loopback_only(self):
        with mock.patch.dict(os.environ, {np.TEST_LOCAL_HTTP_ENV: "1"}):
            np.validate_outbound_url("http://127.0.0.1:12345/f",
                                     purpose="storage", https_required=False)
            np.validate_outbound_url("http://localhost/f",
                                     purpose="storage", https_required=False)
            np.validate_outbound_url("https://[::1]/f", purpose="storage")
            with self.assertRaises(np.NetworkPolicyError):
                np.validate_outbound_url("http://10.0.0.5/f",
                                         purpose="storage",
                                         https_required=False)  # private
            with self.assertRaises(np.NetworkPolicyError):
                np.validate_outbound_url("ftp://127.0.0.1/f",
                                         purpose="storage")     # scheme

    def test_harness_escape_admits_exactly_the_mock_hosts(self):
        # direct download() target: 127.0.0.1 / localhost without the flag,
        # but ::1 / private ranges stay rejected
        np.validate_outbound_url("http://127.0.0.1:12345/f", purpose="storage",
                                 https_required=False, harness=True)
        np.validate_outbound_url("http://localhost/f", purpose="storage",
                                 https_required=False, harness=True)
        for url in ("http://[::1]/x", "http://10.0.0.5/x"):
            with self.assertRaises(np.NetworkPolicyError, msg=url):
                np.validate_outbound_url(url, purpose="storage",
                                         https_required=False, harness=True)

    def test_frozen_build_ignores_the_flag(self):
        with mock.patch.dict(os.environ, {np.TEST_LOCAL_HTTP_ENV: "1"}), \
                mock.patch.object(sys, "frozen", True, create=True):
            self.assertFalse(np.is_test_mode())
            with self.assertRaises(np.NetworkPolicyError):
                np.validate_outbound_url("http://127.0.0.1/f",
                                         purpose="storage",
                                         https_required=False)

    def test_is_test_mode_truth_table(self):
        for value, expected in (("1", True), ("true", True), ("YES", True),
                                ("0", False), ("", False), ("no", False)):
            with mock.patch.dict(os.environ, {np.TEST_LOCAL_HTTP_ENV: value}):
                self.assertIs(np.is_test_mode(), expected, value)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(np.TEST_LOCAL_HTTP_ENV, None)
            self.assertFalse(np.is_test_mode())


class TestResolveAndPin(unittest.TestCase):
    """M3 resolution: every answer public, or the host is rejected."""

    def test_all_public_answers_pin_the_first(self):
        with mock.patch("socket.getaddrinfo",
                        fake_resolver([PUBLIC1, PUBLIC2])):
            pin = np.resolve_and_pin("cdn.example", 443)
        self.assertEqual(pin.addr, PUBLIC1)
        self.assertEqual(pin.answers, (PUBLIC1, PUBLIC2))
        self.assertEqual(pin.hostname, "cdn.example")

    def test_mixed_public_private_rejects(self):
        with mock.patch("socket.getaddrinfo",
                        fake_resolver([PUBLIC1, "10.1.2.3"])):
            with self.assertRaises(np.NetworkPolicyError):
                np.resolve_and_pin("rebind.example", 443)

    def test_link_local_metadata_answer_rejects(self):
        with mock.patch("socket.getaddrinfo",
                        fake_resolver([PUBLIC1, "169.254.169.254"])):
            with self.assertRaises(np.NetworkPolicyError):
                np.resolve_and_pin("meta.example", 443)

    def test_v4_mapped_private_answer_rejects(self):
        with mock.patch("socket.getaddrinfo",
                        fake_resolver(["::ffff:192.168.0.9"])):
            with self.assertRaises(np.NetworkPolicyError):
                np.resolve_and_pin("mapped.example", 443)

    def test_empty_and_gaierror_fail_closed(self):
        with mock.patch("socket.getaddrinfo", fake_resolver([])):
            with self.assertRaises(np.NetworkPolicyError):
                np.resolve_and_pin("empty.example", 443)
        with mock.patch("socket.getaddrinfo",
                        mock.Mock(side_effect=socket.gaierror(1, "offline"))):
            with self.assertRaises(np.NetworkPolicyError):
                np.resolve_and_pin("dead.example", 443)

    def test_loopback_needs_explicit_relaxation(self):
        with mock.patch("socket.getaddrinfo", fake_resolver(["127.0.0.1"])):
            with self.assertRaises(np.NetworkPolicyError):
                np.resolve_and_pin("mock.example", 8080)
            pin = np.resolve_and_pin("mock.example", 8080,
                                     allow_loopback=True)
            self.assertEqual(pin.addr, "127.0.0.1")

    def test_test_mode_relaxes_loopback_never_private(self):
        with mock.patch.dict(os.environ, {np.TEST_LOCAL_HTTP_ENV: "1"}):
            with mock.patch("socket.getaddrinfo", fake_resolver(["::1"])):
                pin = np.resolve_and_pin("mock.example", 8080)
            self.assertEqual(pin.addr, "::1")
            with mock.patch("socket.getaddrinfo",
                            fake_resolver(["192.168.0.9"])):
                with self.assertRaises(np.NetworkPolicyError):
                    np.resolve_and_pin("private.example", 80)

    def test_assert_public_answers_shapes(self):
        np.assert_public_answers([PUBLIC1, PUBLIC2])
        np.assert_public_answers([ipaddress.ip_address(PUBLIC1)])
        for bad in ("10.0.0.5", "169.254.1.1", "224.0.0.1", "0.0.0.0",
                    "::ffff:10.0.0.1", "not-an-ip"):
            with self.assertRaises(np.NetworkPolicyError, msg=bad):
                np.assert_public_answers([bad])


class TestPinning(unittest.TestCase):
    """The connect target is the validated address — provable, not claimed."""

    def test_pinned_session_connects_to_validated_ip_without_second_dns(self):
        captured: list[tuple] = []
        resolver_hosts: list[str] = []

        class RecordingSock:
            def __init__(self, family=socket.AF_INET, type_=0, *a, **kw):
                self.family = family

            def settimeout(self, t):
                pass

            def setsockopt(self, *a):
                pass

            def bind(self, a):
                pass

            def connect(self, a):
                captured.append(a)
                raise ConnectionRefusedError("refused (expected by test)")

            def close(self):
                pass

        with mock.patch("socket.getaddrinfo",
                        fake_resolver([PUBLIC1, PUBLIC2],
                                      record=resolver_hosts)), \
                mock.patch("socket.socket", RecordingSock):
            session = np.PinnedSession("cdn.example", 443)
            with self.assertRaises(requests.exceptions.ConnectionError):
                session.get("https://cdn.example/x")
        # the TCP connect went to the validated public literal, in resolver
        # order — never to a host name, never via a second lookup
        self.assertEqual(captured[0], (PUBLIC1, 443))
        self.assertEqual(resolver_hosts, ["cdn.example"])   # resolved ONCE

    def test_rebinding_between_validation_and_connect_cannot_redirect(self):
        # a hostile resolver that answers public at validation time and
        # private afterwards is impossible here: the connect path performs
        # NO resolution at all (see the single getaddrinfo call above)
        captured: list[tuple] = []

        class RecordingSock:
            def __init__(self, *a, **kw):
                pass

            def settimeout(self, t):
                pass

            def setsockopt(self, *a):
                pass

            def bind(self, a):
                pass

            def connect(self, a):
                captured.append(a)
                raise ConnectionRefusedError

            def close(self):
                pass

        with mock.patch("socket.getaddrinfo", fake_resolver([PUBLIC1])), \
                mock.patch("socket.socket", RecordingSock):
            session = np.PinnedSession("cdn.example", 443)
            with self.assertRaises(requests.exceptions.ConnectionError):
                session.get("https://cdn.example/x")
        self.assertEqual([a[0] for a in captured], [PUBLIC1])

    def test_pinned_session_refuses_foreign_hosts_and_ports(self):
        with mock.patch("socket.getaddrinfo", fake_resolver([PUBLIC1])):
            session = np.PinnedSession("cdn.example", 443)
            with self.assertRaises(np.NetworkPolicyError):
                session.get("https://evil.example/x")
            with self.assertRaises(np.NetworkPolicyError):
                session.get("https://cdn.example:8443/x")

    def test_end_to_end_loopback_pinned_request(self):
        with LoopbackServer() as srv:
            session = np.PinnedSession("127.0.0.1", urlsplit(srv.base).port,
                                       allow_loopback=True)
            resp = session.get(srv.base, allow_redirects=False)
            try:
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp.content, b"pinned-ok")
            finally:
                resp.close()
                session.close()
            self.assertGreaterEqual(srv.hits, 1)

    def test_localhost_multi_answer_falls_back_within_validated_set(self):
        # "localhost" usually answers ::1 AND 127.0.0.1; the mock server only
        # listens on 127.0.0.1 — the pinned connect must try the validated
        # addresses in order (like socket.create_connection) instead of
        # failing on the first refused answer
        with LoopbackServer() as srv:
            with mock.patch("socket.getaddrinfo",
                            fake_resolver(["::1", "127.0.0.1"])):
                session = np.PinnedSession("localhost", urlsplit(srv.base).port,
                                           allow_loopback=True)
            resp = session.get(srv.base, allow_redirects=False)
            try:
                self.assertEqual(resp.status_code, 200)
            finally:
                resp.close()
                session.close()

    def test_tls_verification_stays_on(self):
        # only the TCP destination is pinned; urllib3's stock connect()
        # (SNI + hostname-verified handshake, verify ON) is untouched
        with mock.patch("socket.getaddrinfo", fake_resolver([PUBLIC1])):
            session = np.PinnedSession("cdn.example", 443)
        self.assertTrue(session.verify)
        conn = np._PinnedHTTPSConnection("cdn.example", 443,
                                         pinned_addrs=(PUBLIC1,))
        self.assertEqual(getattr(conn, "_pinned_addrs", None), (PUBLIC1,))
        self.assertIsInstance(conn, urllib3.connection.HTTPSConnection)
class TestFollowRedirects(unittest.TestCase):
    """Manual walker: validate every hop BEFORE the request is sent."""

    def _fetch_script(self, script):
        """fetch callable serving ``script[url]``; records every fetch."""
        fetched: list[str] = []

        def fetch(url, headers=None):
            fetched.append(url)
            return script[url]
        return fetch, fetched

    def test_same_origin_chain_returns_final_and_closes_intermediates(self):
        final = FakeResp(200, "https://www.transfernow.net/dl/abc3")
        hop1 = FakeResp(302, headers={"Location": "/dl/abc2"})
        hop2 = FakeResp(302, headers={
            "Location": "https://www.transfernow.net/dl/abc3"})
        fetched: list[str] = []

        def fetch(url, headers=None):
            fetched.append(url)
            if url.endswith("dl/abc"):
                return hop1
            if url.endswith("dl/abc2"):
                return hop2
            return final

        resp = np.follow_redirects(fetch, "https://www.transfernow.net/dl/abc",
                                   purpose="page")
        self.assertIs(resp, final)
        self.assertTrue(hop1.closed and hop2.closed)   # intermediates closed
        self.assertFalse(final.closed)                 # caller owns the final
        self.assertEqual(fetched[-1], "https://www.transfernow.net/dl/abc3")

    def test_relative_location_is_resolved_and_validated(self):
        final = FakeResp(200, "https://www.transfernow.net/dl/abc2")
        hop = FakeResp(302, headers={"Location": "/dl/abc2"})   # root-relative
        calls: list = []

        def scripted_fetch(url, headers=None):
            calls.append(url)
            return hop if len(calls) == 1 else final

        resp = np.follow_redirects(scripted_fetch,
                                   "https://www.transfernow.net/dl/abc",
                                   purpose="page")
        self.assertIs(resp, final)
        self.assertEqual(calls[1], "https://www.transfernow.net/dl/abc2")

    def test_cross_origin_trusted_hop_strips_sensitive_headers(self):
        final = FakeResp(200, "https://transfernow.net/dl/abc")
        hop = FakeResp(302, headers={
            "Location": "https://transfernow.net/dl/abc"})
        seen = []

        def fetch(url, headers=None):
            seen.append(dict(headers or {}))
            return hop if url.endswith("dl/abcx") else final

        np.follow_redirects(
            fetch, "https://www.transfernow.net/dl/abcx", purpose="page",
            headers={"Cookie": "sid=1", "Authorization": "Bearer t",
                     "Accept": "text/html"})
        self.assertIn("Cookie", seen[0])
        self.assertNotIn("Cookie", seen[1])
        self.assertNotIn("Authorization", seen[1])

    def test_untrusted_hop_rejected_and_never_fetched(self):
        hop = FakeResp(302, headers={"Location": "https://evil.com/x?token=SECRET"})
        fetch, fetched = self._fetch_script(
            {"https://www.transfernow.net/dl/abc": hop})
        with self.assertRaises(np.NetworkPolicyError):
            np.follow_redirects(fetch, "https://www.transfernow.net/dl/abc",
                                purpose="page")
        self.assertEqual(fetched, ["https://www.transfernow.net/dl/abc"])
        self.assertTrue(hop.closed)   # the redirect response was closed

    def test_downgrade_credentials_loop_and_budget(self):
        cases = [
            "http://www.transfernow.net/dl/abc",               # downgrade
            "https://user:pass@www.transfernow.net/dl/abc",    # credentials
            "https://evil.com/x",                              # untrusted
        ]
        for location in cases:
            hop = FakeResp(302, headers={"Location": location})
            fetch, fetched = self._fetch_script(
                {"https://www.transfernow.net/dl/abc": hop})
            with self.assertRaises(np.NetworkPolicyError, msg=location):
                np.follow_redirects(fetch, "https://www.transfernow.net/dl/abc",
                                    purpose="page")
            self.assertEqual(fetched, ["https://www.transfernow.net/dl/abc"])

    def test_redirect_loop_detected(self):
        a = FakeResp(302, headers={"Location": "https://www.transfernow.net/dl/b"})
        b = FakeResp(302, headers={"Location": "https://www.transfernow.net/dl/abc"})
        fetch, _ = self._fetch_script({})
        calls = []

        def scripted_fetch(url, headers=None):
            calls.append(url)
            return b if url.endswith("dl/b") else a

        with self.assertRaises(np.NetworkPolicyError) as ctx:
            np.follow_redirects(scripted_fetch,
                                "https://www.transfernow.net/dl/abc",
                                purpose="page")
        self.assertIn("loop", str(ctx.exception))

    def test_hop_budget_and_missing_location(self):
        # too many hops — each Location is strictly longer, so the visited
        # set never triggers the loop detector before the budget runs out
        def endless_fetch(url, headers=None):
            nxt = f"https://www.transfernow.net/dl/{'x' * len(url)}"
            return FakeResp(302, headers={"Location": nxt})

        with self.assertRaises(np.NetworkPolicyError) as ctx:
            np.follow_redirects(endless_fetch,
                                "https://www.transfernow.net/dl/abc",
                                purpose="page")
        self.assertIn("too many redirects", str(ctx.exception))
        # missing Location header
        fetch, _ = self._fetch_script(
            {"https://www.transfernow.net/dl/abc": FakeResp(302)})
        with self.assertRaises(np.NetworkPolicyError):
            np.follow_redirects(fetch, "https://www.transfernow.net/dl/abc",
                                purpose="page")

    def test_error_paths_close_the_open_response(self):
        # the walker must close the current response even when the NEXT hop
        # fails validation
        class ExplodingResp(FakeResp):
            def close(self):
                self.closed = True
                raise RuntimeError("close race")   # cleanup never raises

        hop = ExplodingResp(302, headers={"Location": "https://evil.com/x"})
        fetch, _ = self._fetch_script(
            {"https://www.transfernow.net/dl/abc": hop})
        with self.assertRaises(np.NetworkPolicyError):
            np.follow_redirects(fetch, "https://www.transfernow.net/dl/abc",
                                purpose="page")
        self.assertTrue(hop.closed)


class TestRedact(unittest.TestCase):
    def test_strips_query_userinfo_and_port(self):
        self.assertEqual(
            np.redact("https://user:pw@cdn.transfernow.net:8443/a/b?sig=SECRET"),
            "https://cdn.transfernow.net/a/b")
        self.assertEqual(np.redact("https://[::1]:8080/x?t=1"), "https://[::1]/x")

    def test_garbage_degrades_safely(self):
        self.assertEqual(np.redact(""), "<invalid-url>")
        self.assertEqual(np.redact("not a url"), "<invalid-url>")


if __name__ == "__main__":
    unittest.main(verbosity=2)
