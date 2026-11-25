"""Settings + transfer history persistence (portable JSON under ``data/``).

Storage hardening:

- Every write goes through a unique ``O_CREAT | O_EXCL`` temp file created
  with mode 0600 and is moved into place with ``os.replace``.  That rename is
  atomic on POSIX and Windows, so even two app instances interleaving writes
  cannot produce a corrupt file — a reader always sees one complete version
  (last writer wins; no advisory lock file is needed).
- On POSIX the data directory is tightened to 0700 and the JSON files to
  0600, both at creation and again on every load, so pre-existing loose
  files are fixed too.  On Windows this is a documented no-op: ``os.chmod``
  there only honours the read-only bit and no ACLs are faked — privacy relies
  on the per-user profile directory, as before.
- A data file that exists but does not parse as JSON is renamed to
  ``<name>.corrupt-<timestamp>`` instead of being overwritten, so user data
  is never silently destroyed.  Every storage problem is recorded on the
  object (``last_error``) and written as a one-line stderr warning; no
  storage error ever raises into UI code paths.
"""
from __future__ import annotations

import contextlib
import csv
import json
import os
import re
import stat
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _default_data_dir() -> str:
    """Per-user data folder (always writable, survives app updates)."""
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "TransferNow")


DATA_DIR = os.environ.get("TN_DATA_DIR") or _default_data_dir()
LEGACY_HISTORY = os.path.join(APP_DIR, "history.json")

DEFAULTS: dict[str, Any] = {
    "theme": "dark",             # dark | black | navy | cream | light | system
    "accent": "signal",          # signal | teal | violet | magenta | amber | rose
    "language": "en",            # en | hi | es | fr | de | pt | ja | zh
    "sender_mode": "random",     # random | fixed
    "fixed_email": "",
    "auto_copy_link": True,
    "human_pace": True,
    "parallel_uploads": 2,       # simultaneous upload jobs (1-4)
    "auto_retry": True,          # retry failed uploads automatically (up to 2x)
    "download_dir": "",          # "" -> ~/Downloads
    "open_folder_after_download": True,
    "window_geometry": "",
    "sounds": True,              # UI sound effects
    "sound_volume": "medium",    # low | medium | high
    "queue_cache": [],           # transfers restored on the next launch
    "tray_icon": True,           # show the system-tray icon with quick actions
    "minimize_to_tray": False,   # the close button hides to the tray instead of quitting
    "tray_notifications": True,  # desktop notification when a transfer finishes
    "clip_watch": False,         # watch the clipboard for TransferNow links
    "toast_pos": "br",           # br | bc | tr | tc  (notification stack position)
    "toast_dur": "normal",       # short | normal | long | sticky (auto-dismiss time)
    "toast_bar": True,           # thin countdown line on each notification
    "toast_sound": True,         # soft cue when a notification appears
    "settings_section": "appearance",  # last-open Settings sub-page
    "wizard_done": False,        # first-launch setup wizard completed
    "sidebar_collapsed": False,  # main-window sidebar collapsed state
}


# ------------------------------------------------------------- safe storage
def _harden_path(path: str, is_dir: bool = False) -> None:
    """Owner-only permissions on POSIX (0700 for dirs, 0600 for files).

    Applied at creation and again on every load so pre-existing loose files
    get tightened too.  On Windows this is a no-op: ``os.chmod`` there only
    honours the read-only bit, and faking ACLs is out of scope — the data
    lives under the user's own profile directory anyway.
    """
    if os.name == "nt":
        return
    mode = 0o700 if is_dir else 0o600
    with contextlib.suppress(OSError):
        if stat.S_IMODE(os.stat(path).st_mode) != mode:
            os.chmod(path, mode)


