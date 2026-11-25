"""Upload screening policy for folder scans (Qt-free, network-free).

When the user picks a whole folder to upload, some files must never leave
the machine quietly, and a few more should never leave without an explicit
decision.  This module is the single policy source for the folder scan
(core.tasks) and the upload page; it classifies one path at a time:

- ``SKIP``  — hard exclusions the app must never upload silently: symlinks
  (or Windows junctions/reparse points) whose resolved target lies OUTSIDE
  the user-selected root, broken links, anything that is not a regular file
  (sockets, FIFOs, device nodes, directories) and unreadable paths.  A
  resolved path that escapes the selected root at all (path traversal via
  odd directory entries) is skipped as well.
- ``FLAG``  — sensitive-by-default entries a folder scan excludes but the
  user may deliberately include: hidden dotfiles, ``.env``-style secret
  files, private keys / SSH credentials, credential stores, database dumps,
  browser profile folders and OS metadata files.  The exact, UI-displayable
  pattern lists live in :data:`SENSITIVE_FILES` and :data:`SENSITIVE_FOLDERS`.
  Windows junctions / reparse points that stay inside the root are flagged
  too (``os.path.islink`` does not see them).
- ``OK``    — everything else.  A symlink *inside* the selected root that
  points *inside* it is allowed: linking files around is a normal way to
  organise a folder.

Nothing here reads file content and no function ever raises: any OS error
results in ``SKIP`` with the reason "not readable".
"""
from __future__ import annotations

import fnmatch
import os
import stat

OK = "ok"
FLAG = "flag"
SKIP = "skip"

# Name patterns matched (case-insensitively, ``*`` wildcards) against the
# file NAME only, in priority order (exact/specific names first, so e.g.
# "Thumbs.db" reports as metadata, not as a generic "*.db" database).  Shown
# verbatim in the UI so users can see exactly which names are sensitive.
SENSITIVE_FILES = (
    "Thumbs.db", "desktop.ini", ".DS_Store", "ehthumbs.db", ".AppleDouble",  # OS metadata
    ".env", ".env.*", "*.env",                              # secrets / environment
    "*.pem", "*.key", "*.p12", "*.pfx", "*.ppk",            # private keys / certs
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "known_hosts",  # SSH
    "credentials*.json", "*.kdbx", "service-account*.json",  # credential stores
    "wallet.dat", ".npmrc", ".netrc", ".git-credentials", ".htpasswd",  # more secrets
    "*.sql", "*.sqlite", "*.sqlite3", "*.db",               # database dumps
)

# Folder patterns matched against the path components relative to the
# scanned root (``/``-separated, case-insensitive): any file inside such a
# folder is flagged.
SENSITIVE_FOLDERS = (
    ".ssh", ".aws", ".gnupg", ".kube", ".docker",           # credential stores
    ".mozilla",                                             # browser profiles
    ".config/google-chrome", ".config/chromium",
    "Library/Application Support/Google/Chrome",
)

_METADATA_NAMES = ("thumbs.db", "desktop.ini", ".ds_store", "ehthumbs.db", ".appledouble")
_KEY_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".ppk")
_KEY_NAMES = ("id_rsa", "id_dsa", "id_ed25519", "known_hosts")
_DB_SUFFIXES = (".sql", ".sqlite", ".sqlite3", ".db")


def _name_reason(pattern: str) -> str:
    """Short, dialog-ready reason for one SENSITIVE_FILES pattern."""
    p = pattern.lower()
    if p in _METADATA_NAMES:
        return "system metadata file"
    if p.startswith(".env") or p.endswith(".env"):
        return "may contain secrets (.env)"
    if p.endswith(_KEY_SUFFIXES) or p in _KEY_NAMES:
        return "looks like a private key"
    if "credentials" in p or p.endswith(".kdbx") or p.startswith("service-account"):
        return "may contain credentials"
    if p.endswith(_DB_SUFFIXES):
        return "database file"
    return "sensitive file"


def _folder_reason(pattern: str) -> str:
    """Short reason for one SENSITIVE_FOLDERS pattern."""
    if "chrome" in pattern or pattern == ".mozilla":
        return "browser profile folder"
    return "may contain credentials"


def _flagged_by_name(name: str) -> str | None:
    """Reason when the file name itself matches a sensitive pattern."""
    low = name.lower()
    for pattern in SENSITIVE_FILES:
        if fnmatch.fnmatchcase(low, pattern.lower()):
            return _name_reason(pattern)
    return None


