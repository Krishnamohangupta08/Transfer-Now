"""CHANGELOG.md parser (Qt-free, network-free, never raises).

``CHANGELOG.md`` is the single source of release notes; the Changelog page
renders whatever this parser returns.  Nothing is hardcoded here.

Recognised layout (both styles may be mixed, file order is kept — it is the
author's chronology, so versions are NOT re-sorted by semver)::

    ## 1.0.0.1 (2026-11-24)          release heading; date is optional
    Summary: one paragraph …          optional summary (continuation lines ok)
    ### New Features                  section heading …
    Security:                         … or a plain "Heading:" line
    - bullet that may continue
      on indented lines

Inline ``**bold**`` / `` `code` `` / ``*em*`` markers are stripped.  Unknown
or malformed lines never crash the parser; they are skipped or kept as prose.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

SECTION_ORDER = ("New Features", "Improvements", "Bug Fixes", "Security",
                 "Breaking Changes")

_SECTION_ALIASES = {
    "new features": "New Features", "new feature": "New Features",
    "features": "New Features", "added": "New Features", "new": "New Features",
    "improvements": "Improvements", "improvement": "Improvements",
    "changed": "Improvements", "changes": "Improvements",
    "bug fixes": "Bug Fixes", "bug fix": "Bug Fixes", "fixes": "Bug Fixes",
    "fixed": "Bug Fixes",
    "security": "Security", "security improvements": "Security",
    "breaking changes": "Breaking Changes", "breaking": "Breaking Changes",
}

_VERSION_RE = re.compile(
    r"^##\s+\[?v?(?P<ver>\d+(?:\.\d+)*(?:[-+.][0-9A-Za-z.\-]+)?)\]?"
    r"\s*(?:[-–—(]\s*(?P<date>[^)]*?)\)?)?\s*$")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_H3_RE = re.compile(r"^#{3,4}\s+(?P<t>.+?)\s*#*\s*$")
_COLON_RE = re.compile(r"^(?P<t>[A-Za-z][A-Za-z /&-]{1,40}):\s*$")
_BULLET_RE = re.compile(r"^(?P<indent>\s*)(?:[-*+]|\d+[.)])\s+(?P<t>.*)$")
_INLINE = [(re.compile(r"\*\*(.+?)\*\*"), r"\1"), (re.compile(r"__(.+?)__"), r"\1"),
           (re.compile(r"`([^`]*)`"), r"\1"), (re.compile(r"(?<!\w)\*(?!\s)(.+?)\*(?!\w)"), r"\1"),
           (re.compile(r"\[([^\]]+)\]\([^)]*\)"), r"\1")]


@dataclass
class Section:
    title: str                                   # display title (canonical when known)
    items: list[str] = field(default_factory=list)


@dataclass
class Release:
    version: str
    date: str = ""                               # "" when the heading has none
    summary: str = ""
    sections: list[Section] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.summary and not any(s.items for s in self.sections)


@dataclass
class Changelog:
    releases: list[Release] = field(default_factory=list)
    status: str = "ok"                           # ok | missing | empty | invalid
    path: str = ""

    @property
    def latest(self) -> Release | None:
        return self.releases[0] if self.releases else None


def clean_inline(text: str) -> str:
    """Strip light Markdown markers from one line of text."""
    for rx, rep in _INLINE:
        text = rx.sub(rep, text)
    return " ".join(text.split())


def _canon(title: str) -> str:
    t = clean_inline(title).rstrip(":").strip()
    return _SECTION_ALIASES.get(t.lower(), t)


def parse_text(text: str) -> Changelog:
    """Parse changelog Markdown.  Never raises."""
    try:
        return _parse(text if isinstance(text, str) else "")
    except Exception:  # noqa: BLE001 — a malformed file must not take the page down
        return Changelog(status="invalid")


def _parse(text: str) -> Changelog:
    if not text.strip():
        return Changelog(status="empty")
    releases: list[Release] = []
    cur: Release | None = None
    sec: Section | None = None
    in_summary = False
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("## ") and not stripped.startswith("###"):
            m = _VERSION_RE.match(stripped)
            if m:
                d = (m.group("date") or "").strip()
                dm = _DATE_RE.search(d)
                cur = Release(version=m.group("ver"), date=dm.group(0) if dm else "")
                releases.append(cur)
                sec, in_summary = None, False
            else:
                cur, sec, in_summary = None, None, False   # unknown "## x" block
            continue
        if cur is None:
            continue                       # preamble / title / unknown block
        if not stripped:
            in_summary = False
            continue
        h3 = _H3_RE.match(stripped)
        if h3:
            sec = Section(_canon(h3.group("t")))
            cur.sections.append(sec)
            in_summary = False
            continue
        if stripped.lower().startswith("summary:"):
            cur.summary = clean_inline(stripped[8:])
            in_summary = True
            continue
        cm = _COLON_RE.match(stripped)
        if cm and not _BULLET_RE.match(line):
            sec = Section(_canon(cm.group("t")))
            cur.sections.append(sec)
            in_summary = False
            continue
        bm = _BULLET_RE.match(line)
        if bm:
            if sec is None:
                sec = Section("Notes")
                cur.sections.append(sec)
            sec.items.append(clean_inline(bm.group("t")))
            in_summary = False
            continue
        # continuation / prose
        if in_summary:
            cur.summary = (cur.summary + " " + clean_inline(stripped)).strip()
        elif sec is not None and sec.items and raw[:1] in (" ", "\t"):
            sec.items[-1] = (sec.items[-1] + " " + clean_inline(stripped)).strip()
        elif sec is not None and sec.items:
            sec.items[-1] = (sec.items[-1] + " " + clean_inline(stripped)).strip()
        elif sec is None and not cur.summary:
            cur.summary = clean_inline(stripped)
            in_summary = True
        elif sec is None:
            cur.summary = (cur.summary + " " + clean_inline(stripped)).strip()
        else:
            sec.items.append(clean_inline(stripped))
    for r in releases:
        r.sections = [s for s in r.sections if s.items]
    if not releases:
        return Changelog(status="invalid")
    return Changelog(releases=releases)


class ChangelogParser:
    """File-backed parser with an mtime/size cache (edits are picked up live)."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or default_path()
        self._sig: tuple | None = None
        self._cached: Changelog | None = None

    def load(self) -> Changelog:
        """Parsed changelog; never raises."""
        try:
            st = os.stat(self.path)
        except OSError:
            self._sig, self._cached = None, None
            if self.path == default_path():              # packaged build lost the file: use the embedded copy
                try:
                    from ui.assets import changelog_text
                    txt = changelog_text()
                    if txt:
                        res = parse_text(txt)
                        res.path = self.path
                        return res
                except Exception:  # noqa: BLE001
                    pass
            return Changelog(status="missing", path=self.path)
        sig = (st.st_mtime_ns, st.st_size)
        if self._cached is not None and sig == self._sig:
            return self._cached
        try:
            with open(self.path, "rb") as fh:
                data = fh.read(2_000_000)                 # bounded read
            text = data.decode("utf-8", errors="replace")
        except OSError:
            return Changelog(status="missing", path=self.path)
        result = parse_text(text)
        result.path = self.path
        self._sig, self._cached = sig, result
        return result


def default_path() -> str:
    """CHANGELOG.md next to the app (source tree, or the PyInstaller bundle)."""
    import sys
    src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cands = []
    mp = getattr(sys, "_MEIPASS", None)
    if mp:
        cands.append(mp)
    if getattr(sys, "frozen", False):
        exe = os.path.dirname(sys.executable)
        cands += [exe, os.path.join(exe, "_internal")]
    cands.append(src)
    for base in cands:
        p = os.path.join(base, "CHANGELOG.md")
        if os.path.isfile(p):
            return p
    return os.path.join(cands[0], "CHANGELOG.md")