def _warn(message: str) -> None:
    """One-line stderr warning + a line in ``storage.log``.

    stderr may be None under pythonw (windowed frozen build) — the log file
    is then the only trace of a storage failure (A4-05).  The log is
    appended one line per failure and trimmed when it grows past 256 KiB.
    """
    line = f"{datetime.now().isoformat(timespec='seconds')} {message}"
    if sys.stderr is not None:
        with contextlib.suppress(Exception):   # a closed stderr must never crash the app
            print(f"[TransferNow] {message}", file=sys.stderr)
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        log_path = os.path.join(DATA_DIR, "storage.log")
        if os.path.exists(log_path) \
                and os.path.getsize(log_path) > (256 << 10):
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                tail = f.readlines()[-100:]
            with open(log_path, "w", encoding="utf-8") as f:
                f.writelines(tail)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        _harden_path(log_path)
    except Exception:                      # noqa: BLE001 — never crash on logging
        pass


def _sweep_stale_tmp(folder: str, max_age_s: float = 3600) -> None:
    """Remove crashed writers' leftover ``*.tmp`` siblings older than an hour.

    ``_atomic_write`` cleans its temp file on every normal path; a hard kill
    between create and rename can still strand one (A4-10).  A stale temp is
    never picked up again by anyone, so sweeping old ones is pure hygiene.
    """
    try:
        now = time.time()
        for name in os.listdir(folder):
            if not name.endswith(".tmp"):
                continue
            p = os.path.join(folder, name)
            try:
                if now - os.stat(p).st_mtime > max_age_s:
                    os.unlink(p)
            except OSError:
                pass
    except OSError:
        pass


def _atomic_write(path: str, text: str) -> None:
    """Write via a unique temp file + rename so a crash never corrupts the JSON.

    The temp file is created with ``O_CREAT | O_EXCL`` and mode 0600 (it can
    never be clobbered by another writer and is owner-readable only), then
    moved over the destination with ``os.replace`` — an atomic rename on both
    POSIX and Windows, so two instances interleaving writes always leave one
    complete file behind, never a mixed or truncated one.  On any failure the
    temp file is removed and the error propagates for the caller to record.
    """
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    _harden_path(folder, is_dir=True)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    fd = None
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with contextlib.suppress(OSError, AttributeError):
            os.fchmod(fd, 0o600)               # undo any umask surprise (POSIX)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            fd = None                          # fdopen took ownership of the fd
            f.write(text)
        os.replace(tmp, path)
    except BaseException:                  # clean up (even on Ctrl-C), then re-raise
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _backup_corrupt(path: str) -> str | None:
    """Move a corrupt data file aside (``<name>.corrupt-<stamp>``) so the
    user's bytes are never lost; returns the backup path or None."""
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1_000_000:06d}"
    backup = f"{path}.corrupt-{stamp}"
    try:
        os.replace(path, backup)
    except OSError:
        return None
    _harden_path(backup, is_dir=False)
    return backup


def _load_json(path: str, label: str, backup: bool = True) -> tuple[Any, str | None]:
    """Read a JSON data file, keeping corrupt content alive as a backup.

    Returns ``(parsed, None)`` on success, ``(None, None)`` when the file
    does not exist yet, and ``(None, reason)`` on a problem (the reason is
    already phrased for the user, e.g. ``"history: corrupt JSON in ..."``).
    """
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        return None, (f"{label}: cannot read {os.path.basename(path)} "
                      f"({exc.__class__.__name__})")
    try:
        return json.loads(text), None
    except ValueError:
        moved = _backup_corrupt(path) if backup else None
        note = f" (kept a backup at {os.path.basename(moved)})" if moved else ""
        return None, f"{label}: corrupt JSON in {os.path.basename(path)}{note}"


# ------------------------------------------------------- loaded-data checks
# Settings are type-checked before they are trusted: a corrupted or
# hand-edited value falls back to its default instead of crashing the UI.
_BOOL_KEYS = frozenset({
    "auto_copy_link", "human_pace", "auto_retry", "open_folder_after_download",
    "sounds", "tray_icon", "minimize_to_tray", "tray_notifications",
    "clip_watch", "toast_bar", "toast_sound", "wizard_done",
    "sidebar_collapsed",
})
_STR_KEYS = frozenset({
    "theme", "accent", "language", "sender_mode", "fixed_email", "download_dir",
    "window_geometry", "sound_volume", "toast_pos", "toast_dur", "settings_section",
})
_INT_RANGES: dict[str, tuple[int, int]] = {"parallel_uploads": (1, 4)}


