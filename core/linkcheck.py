"""Link health checker for saved TransferNow share links.

Uses the same page structure the downloader already relies on (the share page
is server-rendered and embeds ``"validity":{"from":…,"to":…}`` plus the file
list), so the verdict is based on what the site really says:

* **active**  – page is served, files are listed / "Files are ready" and the
  validity window has not ended.
* **expired** – HTTP 404/410, the validity window has ended, or the page has
  no files and no "ready" marker (TransferNow's "invalid / expired / removed"
  page).
* **unknown** – anything we cannot be sure about (network trouble, 403/429/5xx,
  captcha pages).  Unknown never overwrites a previous verdict.

Links are normalised first (``/en/d/<id>`` and bare ids work), and transient
network errors are retried once.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from .downloader import HEADERS, VALIDITY_RE, parse_transfer_id

# Only phrases a *visible* "gone" page would show.  Deliberately specific: the
# page's embedded translation JSON can contain generic words like "has expired"
# for every transfer, so those are never used as evidence.
_EXPIRED_TEXT = re.compile(
    r"(this (?:transfer|link) has expired|link has expired|transfer has expired|"
    r"no longer available|not available anymore|has been deleted|"
    r"files? (?:have|has) been removed|transfer not found)", re.I)
_BLOCKED_TEXT = re.compile(r"(captcha|access denied|attention required|just a moment)", re.I)
_READY_TEXT = re.compile(r"(Files are ready|list_filename__|downloader_filename)", re.I)
_PREFIX_BYTES = 600_000          # the Next.js payload sits well inside this
FRESH_GRACE_S = 30 * 60          # a just-created link may 404 while storage syncs


def normalize_link(link: str) -> Optional[str]:
    """Canonical download URL for a link / bare id, or None if it isn't one."""
    tid = parse_transfer_id(link)
    return f"https://www.transfernow.net/dl/{tid}" if tid else None


def _parse_end(iso: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _read_prefix(resp: requests.Response) -> str:
    chunks, got = [], 0
    for chunk in resp.iter_content(chunk_size=8192):
        if not chunk:
            continue
        chunks.append(chunk)
        got += len(chunk)
        if got >= _PREFIX_BYTES:
            break
    return b"".join(chunks).decode("utf-8", "ignore")


def _visible_text(html: str) -> str:
    """Rendered text only: no <script>/<style> blocks, comments or tags."""
    t = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    t = re.sub(r"<(script|style)\b.*?</\1>", " ", t, flags=re.S | re.I)
    return re.sub(r"<[^>]+>", " ", t)


def classify_page(html: str) -> tuple[str, str]:
    """(state, valid_until_iso) for a 200 share page.

    ``expired`` needs hard evidence (an end date in the past, or a visible
    "gone" message with no files).  Anything we can't read is ``unknown`` — a
    live link must never be reported expired because of a parsing guess.
    """
    raw = html.replace('\\"', '"')                 # Next.js payload escapes its quotes
    m = VALIDITY_RE.search(raw)
    if m:
        end = _parse_end(m.group(2))
        if end is not None:
            return ("expired" if end <= datetime.now(timezone.utc) else "active"), m.group(2)
    has_files = bool(re.search(r'"files":\[\s*\{', raw)) or bool(_READY_TEXT.search(raw))
    if has_files:
        return "active", ""
    text = _visible_text(raw)
    if _BLOCKED_TEXT.search(text) and len(text) < 20_000:
        return "unknown", ""
    if _EXPIRED_TEXT.search(text):
        return "expired", ""
    return "unknown", ""


def check_link_detail(link: str, timeout: float = 12.0, retries: int = 1,
                      fresh: bool = False) -> dict:
    """``{"state": active|expired|unknown, "valid_until": iso|"", "http": int}``."""
    url = normalize_link(link)
    if url is None:
        return {"state": "unknown", "valid_until": "", "http": 0, "reason": "not a TransferNow link"}
    last = {"state": "unknown", "valid_until": "", "http": 0, "reason": "unreachable"}
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=timeout, stream=True,
                                allow_redirects=True)
        except requests.RequestException as exc:
            last = {"state": "unknown", "valid_until": "", "http": 0,
                    "reason": exc.__class__.__name__}
            if attempt < retries:
                time.sleep(0.8)
            continue
        try:
            code = resp.status_code
            if code in (404, 410):
                if fresh:            # just created: storage may still be syncing
                    return {"state": "unknown", "valid_until": "", "http": code,
                            "reason": "new link, not published yet"}
                if attempt < retries:   # one more look before calling it gone
                    time.sleep(1.5)
                    continue
                return {"state": "expired", "valid_until": "", "http": code, "reason": f"HTTP {code}"}
            if code != 200:
                last = {"state": "unknown", "valid_until": "", "http": code, "reason": f"HTTP {code}"}
                if code in (429, 500, 502, 503, 504) and attempt < retries:
                    time.sleep(1.2)
                    continue
                return last
            state, until = classify_page(_read_prefix(resp))
            return {"state": state, "valid_until": until, "http": 200, "reason": ""}
        finally:
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass
    return last


def check_link(link: str, timeout: float = 12.0) -> str:
    """Return 'active' | 'expired' | 'unknown' for one share link."""
    return check_link_detail(link, timeout)["state"]


def is_fresh(date_iso: str) -> bool:
    """True when a link was created within the last FRESH_GRACE_S seconds."""
    dt = _parse_end(date_iso or "")
    return dt is not None and (datetime.now(timezone.utc) - dt).total_seconds() < FRESH_GRACE_S


def extract_ids(entries: list[dict]) -> list[tuple[str, str]]:
    """(entry id, link) pairs for upload entries that have a real link."""
    return [(str(e.get("id", "")), e["link"]) for e in entries
            if e.get("kind") == "upload" and e.get("link")]
