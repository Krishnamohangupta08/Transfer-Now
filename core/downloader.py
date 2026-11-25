"""TransferNow link fetcher + streaming downloader — pure ``requests``.

Validated against the live site (see analysis/analyze_download2.py):

  1. ``GET https://www.transfernow.net/dl/<id>``
     Server-rendered page.  The embedded Next.js payload contains
     ``"validity":{"from":…,"to":…}`` and the ``"files":[…]`` array;
     span-based parsing is kept as a fallback.
  2. ``GET /api/transfer/downloads/link?transferId=<id>&preview=false``
     -> ``{"url": "<signed storage URL>"}`` — works with a plain browser
     User-Agent, no cookies / browser needed.
  3. ``GET <storage URL>``  — stream the file with browser-like headers
     (the storage host enforces Referer hotlink protection: without it,
     even valid signed URLs return 404 — verified live).  Transient
     failures are retried with backoff, re-resolving the signed URL.

Security model (v1.1 hardening):

  * Share links are parsed with :func:`urllib.parse.urlsplit` and matched
    against :data:`ALLOWED_PAGE_HOSTS` — the id comes from the URL *path*,
    never from a substring search, and lookalike hosts / credentials /
    ports / non-https schemes are rejected.
  * Signed storage URLs returned by the API must pass
    :func:`_assert_public_https`: https, an allowlisted storage host (or any
    ``*.transfernow.net`` subdomain — the real CDN hostname is not stable /
    documented, hence the family escape hatch), no credentials or port, and
    the host must resolve to public, non-private addresses only (a
    ``getaddrinfo`` check on every resolved address defeats DNS rebinding).
  * ``download()`` follows redirects manually (max 5 hops), re-validating
    each hop's target; cross-origin hops drop the Referer and the Range
    header.  Mid-stream resumes verify ``Content-Range`` and send
    ``If-Range`` so a changed entity restarts cleanly instead of corrupting
    the file.
  * This module never prints or logs URLs — signed URLs must stay out of
    any diagnostics.

Test escape hatch: setting the environment variable ``TN_TEST_LOCAL_HTTP``
to ``1`` relaxes the destination policies so the offline test-suite can use
plain ``http://127.0.0.1`` / ``localhost`` mock servers.  It is read at call
time and is NEVER set by the shipped application.  ``download()`` keeps
``127.0.0.1`` / ``localhost`` reachable even without the flag (the URL it
receives originates from ``resolve_download_url``, which already enforced
the strict allowlist — the offline test harnesses rely on loopback, and the
app itself never produces such a URL), but any other private / loopback /
link-local destination stays rejected.
"""
from __future__ import annotations

import errno
import ipaddress
import json
import os
import re
import shutil
import socket
import time
from collections.abc import Callable
from datetime import datetime
from urllib.parse import parse_qs, unquote, urljoin, urlparse, urlsplit

import requests

from . import netpolicy
from .netpolicy import (ALLOWED_PAGE_HOSTS, ALLOWED_STORAGE_HOSTS,  # noqa: F401
                        TEST_LOCAL_HTTP_ENV, is_test_mode)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}