def _setting_is_valid(key: str, value: Any) -> bool:
    """True when ``value`` has the expected type and range for a known key."""
    if key in _BOOL_KEYS:
        return isinstance(value, bool)
    if key in _INT_RANGES:
        lo, hi = _INT_RANGES[key]
        return (isinstance(value, int) and not isinstance(value, bool)
                and lo <= value <= hi)
    if key in _STR_KEYS:
        return isinstance(value, str)
    if key == "queue_cache":
        return isinstance(value, list) and all(isinstance(x, dict) for x in value)
    return False           # unknown key — never accepted (not in DEFAULTS anyway)


_STR_FIELDS = frozenset({
    "link", "email", "save_path", "date", "expiry", "error", "state",
    "kind", "title",
})


def _clean_history(raw: list) -> list[dict]:
    """Keep dict records only, and normalize every field's type (A4-01/A4-02).

    One corrupt or hand-edited record must never crash a consumer: the
    History page, the CSV export and the Dashboard aggregations all assume
    ``files`` is a list of dicts with string names and numeric sizes, and
    ``total_bytes`` is an int — a single string cell used to brick app
    startup.  Coercion is total: unknown-shape values are dropped or
    zeroed, never passed through.
    """
    out: list[dict] = []
    for i, e in enumerate(raw):
        if not isinstance(e, dict):
            continue
        eid = e.get("id")
        if not (eid and isinstance(eid, str)):
            eid = str(eid) if eid else f"{time.time_ns() // 1000}_{i}"
        fixed: dict = {"id": eid}
        for k, v in e.items():
            if k == "id":
                continue
            if k == "files":
                fixed[k] = _clean_file_list(v)
            elif k == "total_bytes":
                fixed[k] = v if (isinstance(v, int) and not isinstance(v, bool)
                                 and v >= 0) else 0
            elif k in _STR_FIELDS:
                fixed[k] = v if isinstance(v, str) else ("" if v is None
                                                         else str(v))
            else:
                fixed[k] = v
        out.append(fixed)
    return out


def _clean_file_list(value: Any) -> list[dict]:
    """``files`` must be a list of ``{"name": str, "size": int >= 0}``."""
    if not isinstance(value, list):
        return []
    out: list[dict] = []
    for f in value:
        if not isinstance(f, dict):
            continue
        name = f.get("name")
        if not isinstance(name, str) or not name:
            continue
        size = f.get("size", 0)
        if not (isinstance(size, (int, float)) and not isinstance(size, bool)):
            size = 0
        out.append({"name": name, "size": max(0, int(size))})
    return out


# --------------------------------------------------------------------- utils
def human_size(n: float) -> str:
    """1234567 -> '1.2 MB'."""
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit not in ("B",) else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"


def parse_dt(iso: str):
    """Parse an ISO date; always returns a timezone-aware datetime."""
    try:
        dt = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def now_iso() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def time_ago(iso: str) -> str:
    """'2026-11-02T04:47:42+00:00' -> '3m ago'."""
    dt = parse_dt(iso)
    if dt is None:
        return ""
    secs = max(0, (datetime.now().astimezone() - dt).total_seconds())
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    if secs < 7 * 86400:
        return f"{int(secs // 86400)}d ago"
    return dt.strftime("%b %d, %Y")


def pretty_date(iso: str) -> str:
    dt = parse_dt(iso)
    return dt.strftime("%b %d, %Y · %H:%M") if dt else (iso or "")


