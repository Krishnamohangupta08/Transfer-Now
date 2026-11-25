"""Security tests for core.linkcheck — safe redirect handling for link
health checks.

    python -m unittest tests.test_linkcheck_security -v

The transport is a recorded fake (``lc.requests.get`` is patched): every
URL the checker *tried* to contact is captured, so each redirect-security
assertion can prove the negative the release requires — **no request is
ever made to a rejected destination** (the fetch log must show the initial
page only).  No network, no DNS: the checker's own policy validation runs
before the first request, and every hop's ``Location`` is validated before
the next one.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import linkcheck as lc  # noqa: E402

BASE = "https://www.transfernow.net/dl/abc123xyz"


class FakeResp:
    """Minimal requests.Response stand-in with close tracking."""

    def __init__(self, status_code=200, url=BASE, headers=None, html=b""):
        self.status_code = status_code
        self.url = url
        self.headers = headers or {}
        self._html = html if isinstance(html, bytes) else html.encode()
        self.closed = False

    def iter_content(self, chunk_size=None):
        yield self._html

    def close(self):
        self.closed = True


ACTIVE_HTML = (b'<html><script>"validity":{"from":"2026-01-01T00:00:00Z",'
               b'"to":"2099-01-01T00:00:00Z"}"files":[{"name":"a","size":1}]'
               b'Files are ready</script></html>')
EXPIRED_HTML = (b'<html><script>"validity":{"from":"2020-01-01T00:00:00Z",'
                b'"to":"2021-01-01T00:00:00Z"}"files":[{"name":"a","size":1}]'
                b"</script></html>")
GONE_HTML = b"<html><body>This transfer has expired</body></html>"


class ScriptedTransport:
    """Patchable ``requests.get`` replacement; records every URL fetched."""

    def __init__(self, script):
        self.script = dict(script)      # url-prefix -> FakeResp (first match)
        self.fetched: list[str] = []

    def __call__(self, url, headers=None, timeout=None, stream=False,
                 allow_redirects=True):
        self.fetched.append(url)
        for prefix, resp in self.script.items():
            if url.startswith(prefix):
                return resp
        raise AssertionError(f"unexpected fetch target: {url}")

    def assert_only_base(self, testcase):
        testcase.assertEqual(self.fetched, [BASE],
                             "a request reached a rejected destination")


class TestLinkcheckPolicy(unittest.TestCase):
    """Redirect policy: every hop validated BEFORE its request is sent."""

    def _check(self, transport, **kw):
        with mock.patch.object(lc.requests, "get", transport):
            return lc.check_link_detail(BASE, retries=0, **kw)

    def test_not_a_transfernow_link_never_requests(self):
        transport = ScriptedTransport({})
        with mock.patch.object(lc.requests, "get", transport):
            result = lc.check_link_detail("https://evil.com/dl/abc", retries=0)
        self.assertEqual(result["state"], "unknown")
        self.assertEqual(result["http"], 0)
        self.assertEqual(transport.fetched, [])          # no request at all

    def test_plain_page_is_active(self):
        transport = ScriptedTransport({BASE: FakeResp(200, html=ACTIVE_HTML)})
        result = self._check(transport)
        self.assertEqual(result["state"], "active")
        self.assertEqual(result["http"], 200)

    def test_expired_pages(self):
        transport = ScriptedTransport({BASE: FakeResp(200, html=EXPIRED_HTML)})
        self.assertEqual(self._check(transport)["state"], "expired")
        transport = ScriptedTransport({BASE: FakeResp(404)})
        self.assertEqual(self._check(transport)["state"], "expired")

    def test_gone_page_is_expired_blocked_page_is_unknown(self):
        transport = ScriptedTransport({BASE: FakeResp(200, html=GONE_HTML)})
        self.assertEqual(self._check(transport)["state"], "expired")
        transport = ScriptedTransport(
            {BASE: FakeResp(200, html="<html>Just a moment…</html>")})
        self.assertEqual(self._check(transport)["state"], "unknown")

    def test_5xx_is_unknown_not_expired(self):
        transport = ScriptedTransport({BASE: FakeResp(503)})
        self.assertEqual(self._check(transport)["state"], "unknown")

    def test_redirect_to_trusted_same_host_is_followed(self):
        nxt = "https://www.transfernow.net/en/d/abc123xyz"
        transport = ScriptedTransport({
            BASE: FakeResp(302, headers={"Location": nxt}),
            nxt: FakeResp(200, url=nxt, html=ACTIVE_HTML),
        })
        with mock.patch.object(lc.requests, "get", transport):
            result = lc.check_link_detail(BASE, retries=0)
        self.assertEqual(result["state"], "active")
        self.assertEqual(transport.fetched, [BASE, nxt])

    def test_redirect_to_untrusted_host_rejected_before_request(self):
        transport = ScriptedTransport({
            BASE: FakeResp(302, headers={
                "Location": "https://evil.com/payload?token=SECRET"}),
        })
        result = self._check(transport)
        self.assertEqual(result["state"], "unknown")
        self.assertEqual(result["reason"], "NetworkPolicyError")
        # THE proof: the rejected destination was never contacted
        transport.assert_only_base(self)

    def test_downgrade_credentials_and_private_hops_rejected(self):
        for location in ("http://www.transfernow.net/en/d/abc123xyz",   # http
                         "https://user:pass@www.transfernow.net/dl/x",  # creds
                         "http://127.0.0.1:9/x",                        # loopback
                         "http://10.9.9.9/x",                           # private
                         "https://169.254.169.254/latest/meta-data"):   # cloud
            transport = ScriptedTransport({
                BASE: FakeResp(302, headers={"Location": location})})
            result = self._check(transport)
            self.assertEqual(result["reason"], "NetworkPolicyError", location)
            transport.assert_only_base(self)

    def test_signed_query_params_never_leak_into_results(self):
        transport = ScriptedTransport({
            BASE: FakeResp(302, headers={
                "Location": "https://evil.com/x?token=SECRET123"}),
        })
        result = self._check(transport)
        self.assertNotIn("SECRET123", str(result))
        self.assertNotIn("evil.com", str(result))

    def test_redirect_loop_detected(self):
        b = "https://www.transfernow.net/en/d/abc123xyz"
        transport = ScriptedTransport({
            BASE: FakeResp(302, headers={"Location": b}),
            b: FakeResp(302, headers={"Location": BASE}),
        })
        result = self._check(transport)
        self.assertEqual(result["reason"], "NetworkPolicyError")
        # the loop was caught after one round trip: only trusted same-host
        # pages were contacted, no rejected destination ever
        self.assertEqual(transport.fetched, [BASE, b])

    def test_missing_location_rejected(self):
        transport = ScriptedTransport({BASE: FakeResp(302)})
        result = self._check(transport)
        self.assertEqual(result["reason"], "NetworkPolicyError")
        transport.assert_only_base(self)

    def test_malformed_location_rejected(self):
        transport = ScriptedTransport({
            BASE: FakeResp(302, headers={"Location": "http://[::1"})})
        result = self._check(transport)
        self.assertEqual(result["reason"], "NetworkPolicyError")
        transport.assert_only_base(self)

    def test_responses_closed_on_all_paths(self):
        # intermediate (redirect) and final responses must both be closed
        nxt = "https://www.transfernow.net/en/d/abc123xyz"
        hop = FakeResp(302, headers={"Location": nxt})
        final = FakeResp(200, url=nxt, html=ACTIVE_HTML)
        transport = ScriptedTransport({BASE: hop, nxt: final})
        self._check(transport)
        self.assertTrue(hop.closed and final.closed)
        # error path: a redirect to a rejected destination still closes the hop
        hop = FakeResp(302, headers={"Location": "https://evil.com/x"})
        transport = ScriptedTransport({BASE: hop})
        self._check(transport)
        self.assertTrue(hop.closed)

    def test_transport_failure_is_unknown_with_class_name_only(self):
        def dead(url, headers=None, timeout=None, stream=False,
                 allow_redirects=True):
            raise lc.requests.exceptions.ConnectTimeout("no route to host")

        with mock.patch.object(lc.requests, "get", dead):
            result = lc.check_link_detail(BASE, retries=0)
        self.assertEqual(result["state"], "unknown")
        self.assertEqual(result["reason"], "ConnectTimeout")

    def test_check_link_returns_state_only(self):
        transport = ScriptedTransport({BASE: FakeResp(200, html=ACTIVE_HTML)})
        with mock.patch.object(lc.requests, "get", transport):
            self.assertEqual(lc.check_link(BASE), "active")

    def test_normalize_accepts_official_forms_only(self):
        self.assertEqual(lc.normalize_link("abc123xyz"), BASE)
        self.assertEqual(
            lc.normalize_link("https://www.transfernow.net/en/d/abc123xyz/"),
            BASE)
        self.assertIsNone(lc.normalize_link("https://evil.com/?u=transfernow.net/dl/abc"))
        self.assertIsNone(lc.normalize_link("http://www.transfernow.net/dl/abc123xyz"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