# The storage host enforces hotlink protection: direct GETs without a
# transfernow.net Referer return 404 (verified live).  These browser-like
# headers make the signed storage URL downloadable directly.
STORAGE_HEADERS = {
    "User-Agent": UA,
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.transfernow.net/en/d/start",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-site",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# Share pages live exactly here (no subdomain tricks — "www2." and friends
# are rejected by parse_transfer_id).  Canonical home: core.netpolicy
# (re-exported above for backwards compatibility).
# Signed storage URLs the downloads API is known to return.  The real CDN
# hostname is not documented and may change, so _assert_public_https also
# accepts any https host under the transfernow.net family as an explicitly
# documented escape hatch; everything else (other domains, http, literal
# IPs, private/loopback ranges) is rejected.
TN_FAMILY_SUFFIX = netpolicy.TN_FAMILY_SUFFIX

# Test-mode escape hatch (offline test-suite only): when truthy
# ("1"/"true"/"yes"), the URL policies accept http:// and loopback/private
# destinations so tests can hit local mock servers.  Read at call time;
# never set by the app itself.  Frozen builds ignore it entirely.
TEST_LOCAL_HTTP_ENV = netpolicy.TEST_LOCAL_HTTP_ENV
LOCAL_TEST_HOSTS = {"127.0.0.1", "localhost"}

PAGE_PATH_RE = re.compile(
    r"^/(?:(?P<lang>[a-z]{2}(?:-[A-Za-z]{2})?)/)?(?:d|dl)/(?P<tid>[A-Za-z0-9-]{6,})/?$",
    re.IGNORECASE)

TITLE_RE = re.compile(r"downloader_title__[^>]*>(?:<[^>]+>)*([^<]+)")
FILE_NAME_RE = re.compile(r'list_filename__[^"]*"\s+title="([^"]+)"')
FILE_SIZE_RE = re.compile(r'list_filesize__[^"]*">([\d.,]+)\s*([KMG]B)\s*<')
VALIDITY_RE = re.compile(r'"validity":\{"from":"([^"]+)","to":"([^"]+)"\}')

# retry backoff (seconds) — safety net for transient storage/API hiccups
RETRY_DELAYS = [2, 3, 5, 8, 12, 15, 20, 25]

# mid-stream resume backoff (seconds): delay before resume attempt N.
# Tests patch this (together with OPEN_TIMEOUTS) to keep offline runs fast.
RESUME_DELAYS = [2, 4, 6, 8, 8, 8]

# (connect, read) timeouts for every storage request.  The read timeout
# bounds a stalled stream — a dead peer surfaces within ~15 s, so user
# cancel/pause never stays stuck longer than that per blocked read.
OPEN_TIMEOUTS = (10, 15)

MAX_REDIRECTS = 5          # manual redirect budget inside download()
MAX_PAGE_BYTES = 5 << 20   # fetch_info page body cap (5 MiB)

UNSAFE_URL_MSG = "The server returned an unsafe download URL"
REDIRECT_UNSAFE_MSG = "The download server redirected to an unsafe address."


class TransferLinkError(RuntimeError):
    """A human-readable problem with the link (invalid / expired / failed)."""


class DownloadCancelled(RuntimeError):
    """Raised when the user cancels a running download."""


def _test_local_http() -> bool:
    """True only when the offline-test escape hatch is explicitly enabled."""
    return netpolicy.is_test_mode()


def parse_transfer_id(link: str) -> str | None:
    """'https://www.transfernow.net/dl/abc123' -> 'abc123'.

    The id is taken from the parsed URL *path* — never from a substring
    search — so ``https://evil.com/?u=transfernow.net/dl/xxx`` does not
    match.  Canonical and locale page URLs on the official hosts (https,
    no port, no credentials) and bare ids are accepted; everything else
    returns None.
    """
    link = (link or "").strip()
    if not link:
        return None
    if re.fullmatch(r"[A-Za-z0-9-]{6,}", link):  # bare id
        return link
    candidate = link
    # scheme-less pastes ("transfernow.net/dl/…", "www.transfernow.net/d/…")
    if re.match(r"^(?:www\.)?transfernow\.net/", candidate, re.IGNORECASE):
        candidate = "https://" + candidate
    try:
        parts = urlsplit(candidate)
    except ValueError:
        return None
    if parts.scheme.lower() != "https":
        return None
    if (parts.hostname or "").lower() not in ALLOWED_PAGE_HOSTS:
        return None
    if parts.port is not None or parts.username or parts.password:
        return None
    m = PAGE_PATH_RE.match(parts.path or "")
    return m.group("tid") if m else None


def _resolve_host_ips(host: str) -> list:
    """All IPv4/IPv6 addresses ``host`` resolves to (patchable in tests).

    Thin re-export shim over :func:`netpolicy.resolve_and_pin` so existing
    tests that patch ``dl._resolve_host_ips`` keep steering resolution.
    """
    try:
        return [ipaddress.ip_address(a) for a in
                netpolicy.resolve_and_pin(host, 443).answers]
    except netpolicy.NetworkPolicyError as exc:
        raise socket.gaierror(1, str(exc)) from exc


def _assert_resolved_public(host: str) -> None:
    """Fail closed: the host must resolve, and every address must be public.

    Resolution goes through :func:`_resolve_host_ips` — the patchable seam
    the offline tests steer — and the returned addresses are judged by
    :func:`netpolicy.assert_public_answers` (rebinding guard).
    """
    try:
        ips = _resolve_host_ips(host)
    except (socket.gaierror, OSError) as exc:
        raise TransferLinkError(f"{UNSAFE_URL_MSG} "
                                "(private or unresolvable address).") from exc
    try:
        netpolicy.assert_public_answers(ips)
    except netpolicy.NetworkPolicyError as exc:
        raise TransferLinkError(f"{UNSAFE_URL_MSG} "
                                "(private or unresolvable address).") from exc


def _assert_public_https(url: str) -> None:
    """Strict policy for signed storage URLs produced by ``resolve_download_url``.

    Delegates to :func:`netpolicy.validate_outbound_url` (purpose="storage"):
    https (or test-mode http on loopback), an allowlisted storage host (or
    any ``*.transfernow.net`` subdomain), no embedded credentials, no
    unexpected port, and public-only resolved addresses.  The URL itself is
    never included in error messages (signed URLs must not leak).
    """
    try:
        target = netpolicy.validate_outbound_url(url, purpose="storage")
    except netpolicy.NetworkPolicyError as exc:
        raise TransferLinkError(f"{UNSAFE_URL_MSG} ({exc}).") from exc
    if target.hostname and target.hostname not in LOCAL_TEST_HOSTS \
            and not target.hostname.endswith(".localhost"):
        _assert_resolved_public(target.hostname)


def _assert_stream_target(url: str, *, https_required: bool = False,
                          resolve_named: bool = False,
                          harness: bool = True) -> None:
    """Safety net for URLs handed to :func:`download` and for redirect hops.

    Delegates to :func:`netpolicy.validate_outbound_url` (purpose="storage",
    https_required as given) — the same single source of truth — and
    additionally resolves named hosts when ``resolve_named`` is set.

    ``harness`` defaults to True: the *direct* target of :func:`download`
    keeps ``127.0.0.1`` / ``localhost`` reachable without the flag (the URL
    it receives originates from ``resolve_download_url``, which already
    enforced the strict allowlist — the offline test harnesses rely on
    loopback, and the app itself never produces such a URL).  Redirect hops
    pass ``harness=False`` explicitly, so a server-chosen loopback target
    still needs the explicit ``TN_TEST_LOCAL_HTTP`` flag.
    """
    try:
        target = netpolicy.validate_outbound_url(
            url, purpose="storage", https_required=https_required,
            harness=harness)
    except netpolicy.NetworkPolicyError as exc:
        raise TransferLinkError(REDIRECT_UNSAFE_MSG) from exc
    if resolve_named and target.hostname \
            and target.hostname not in LOCAL_TEST_HOSTS \
            and not target.hostname.endswith(".localhost"):
        _assert_resolved_public(target.hostname)


def _size_to_bytes(value: str, unit: str) -> int:
    mult = {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3}.get(unit.upper(), 1)
    try:
        return int(float(value.replace(",", ".")) * mult)
    except ValueError:
        return 0


def _matching_bracket(s: str, start: int, open_c: str = "[", close_c: str = "]") -> int:
    """Index of the closing bracket matching ``s[start]`` (string-aware)."""
    depth, in_str, esc = 0, False, False
    for k in range(start, len(s)):
        c = s[k]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == open_c:
            depth += 1
        elif c == close_c:
            depth -= 1
            if depth == 0:
                return k
    return -1


def _rsc_payload(html: str) -> tuple[list | None, str | None]:
    """Extract (files, validity_to) from the embedded Next.js payload."""
    m = VALIDITY_RE.search(html)
    if not m:
        return None, None
    i = html.find('"files":[', m.end())
    if i < 0:
        return None, None
    j = _matching_bracket(html, i + len('"files":'))
    if j < 0:
        return None, None
    try:
        arr = json.loads(html[i + len('"files":'):j + 1])
    except ValueError:
        return None, None
    if not isinstance(arr, list):
        return None, None
    files = [{"name": _display_safe(f.get("name", "file")), "size": int(f.get("size", 0))}
             for f in arr if isinstance(f, dict) and f.get("name")]
    return (files or None), _display_safe(m.group(2))


def _display_safe(text):
    """Neutralize rich-text metacharacters in remote strings.

    Qt labels/tooltips re-run the AutoText rich-text heuristic even on
    entity-encoded text, so remote titles/file names must never carry
    ``<``/``>``/``&`` (an attacker-supplied ``<img src=...>`` would make the
    victim's Qt fetch remote content). Returns ``None`` unchanged.
    """
    if not text:
        return text
    return text.replace("<", "").replace(">", "").replace("&", "")


def _fmt_expiry(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%b %d, %Y")
    except ValueError:
        return ""


def _read_capped(resp, limit: int) -> str:
    """Read a text response with a hard byte cap (page bodies must stay sane)."""
    buf = bytearray()
    try:
        for data in resp.iter_content(chunk_size=1 << 16):
            buf += data
            if len(buf) > limit:
                raise TransferLinkError(
                    "The transfer page is unexpectedly large — aborted.")
    except requests.RequestException as exc:
        raise TransferLinkError(
            f"Could not read the transfer page ({exc.__class__.__name__}).") from exc
    return bytes(buf).decode("utf-8", "replace")


def _close_quietly(resp) -> None:
    """Close a half-dead response without masking the real error."""
    try:
        resp.close()
    except Exception:  # noqa: BLE001, S110 — cleanup must never raise
        pass


def fetch_info(link: str, timeout: int = 20) -> dict:
    """Fetch transfer metadata for a share link (no browser required)."""
    tid = parse_transfer_id(link)
    if not tid:
        raise TransferLinkError(
            "That doesn't look like a TransferNow link "
            "(expected https://www.transfernow.net/dl/…).")
    url = f"https://www.transfernow.net/dl/{tid}"
    # M1: validate before any socket (SSRF guard), then walk redirects
    # manually with per-hop validation — no implicit redirect following, so
    # a redirect can never be *sent* to an unvalidated destination.
    try:
        netpolicy.validate_outbound_url(url, purpose="page")
    except netpolicy.NetworkPolicyError as exc:
        raise TransferLinkError(
            "That doesn't look like a TransferNow link "
            f"({exc}).") from exc
    try:
        resp = netpolicy.follow_redirects(
            lambda u, h=None: requests.get(
                u, headers=h if h is not None else HEADERS,
                timeout=timeout, stream=True, allow_redirects=False),
            url, purpose="page", headers=HEADERS)
    except requests.RequestException as exc:
        raise TransferLinkError(f"Could not reach transfernow.net ({exc.__class__.__name__}).") from exc
    except netpolicy.NetworkPolicyError as exc:
        raise TransferLinkError(
            "This link redirects away from transfernow.net — it is not a valid transfer page.") from exc
    try:
        if resp.status_code != 200:
            raise TransferLinkError(f"TransferNow returned HTTP {resp.status_code} for this link.")
        # after redirects the page must still sit on an official host (a captive
        # portal or phishing redirect must not be parsed as a transfer page)
        if (urlsplit(resp.url).hostname or "").lower() not in ALLOWED_PAGE_HOSTS:
            raise TransferLinkError(
                "This link redirects away from transfernow.net — it is not a valid transfer page.")
        try:
            html = _read_capped(resp, MAX_PAGE_BYTES)
        finally:
            _close_quietly(resp)
    finally:
        _close_quietly(resp)
    html_nc = re.sub(r"<!--.*?-->", "", html)  # SPA hydration comments

    title_m = TITLE_RE.search(html_nc)
    # M1 residual (A4-07): remote strings are stripped of rich-text
    # metacharacters on EVERY parse path, not just the RSC payload
    title = _display_safe((title_m.group(1).strip() if title_m else "").strip())

    files, validity_to = _rsc_payload(html)
    expiry = _fmt_expiry(validity_to or "")
    if not files:  # fallback: parse the rendered file rows
        names = [_display_safe(unquote(n)) for n in FILE_NAME_RE.findall(html_nc)]
        sizes = [( _size_to_bytes(v, u), u) for v, u in FILE_SIZE_RE.findall(html_nc)]
        files = [{"name": n, "size": sizes[i][0] if i < len(sizes) else 0}
                 for i, n in enumerate(names)]
    if not expiry:
        expiry_m = re.search(r'downloader_expiry_date__[^"]*"[^>]*>\s*'
                             r'(?:</svg>)?\s*([^<]*)', html_nc)
        expiry = (expiry_m.group(1).strip() if expiry_m else "").strip().removeprefix("Expires on").strip()

    ready = "Files are ready" in html_nc
    if not ready and not files:
        message = title or "This link is invalid, expired, or the files were removed."
        raise TransferLinkError(message)
    if not files:  # page parsed oddly — still allow download
        files = [{"name": "transfer files", "size": 0}]
    return {
        "transfer_id": tid,
        "link": url,
        "title": title or "Files are ready!",
        "ready": ready,
        "files": files,
        "total_bytes": sum(f["size"] for f in files),
        "expiry": expiry,
    }


def resolve_download_url(transfer_id: str, timeout: int = 20) -> str:
    """Ask TransferNow for the signed, direct storage URL of the transfer."""
    api = "https://www.transfernow.net/api/transfer/downloads/link"
    try:
        resp = requests.get(api, params={"transferId": transfer_id, "preview": "false"},
                            headers=HEADERS, timeout=timeout)
    except requests.RequestException as exc:
        raise TransferLinkError(f"Could not reach the download API ({exc.__class__.__name__}).") from exc
    if resp.status_code != 200:
        raise TransferLinkError(f"Download API returned HTTP {resp.status_code}.")
    try:
        data = resp.json()
    except ValueError as exc:
        raise TransferLinkError("Download API returned an unexpected response.") from exc
    # strict schema: a dict with a non-empty string "url" — nothing else
    if (not isinstance(data, dict) or not isinstance(data.get("url"), str)
            or not data["url"].strip()):
        raise TransferLinkError("Could not resolve a download URL for this link "
                                "(it may have expired).")
    url = data["url"].strip()
    # https + storage allowlist + public-only resolution via netpolicy; the
    # URL is never echoed into the error message
    _assert_public_https(url)
    return url


def _filename_from(url: str, content_disposition: str, transfer_id: str) -> str:
    cd = content_disposition or ""
    m = re.search(r"filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)", cd)
    if m:
        return _safe_name(unquote(m.group(1).strip().strip('"')))
    m = re.search(r'filename\s*=\s*"?([^";]+)"?', cd)
    if m:
        return _safe_name(unquote(m.group(1).strip()))
    qs = parse_qs(urlparse(url).query).get("fileName", [])
    if qs:
        return _safe_name(unquote(qs[0]))
    return f"TransferNow-{transfer_id}.zip"


_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}


def _safe_name(name: str) -> str:
    """Filesystem-safe file name; keeps Unicode, blocks path tricks."""
    name = os.path.basename(name.replace("\\", "/"))
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(" .")
    if name.split(".")[0].upper() in _RESERVED:
        name = "_" + name
    return name[:180] or "download.bin"


def _unique_path(folder: str, name: str) -> str:
    base, ext = os.path.splitext(name)
    path = os.path.join(folder, name)
    n = 1
    while os.path.exists(path) or os.path.exists(path + ".part"):
        path = os.path.join(folder, f"{base} ({n}){ext}")
        n += 1
    return path


def _claim_part(dest_dir: str, name: str) -> tuple[str, str, int]:
    """Choose a free destination name and create its ``.part`` exclusively.

    The ``.part`` is opened ``O_CREAT|O_EXCL`` so two concurrent downloads
    with the same suggested name can never interleave into one file; if the
    creation loses a race, the next free name is picked instead.  Returns
    ``(dest, part_path, fd)``.
    """
    dest = _unique_path(dest_dir, name)
    while True:
        part = dest + ".part"
        try:
            fd = os.open(part, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            dest = _unique_path(dest_dir, os.path.basename(dest))
            continue
        return dest, part, fd


def _sleep_interruptible(seconds: float, should_stop: Callable[[], bool] | None) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if should_stop and should_stop():
            raise DownloadCancelled()
        time.sleep(min(0.2, max(0.0, end - time.monotonic())))


_CONTENT_RANGE_RE = re.compile(r"^bytes\s+(\d+)-(\d+)/(\d+|\*)$", re.IGNORECASE)


def _parse_content_range(value: str) -> tuple[int, int, int] | None:
    """``'bytes N-M/T'`` -> ``(N, M, T)`` with ``T = -1`` for ``'*'``."""
    m = _CONTENT_RANGE_RE.match((value or "").strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)),
            -1 if m.group(3) == "*" else int(m.group(3)))