# ------------------------------------------------------------------ settings
class Settings:
    """Persistent key/value settings with change notifications.

    ``last_error`` records the last storage problem (None when healthy) so
    the UI can surface it; neither ``set()`` nor the listeners can make a
    storage failure raise into UI code paths.
    """

    def __init__(self, path: str | None = None) -> None:
        self.path = path or os.path.join(DATA_DIR, "settings.json")
        self.last_error: str | None = None
        self._data: dict[str, Any] = dict(DEFAULTS)
        self._listeners: list[Callable[[str], None]] = []
        self._load()

    # ---------------------------------------------------------- persistence
    def _load(self) -> None:
        self.last_error = None
        _harden_path(os.path.dirname(self.path) or ".", is_dir=True)
        _sweep_stale_tmp(os.path.dirname(self.path) or ".")
        loaded, error = _load_json(self.path, "settings")
        if error:
            self._fail(error)
            return
        if loaded is None:
            return                       # no file yet — first launch
        _harden_path(self.path, is_dir=False)
        if not isinstance(loaded, dict):
            self._fail(f"settings: unexpected JSON shape ({type(loaded).__name__}); "
                       "using defaults")
            return
        self._data.update({k: v for k, v in loaded.items()
                           if k in DEFAULTS and _setting_is_valid(k, v)})

    def _fail(self, message: str) -> None:
        """Record a storage problem and warn, without raising."""
        self.last_error = message
        _warn(message)

    def _save(self) -> None:
        try:
            _atomic_write(self.path, json.dumps(self._data, indent=2))
            self.last_error = None
        except (OSError, TypeError, ValueError) as exc:
            self._fail(f"settings: could not save {os.path.basename(self.path)} "
                       f"({exc.__class__.__name__})")

    # ------------------------------------------------------------------- api
    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value: Any) -> None:
        if self._data.get(key) == value:
            return
        self._data[key] = value
        self._save()
        for cb in self._listeners:
            with contextlib.suppress(Exception):   # UI callbacks must never break the store
                cb(key)

    def on_change(self, cb: Callable[[str], None]) -> None:
        self._listeners.append(cb)

    @property
    def download_folder(self) -> str:
        raw = (self.get("download_dir") or "").strip()
        return os.path.expanduser(raw) if raw else os.path.expanduser("~/Downloads")