def _flagged_by_folder(parts: list[str]) -> str | None:
    """Reason when any path component (relative to the scanned root) sits in
    a sensitive folder pattern; ``parts`` includes the file name itself, so
    a file literally named ``.ssh`` is caught too."""
    low_parts = [p.lower() for p in parts]
    for pattern in SENSITIVE_FOLDERS:
        wanted = [c for c in pattern.lower().split("/") if c]
        n = len(wanted)
        for i in range(len(low_parts) - n + 1):
            if low_parts[i:i + n] == wanted:
                return _folder_reason(pattern)
    return None


def _within(child: str, parent: str) -> bool:
    """True when ``child`` is ``parent`` itself or inside it (both resolved,
    absolute paths)."""
    try:
        return os.path.commonpath([child, parent]) == os.path.normpath(parent)
    except ValueError:
        return False


def _inspect(path: str, root: str) -> tuple[str, str]:
    """(verdict, reason) for one path.  Never raises."""
    try:
        return _inspect_unsafe(path, root)
    except Exception:  # noqa: BLE001 — a screening helper must never blow up a scan
        return SKIP, "not readable"


def _inspect_unsafe(path: str, root: str) -> tuple[str, str]:
    try:
        lst = os.lstat(path)
    except OSError:
        return SKIP, "not readable"

    # Links: POSIX symlinks plus Windows junctions/reparse points that
    # os.path.islink does not report.  Resolved targets outside the selected
    # root are skipped outright; in-root links stay (flagged on Windows).
    is_symlink = stat.S_ISLNK(lst.st_mode)
    is_reparse = False
    if not is_symlink and os.name == "nt":
        is_reparse = bool(getattr(lst, "st_file_attributes", 0)
                          & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    if is_symlink or is_reparse:
        try:
            target = os.path.realpath(path)
        except OSError:
            return SKIP, "not readable"
        if not os.path.exists(target):
            return SKIP, "broken link"
        # No root selected (individually picked file): a link must at least
        # stay within its own parent directory — anything that escapes is
        # treated as skip.
        base = os.path.realpath(root) if root else os.path.dirname(os.path.realpath(path))
        if not _within(target, base):
            return SKIP, "points outside the selected folder"

    # Regular-file check on the resolved file (follows links on purpose).
    try:
        st = os.stat(path)
    except OSError:
        return SKIP, "not readable"
    if not stat.S_ISREG(st.st_mode):
        return SKIP, "not a regular file"

    # Resolved path must not escape the selected root (traversal via odd
    # directory entries, ``..`` components handed in by a caller, ...).
    if root:
        try:
            real = os.path.realpath(path)
            outside = not _within(real, os.path.realpath(root))
        except OSError:
            return SKIP, "not readable"
        if outside:
            return SKIP, "points outside the selected folder"

    # Name-based flags first (most specific reasons), then folder location,
    # then the generic hidden-file rule.
    name = os.path.basename(path)
    reason = _flagged_by_name(name)
    if reason:
        return FLAG, reason
    if root:
        try:
            rel = os.path.relpath(os.path.abspath(path), os.path.abspath(root))
        except ValueError:              # Windows: file on a different drive
            rel = name
        reason = _flagged_by_folder([p for p in rel.split(os.sep) if p])
        if reason:
            return FLAG, reason
    if name.startswith("."):
        return FLAG, "hidden file"
    if is_reparse:
        return FLAG, "junction/reparse point"
    return OK, ""


def classify(path: str, root: str = "") -> str:
    """One of OK / FLAG / SKIP (see the module docstring).  Never raises."""
    return _inspect(path, root)[0]


def flag_reason(path: str, root: str = "") -> str:
    """Human-readable reason when classify() != OK; '' when OK.  Never raises."""
    verdict, reason = _inspect(path, root)
    return reason if verdict != OK else ""


def screen_batch(paths: list[str], root: str = "") -> tuple[list[str], list[str]]:
    """Screen a whole batch for the upload dialog.

    Returns ``(allowed, flagged_reasons)``: SKIP entries are silently dropped
    from ``allowed``, FLAG entries are additionally reported in
    ``flagged_reasons`` as ``"path — reason"`` strings (em dash) ready for a
    confirmation dialog.  Order is preserved.
    """
    allowed: list[str] = []
    flagged: list[str] = []
    for p in paths or []:
        verdict, reason = _inspect(p, root)
        if verdict == SKIP:
            continue
        allowed.append(p)
        if verdict == FLAG:
            flagged.append(f"{p} — {reason}")
    return allowed, flagged