def _entity_tag(resp) -> str | None:
    """ETag (preferred) or Last-Modified — sent back as If-Range on resumes
    so a changed entity makes the server answer 200 (full fresh copy)."""
    return resp.headers.get("ETag") or resp.headers.get("Last-Modified") or None


def _redirect_headers(prev_url: str, new_url: str, start: int,
                      etag: str | None) -> tuple[dict, int]:
    """Headers for one redirect hop, and the byte offset actually sent.

    Referer is only sent when the hop stays on the same host — or lands on a
    transfernow.net-family host, whose hotlink protection expects it.  The
    Range/If-Range resume pair is only forwarded to the *same* host: a
    cross-origin hop never receives the byte offset, so the stream is
    treated as a fresh full copy and the resume integrity checks decide.
    """
    prev_host = (urlsplit(prev_url).hostname or "").lower()
    new_host = (urlsplit(new_url).hostname or "").lower()
    headers = {k: v for k, v in STORAGE_HEADERS.items() if k != "Referer"}
    if new_host == prev_host or new_host.endswith(TN_FAMILY_SUFFIX):
        headers["Referer"] = STORAGE_HEADERS["Referer"]
    sent_start = start if new_host == prev_host else 0
    if sent_start:
        headers["Range"] = f"bytes={sent_start}-"
        if etag:
            headers["If-Range"] = etag
    return headers, sent_start


