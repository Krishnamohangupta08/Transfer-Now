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
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from datetime import datetime
from typing import Callable, Optional
from urllib.parse import parse_qs, unquote, urlparse

import requests

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

TRANSFER_ID_RE = re.compile(r"transfernow\.net/(?:[a-z]{2}(?:-[A-Za-z]{2})?/)?(?:d|dl)/([A-Za-z0-9-]{6,})", re.I)

TITLE_RE = re.compile(r"downloader_title__[^>]*>(?:<[^>]+>)*([^<]+)")
FILE_NAME_RE = re.compile(r'list_filename__[^"]*"\s+title="([^"]+)"')
FILE_SIZE_RE = re.compile(r'list_filesize__[^"]*">([\d.,]+)\s*([KMG]B)\s*<')
VALIDITY_RE = re.compile(r'"validity":\{"from":"([^"]+)","to":"([^"]+)"\}')

# retry backoff (seconds) — safety net for transient storage/API hiccups
RETRY_DELAYS = [2, 3, 5, 8, 12, 15, 20, 25]


class TransferLinkError(RuntimeError):
    """A human-readable problem with the link (invalid / expired / failed)."""


class DownloadCancelled(RuntimeError):
    """Raised when the user cancels a running download."""


def parse_transfer_id(link: str) -> Optional[str]:
    """'https://www.transfernow.net/dl/abc123' -> 'abc123'."""
    link = (link or "").strip()
    m = TRANSFER_ID_RE.search(link)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9-]{6,}", link):  # bare id
        return link
    return None


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


def _rsc_payload(html: str) -> tuple[Optional[list], Optional[str]]:
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
    files = [{"name": f.get("name", "file"), "size": int(f.get("size", 0))}
             for f in arr if isinstance(f, dict) and f.get("name")]
    return (files or None), m.group(2)


def _fmt_expiry(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%b %d, %Y")
    except ValueError:
        return ""


def fetch_info(link: str, timeout: int = 20) -> dict:
    """Fetch transfer metadata for a share link (no browser required)."""
    tid = parse_transfer_id(link)
    if not tid:
        raise TransferLinkError(
            "That doesn't look like a TransferNow link "
            "(expected https://www.transfernow.net/dl/…).")
    url = f"https://www.transfernow.net/dl/{tid}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
    except requests.RequestException as exc:
        raise TransferLinkError(f"Could not reach transfernow.net ({exc.__class__.__name__}).") from exc
    if resp.status_code != 200:
        raise TransferLinkError(f"TransferNow returned HTTP {resp.status_code} for this link.")

    html = resp.text
    html_nc = re.sub(r"<!--.*?-->", "", html)  # SPA hydration comments

    title_m = TITLE_RE.search(html_nc)
    title = (title_m.group(1).strip() if title_m else "").strip()

    files, validity_to = _rsc_payload(html)
    expiry = _fmt_expiry(validity_to or "")
    if not files:  # fallback: parse the rendered file rows
        names = [unquote(n) for n in FILE_NAME_RE.findall(html_nc)]
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
        url = (resp.json() or {}).get("url", "")
    except ValueError as exc:
        raise TransferLinkError("Download API returned an unexpected response.") from exc
    if not url:
        raise TransferLinkError("Could not resolve a download URL for this link "
                                "(it may have expired).")
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


def _sleep_interruptible(seconds: float, should_stop: Optional[Callable[[], bool]]) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if should_stop and should_stop():
            raise DownloadCancelled()
        time.sleep(min(0.2, max(0.0, end - time.monotonic())))


def _open_stream(session: requests.Session, url: str, start: int, resolver, status,
                 should_stop) -> tuple[requests.Response, str]:
    """Open the storage stream (optionally from byte ``start``), retrying with
    backoff — fresh transfers can 404 for a few minutes while storage syncs."""
    current = url
    last_code = 0
    headers = dict(STORAGE_HEADERS)
    if start:
        headers["Range"] = f"bytes={start}-"
    for attempt in range(len(RETRY_DELAYS) + 1):
        if should_stop and should_stop():
            raise DownloadCancelled()
        try:
            resp = session.get(current, headers=headers, stream=True,
                               timeout=(10, 30), allow_redirects=True)
        except requests.RequestException as exc:
            if attempt >= len(RETRY_DELAYS):
                raise TransferLinkError(
                    f"Could not start the download ({exc.__class__.__name__}).") from exc
        else:
            if resp.status_code in (200, 206):
                return resp, current
            last_code = resp.status_code
            resp.close()
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


def download(url: str, dest_dir: str, transfer_id: str = "",
             progress: Optional[Callable[[int, int, float], None]] = None,
             should_stop: Optional[Callable[[], bool]] = None,
             wait_if_paused: Optional[Callable[[], None]] = None,
             resolver: Optional[Callable[[], str]] = None,
             status: Optional[Callable[[str], None]] = None,
             chunk: int = 1 << 17) -> str:
    """Stream the signed URL into ``dest_dir``; returns the saved file path.

    * non-200 responses are retried with backoff (re-resolving the signed URL),
    * a connection drop mid-stream resumes from the last byte with ``Range``,
    * ``progress(done, total, speed)`` is throttled to ~6 calls per second,
    * ``should_stop`` -> DownloadCancelled; ``wait_if_paused`` blocks while paused.
    """
    os.makedirs(dest_dir, exist_ok=True)
    session = requests.Session()
    part = None
    resp = None
    try:
        resp, current = _open_stream(session, url, 0, resolver, status, should_stop)
        total = int(resp.headers.get("Content-Length") or 0)
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
        dest = _unique_path(dest_dir, name)
        part = dest + ".part"
        done = 0
        t0 = time.monotonic()
        last_emit = 0.0
        resumes = 0
        with open(part, "wb") as f:
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
                except (requests.RequestException, OSError) as exc:
                    if isinstance(exc, OSError) and not isinstance(
                            exc, requests.RequestException):
                        raise
                    # network drop: resume from where we stopped
                    resumes += 1
                    if resumes > 6 or (total and done >= total):
                        if total and done >= total:
                            break
                        raise TransferLinkError(
                            "The connection was lost during the download.") from exc
                    if status:
                        status("Connection interrupted — resuming…")
                    resp.close()
                    _sleep_interruptible(min(2 * resumes, 8), should_stop)
                    resp, current = _open_stream(session, current, done, resolver,
                                                 status, should_stop)
                    if resp.status_code != 206:
                        # server ignored Range: restart cleanly
                        f.seek(0)
                        f.truncate()
                        done = 0
        if total and done < total:
            raise TransferLinkError("The download ended early. Please try again.")
        if progress:
            progress(done, total or done, done / max(0.001, time.monotonic() - t0))
        os.replace(part, dest)
        part = None
        return dest
    except DownloadCancelled:
        raise
    except TransferLinkError:
        raise
    except OSError as exc:
        raise TransferLinkError(f"Could not save the file: {exc}") from exc
    finally:
        if resp is not None:
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass
        session.close()
        if part and os.path.exists(part):
            try:
                os.remove(part)
            except OSError:
                pass