# ------------------------------------------------------------------- history
class History:
    """A capped, newest-first list of transfer records.

    ``last_error`` records the last storage problem (None when healthy);
    mutating methods never raise on storage failures.
    """

    def __init__(self, path: str | None = None) -> None:
        self.path = path or os.path.join(DATA_DIR, "history.json")
        self.last_error: str | None = None
        self._entries: list[dict] = []
        self._load()
        self._migrate_legacy()

    # ---------------------------------------------------------- persistence
    def _load(self) -> None:
        self.last_error = None
        _harden_path(os.path.dirname(self.path) or ".", is_dir=True)
        _sweep_stale_tmp(os.path.dirname(self.path) or ".")
        data, error = _load_json(self.path, "history")
        if error:
            self._fail(error)
            return
        if data is None:
            return                       # no file yet — first launch
        _harden_path(self.path, is_dir=False)
        if isinstance(data, list):
            self._entries = _clean_history(data)
        else:
            self._fail(f"history: unexpected JSON shape ({type(data).__name__}); "
                       "starting empty")

    def _fail(self, message: str) -> None:
        """Record a storage problem and warn, without raising."""
        self.last_error = message
        _warn(message)

    def _save(self) -> None:
        try:
            _atomic_write(self.path, json.dumps(self._entries, indent=1, ensure_ascii=False))
            self.last_error = None
        except (OSError, TypeError, ValueError) as exc:
            self._fail(f"history: could not save {os.path.basename(self.path)} "
                       f"({exc.__class__.__name__})")

    def _migrate_legacy(self) -> None:
        """Import records from older versions (next to the app) once."""
        if self._entries:
            return
        v2 = os.path.join(APP_DIR, "data", "history.json")
        if os.path.exists(v2) and os.path.abspath(v2) != os.path.abspath(self.path):
            old, error = _load_json(v2, "history (app folder)", backup=False)
            if error:
                self._fail(error)
            elif isinstance(old, list):
                self._entries = _clean_history(old)[:200]
                self._save()
                return
        if not os.path.exists(LEGACY_HISTORY):
            return
        legacy, error = _load_json(LEGACY_HISTORY, "history (legacy)", backup=False)
        if error:
            self._fail(error)
            return
        base = time.time_ns() // 1000
        for i, e in enumerate(legacy if isinstance(legacy, list) else []):
            if not isinstance(e, dict):
                continue
            files = [f for f in (e.get("files") or []) if isinstance(f, dict)]
            self._entries.append({
                "id": f"{base + i}_legacy",      # unique per record
                "kind": "upload",
                "date": (e.get("when") or "").replace(" ", "T"),
                "files": files,
                "total_bytes": sum(f.get("size", 0) for f in files),
                "link": e.get("link", ""),
                "email": e.get("email", ""),
                "expiry": "7 days",
                "elapsed_s": None,
                "save_path": "",
            })
        self._save()

    # ------------------------------------------------------------------- api
    def entries(self) -> list[dict]:
        return list(self._entries)

    def add(self, entry: dict) -> dict:
        entry.setdefault("id", f"{time.time_ns() // 1000}")
        entry.setdefault("date", now_iso())
        entry.setdefault("kind", "upload")
        entry.setdefault("files", [])
        entry.setdefault("total_bytes", 0)
        self._entries.insert(0, entry)
        del self._entries[200:]  # cap
        self._save()
        return entry

    def remove(self, entry_id: str) -> None:
        self.remove_many([entry_id])

    def remove_many(self, ids) -> None:
        ids = set(ids)
        self._entries = [e for e in self._entries if e.get("id") not in ids]
        self._save()

    def update_entry(self, entry_id: str, **fields: Any) -> None:
        """Patch one entry in place (e.g. link_state from the link checker)."""
        for e in self._entries:
            if str(e.get("id")) == str(entry_id):
                e.update(fields)
                self._save()
                return

    def clear(self) -> None:
        self._entries = []
        self._save()


# --------------------------------------------------------------- full reset
RESET_MARKER = ".reset-pending"
_OWNED_FILES = ("settings.json", "history.json", "storage.log", "error.log")