def _pinned_session_for(url: str, *,
                        harness: bool = False) -> requests.Session:
    """A session pinned to the validated address of ``url`` (M3).

    The resolution that picks the pin is the same one the policy check used,
    so the TCP connect cannot be re-pointed by a second, uncontrolled DNS
    lookup.  ``harness=True`` is the documented offline-harness escape for
    the *direct* target of :func:`download` (127.0.0.1 / localhost mock
    servers without the flag); redirect hops use the strict policy.
    Resolution failures surface as ``requests.ConnectionError`` (retryable
    — every retry re-validates from scratch); policy violations raise
    :class:`TransferLinkError` immediately.
    """
    parts = urlsplit(url)
    hostname = (parts.hostname or "").lower()
    try:
        return netpolicy.PinnedSession(
            hostname, parts.port or (443 if parts.scheme == "https" else 80),
            allow_loopback=harness)
    except netpolicy.NetworkPolicyError as exc:
        if isinstance(getattr(exc, "__cause__", None),
                      (socket.gaierror, OSError)):
            raise requests.ConnectionError(str(exc)) from exc
        raise TransferLinkError(REDIRECT_UNSAFE_MSG) from exc


def _request_following(url: str, start: int, etag: str | None, *,
                       harness: bool = False
                       ) -> tuple[requests.Response, str, int, requests.Session]:
    """One GET with a manual, validated, per-hop-pinned redirect loop.

    The first hop runs on a session pinned to ``url`` (``harness`` governs
    the direct-target loopback escape); every further hop is re-validated
    against the destination policy and then re-pinned to ITS validated
    address on a fresh session — so no redirect can move the connection to
    an unvalidated socket.  Policy violations raise
    :class:`TransferLinkError` immediately (never retried); resolution
    failures surface as ``requests.ConnectionError`` so the caller's backoff
    can retry them.  Returns ``(response, final_url,
    range_start_actually_sent, session)`` — the caller owns both and MUST
    close the session.
    """
    target = url
    headers = dict(STORAGE_HEADERS)
    if start:
        headers["Range"] = f"bytes={start}-"
        if etag:
            headers["If-Range"] = etag
    sent_start = start
    session: requests.Session | None = None
    try:
        for _hop in range(MAX_REDIRECTS + 1):
            if session is not None:
                session.close()
            session = _pinned_session_for(target, harness=harness)
            resp = session.get(target, headers=headers, stream=True,
                               timeout=OPEN_TIMEOUTS, allow_redirects=False)
            if resp.status_code not in (301, 302, 303, 307, 308):
                return resp, target, sent_start, session
            location = (resp.headers.get("Location") or "").strip()
            resp.close()
            if not location:
                raise TransferLinkError(REDIRECT_UNSAFE_MSG)
            nxt = urljoin(target, location)
            _assert_stream_target(nxt, https_required=True,
                                  resolve_named=True, harness=False)
            headers, sent_start = _redirect_headers(target, nxt, start, etag)
            target = nxt
        raise TransferLinkError(
            "The download server redirected too many times.")
    except BaseException:
        if session is not None:
            session.close()
        raise


def _open_stream(url: str, start: int, resolver,
                 status, should_stop, etag: str | None = None,
                 harness: bool = False
                 ) -> tuple[requests.Response, str, int, requests.Session]:
    """Open the storage stream (optionally from byte ``start``), retrying with
    backoff — fresh transfers can 404 for a few minutes while storage syncs.

    Redirects are followed manually with per-hop validation and per-hop
    pinning (see :func:`_request_following`).  Returns ``(response,
    final_url, range_start_actually_sent, session)``; only 200/206 responses
    are returned and the caller owns (and must close) the session.
    """
    current = url
    last_code = 0
    for attempt in range(len(RETRY_DELAYS) + 1):
        if should_stop and should_stop():
            raise DownloadCancelled()
        session = None
        resp = None
        try:
            resp, final_url, sent_start, session = _request_following(
                current, start, etag, harness=harness)
        except requests.RequestException as exc:
            if attempt >= len(RETRY_DELAYS):
                raise TransferLinkError(
                    f"Could not start the download ({exc.__class__.__name__}).") from exc
        else:
            if resp.status_code in (200, 206):
                return resp, final_url, sent_start, session
            last_code = resp.status_code
            _close_quietly(resp)
            session.close()
        if attempt >= len(RETRY_DELAYS):
            break
        if status:
            status("The server asked us to wait a moment — retrying…")
        _sleep_interruptible(RETRY_DELAYS[attempt], should_stop)
        if resolver:
            try:
                current = resolver()
            except TransferLinkError:
                if attempt >= len(RETRY_DELAYS) - 1:
                    raise
    raise TransferLinkError(
        f"The file server kept returning HTTP {last_code}. Check the link and try again.")