def request_reset() -> bool:
    """Ask for a factory reset.  The wipe itself runs at the NEXT start (before any
    file is loaded), so the closing app can never write its old state back."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(os.path.join(DATA_DIR, RESET_MARKER), "w", encoding="utf-8") as f:
            f.write("reset")
        return True
    except OSError:
        return False


def wipe_app_data() -> int:
    """Delete everything Transfer Now stored for this user; returns files removed.

    Removed: settings, history (+ queue, which lives in settings), logs, corrupt-file
    backups, temp files, generated sounds, legacy history files and leftover ZIP
    archives.  Never touched: the files you uploaded or downloaded.
    """
    import shutil
    removed = 0

    def rm(path: str) -> None:
        nonlocal removed
        try:
            os.unlink(path)
            removed += 1
        except OSError:
            pass

    if os.path.isdir(DATA_DIR):
        for name in os.listdir(DATA_DIR):
            path = os.path.join(DATA_DIR, name)
            owned = any(name == f or name.startswith(f + ".") for f in _OWNED_FILES)
            if os.path.isfile(path) and owned:
                rm(path)                      # file, its .tmp and .corrupt-* backups
        shutil.rmtree(os.path.join(DATA_DIR, "sounds"), ignore_errors=True)
    for legacy in (LEGACY_HISTORY, os.path.join(APP_DIR, "data", "history.json")):
        if os.path.isfile(legacy):
            rm(legacy)                        # else the old history would be re-imported
    try:
        from core import ziputil
        shutil.rmtree(ziputil.archive_root(), ignore_errors=True)
    except Exception:  # noqa: BLE001 — cleanup must never block a reset
        pass
    return removed


def apply_pending_reset() -> bool:
    """Run at start-up: if a reset was requested, wipe now.  True when it ran."""
    marker = os.path.join(DATA_DIR, RESET_MARKER)
    if not os.path.exists(marker):
        return False
    wipe_app_data()
    with contextlib.suppress(OSError):
        os.unlink(marker)
    return True


LINK_CHECK_V = 2        # verdicts saved by older, less reliable checkers are ignored


def expires_at(entry: dict):
    """When an upload link stops working (aware datetime) or None.

    The end date reported by the live site (``link_valid_until``, saved by the
    link checker) wins over the nominal "date + N days" estimate.
    """
    if entry.get("kind") != "upload":
        return None
    live = (parse_dt(entry.get("link_valid_until", ""))
            if entry.get("link_valid_until") and entry.get("link_check_v") == LINK_CHECK_V else None)
    if live is not None:
        return live
    dt = parse_dt(entry.get("date", ""))
    if dt is None:
        return None
    m = re.search(r"(\d+)", str(entry.get("expiry") or "7"))
    days = int(m.group(1)) if m else 7
    return dt + timedelta(days=days)


def is_active(entry: dict) -> bool:
    """Upload links: not expired.  A link is *expired* when its end date has
    passed **or** the live link check found it gone — whichever comes first,
    so a stale "active" verdict can never outlive the real expiry.
    Downloads: never 'active'."""
    if entry.get("kind") != "upload" or not entry.get("link"):
        return False
    if entry.get("link_state") == "expired" and entry.get("link_check_v") == LINK_CHECK_V:
        return False
    exp = expires_at(entry)
    if exp is None:
        return False
    return exp > datetime.now().astimezone()


# ------------------------------------------------------- CSV formula safety
DANGEROUS_CSV_LEADS = ("=", "+", "-", "@")   # Excel / Sheets formula triggers


def sanitize_csv_cell(value: Any) -> Any:
    """Neutralise CSV formula injection (CWE-1236) for one cell.

    Excel and Google Sheets execute a cell as a formula when its text starts
    with ``=``, ``+``, ``-`` or ``@`` — including after leading TAB/CR/LF
    spaces — so a crafted file name or sender address could run commands on
    whoever opens the exported CSV.  The standard mitigation is a leading
    single quote, which makes the cell import as text; ordinary values
    (including ISO dates like ``2026-11-23``) pass through unchanged and
    numbers are never touched.
    """
    if not isinstance(value, str):
        return value
    stripped = value.lstrip()                 # decision on the first non-whitespace char
    if stripped and stripped[0] in DANGEROUS_CSV_LEADS:
        return "'" + value
    return value


def export_csv(entries: list[dict], path: str) -> int:
    """Write history to CSV (Excel-friendly UTF-8 BOM). Returns rows written.

    Every text cell goes through :func:`sanitize_csv_cell` so a hostile file
    name, link or sender can never become a spreadsheet formula; the file
    count and byte size stay real numbers.
    """
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Type", "Date", "Files", "File names", "Size (bytes)", "Link",
                    "Sender", "Expires", "Status", "Saved to"])
        for e in entries:
            files = e.get("files") or []
            exp = expires_at(e)
            status = ("Active" if is_active(e) else "Expired") if e.get("kind") == "upload" \
                else "Completed"
            w.writerow([
                sanitize_csv_cell(e.get("kind", "")),
                sanitize_csv_cell(e.get("date", "")),
                len(files),
                sanitize_csv_cell("; ".join(x.get("name", "") for x in files)),
                sanitize_csv_cell(e.get("total_bytes", 0)
                                  if isinstance(e.get("total_bytes", 0), (int, float))
                                  else str(e.get("total_bytes", 0))),
                sanitize_csv_cell(e.get("link", "")),
                sanitize_csv_cell(e.get("email", "")),
                sanitize_csv_cell(exp.isoformat(timespec="minutes") if exp else ""),
                sanitize_csv_cell(status),
                sanitize_csv_cell(e.get("save_path", "")),
            ])
    _harden_path(path)                     # owner-only (A4-08/L15)
    return len(entries)