def _is_disk_error(exc: OSError) -> bool:
    """Local-filesystem failures (space, permissions) worth a clear message."""
    if exc.errno in (errno.ENOSPC, errno.EACCES, errno.EDQUOT, errno.EROFS,
                     errno.EFBIG):
        return True
    text = str(exc).lower()
    return ("no space left" in text or "disk quota" in text
            or "read-only" in text or "permission" in text)


def download(url: str, dest_dir: str, transfer_id: str = "",
             progress: Callable[[int, int, float], None] | None = None,
             should_stop: Callable[[], bool] | None = None,
             wait_if_paused: Callable[[], None] | None = None,
             resolver: Callable[[], str] | None = None,
             status: Callable[[str], None] | None = None,
             chunk: int = 1 << 17) -> str:
    """Stream the signed URL into ``dest_dir``; returns the saved file path.

    * non-200 responses are retried with backoff (re-resolving the signed URL),
    * a connection drop mid-stream resumes from the last byte with ``Range``,
      verified via ``Content-Range`` and ``If-Range`` — a wrong offset, a
      changed entity or an ignored Range restarts the file from zero instead
      of appending onto data known to be wrong,
    * redirects are followed manually (max 5 hops) with per-hop destination
      checks and cross-origin header hygiene,
    * ``progress(done, total, speed)`` is throttled to ~6 calls per second,
    * ``should_stop`` -> DownloadCancelled (checked per chunk and before
      every write; a stalled socket surfaces within ``OPEN_TIMEOUTS[1]``);
      ``wait_if_paused`` blocks while paused,
    * the ``.part`` file is always removed on failure or cancel — this
      function always restarts from a fresh request, so partial state is
      never kept across runs,
    * the destination is re-checked just before the final ``os.replace``:
      a file that appeared meanwhile is never clobbered.
    """
    _assert_stream_target(url, harness=True)  # scheme sanity + loopback policy
    os.makedirs(dest_dir, exist_ok=True)
    part = None
    resp = None
    session: requests.Session | None = None
    try:
        # M3: the stream opens on a session pinned to the validated address
        # (loopback / private only pass under the test-mode hatch or the
        # documented harness escape for this direct target); redirect hops
        # re-pin on their own validated sessions.
        resp, current, _sent, session = _open_stream(url, 0, resolver, status,
                                                     should_stop,
                                                     harness=True)
        total = int(resp.headers.get("Content-Length") or 0)
        etag = _entity_tag(resp)
        name = _filename_from(current, resp.headers.get("Content-Disposition", ""),
                              transfer_id)
        if total:
            try:
                free = shutil.disk_usage(dest_dir).free
            except OSError:
                free = None
            if free is not None and free < total + (4 << 20):
                raise TransferLinkError(
                    "Not enough free disk space in the download folder.")
        dest, part, fd = _claim_part(dest_dir, name)
        done = 0
        t0 = time.monotonic()
        last_emit = 0.0
        resumes = 0
        with os.fdopen(fd, "wb") as f:
            while True:
                try:
                    for data in resp.iter_content(chunk_size=chunk):
                        if should_stop and should_stop():
                            raise DownloadCancelled()
                        if wait_if_paused:
                            wait_if_paused()
                        if not data:
                            continue
                        f.write(data)
                        done += len(data)
                        now = time.monotonic()
                        if progress and now - last_emit >= 0.16:
                            last_emit = now
                            progress(done, total, done / max(0.001, now - t0))
                    break
                except requests.RequestException as exc:
                    if total and done >= total:
                        break  # the connection closed after the last byte
                    resumes += 1
                    if resumes > len(RESUME_DELAYS):
                        raise TransferLinkError(
                            "The connection was lost during the download.") from exc
                    if status:
                        status("Connection interrupted — resuming…")
                    _close_quietly(resp)
                    if session is not None:
                        session.close()
                    _sleep_interruptible(
                        RESUME_DELAYS[min(resumes - 1, len(RESUME_DELAYS) - 1)],
                        should_stop)
                    resp, current, sent_start, session = _open_stream(
                        current, done, resolver, status, should_stop,
                        etag=etag, harness=True)
                    if sent_start != done or resp.status_code != 206:
                        # the server ignored the Range request (or a
                        # cross-origin redirect dropped it): the stream is a
                        # fresh full copy — restart the file from zero
                        f.seek(0)
                        f.truncate()
                        done = 0
                        total = int(resp.headers.get("Content-Length") or 0) or total
                        etag = _entity_tag(resp)
                    else:
                        cr = _parse_content_range(
                            resp.headers.get("Content-Range") or "")
                        if (cr is None or cr[0] != done
                                or (total and cr[2] >= 0 and cr[2] != total)):
                            # wrong offset or a different entity: never append
                            # onto data known to be wrong — discard and restart
                            _close_quietly(resp)
                            f.seek(0)
                            f.truncate()
                            done = 0
                            if status:
                                status("Server returned inconsistent data — "
                                       "restarting the download.")
                            if session is not None:
                                session.close()
                            resp, current, _sent, session = _open_stream(
                                current, 0, resolver, status, should_stop,
                                harness=True)
                            total = int(resp.headers.get("Content-Length") or 0) or total
                            etag = _entity_tag(resp)
                        elif cr[2] >= 0:
                            total = cr[2]  # Content-Range total is authoritative
        if total and done < total:
            raise TransferLinkError("The download ended early. Please try again.")
        if progress:
            progress(done, total or done, done / max(0.001, time.monotonic() - t0))
        if os.path.exists(dest):
            # lost a finish-line race: never clobber the winner's file —
            # pick the next free "name (n).ext" for the final replace
            dest = _unique_path(dest_dir, os.path.basename(dest))
        os.replace(part, dest)
        part = None
        return dest
    except DownloadCancelled:
        raise
    except TransferLinkError:
        raise
    except OSError as exc:
        if _is_disk_error(exc):
            raise TransferLinkError(
                "Not enough free disk space in the download folder.") from exc
        raise TransferLinkError(f"Could not save the file: {exc}") from exc
    finally:
        if resp is not None:
            _close_quietly(resp)
        if session is not None:
            session.close()
        if part and os.path.exists(part):
            try:
                os.remove(part)
            except OSError:
                pass
