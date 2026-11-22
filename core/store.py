"""Settings + transfer history persistence (portable JSON under ``data/``)."""
from __future__ import annotations

import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

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
}


# --------------------------------------------------------------------- utils
def _atomic_write(path: str, text: str) -> None:
    """Write via a temp file + rename so a crash never corrupts the JSON."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


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
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


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
    """Persistent key/value settings with change notifications."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or os.path.join(DATA_DIR, "settings.json")
        self._data: dict[str, Any] = dict(DEFAULTS)
        self._listeners: list[Callable[[str], None]] = []
        self._load()

    # ---------------------------------------------------------- persistence
    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self._data.update({k: v for k, v in loaded.items() if k in DEFAULTS})
        except (OSError, ValueError):
            pass

    def _save(self) -> None:
        try:
            _atomic_write(self.path, json.dumps(self._data, indent=2))
        except OSError:
            pass

    # ------------------------------------------------------------------- api
    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value: Any) -> None:
        if self._data.get(key) == value:
            return
        self._data[key] = value
        self._save()
        for cb in self._listeners:
            try:
                cb(key)
            except Exception:  # noqa: BLE001 — UI callbacks must never break store
                pass

    def on_change(self, cb: Callable[[str], None]) -> None:
        self._listeners.append(cb)

    @property
    def download_folder(self) -> str:
        raw = (self.get("download_dir") or "").strip()
        return os.path.expanduser(raw) if raw else os.path.expanduser("~/Downloads")


# ------------------------------------------------------------------- history
class History:
    """A capped, newest-first list of transfer records."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or os.path.join(DATA_DIR, "history.json")
        self._entries: list[dict] = []
        self._load()
        self._migrate_legacy()

    # ---------------------------------------------------------- persistence
    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self._entries = [e for e in data if isinstance(e, dict)]
        except (OSError, ValueError):
            pass

    def _save(self) -> None:
        try:
            _atomic_write(self.path, json.dumps(self._entries, indent=1, ensure_ascii=False))
        except OSError:
            pass

    def _migrate_legacy(self) -> None:
        """Import records from older versions (next to the app) once."""
        if self._entries:
            return
        v2 = os.path.join(APP_DIR, "data", "history.json")
        if os.path.exists(v2) and os.path.abspath(v2) != os.path.abspath(self.path):
            try:
                with open(v2, encoding="utf-8") as f:
                    old = json.load(f)
                if isinstance(old, list):
                    self._entries = [e for e in old if isinstance(e, dict)][:200]
                    self._save()
                    return
            except (OSError, ValueError):
                pass
        if not os.path.exists(LEGACY_HISTORY):
            return
        try:
            with open(LEGACY_HISTORY, encoding="utf-8") as f:
                legacy = json.load(f)
            base = time.time_ns() // 1000
            for i, e in enumerate(legacy if isinstance(legacy, list) else []):
                files = e.get("files") or []
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
        except (OSError, ValueError):
            pass

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


def export_csv(entries: list[dict], path: str) -> int:
    """Write history to CSV (Excel-friendly UTF-8 BOM). Returns rows written."""
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Type", "Date", "Files", "File names", "Size (bytes)", "Link",
                    "Sender", "Expires", "Status", "Saved to"])
        for e in entries:
            files = e.get("files") or []
            exp = expires_at(e)
            status = ("Active" if is_active(e) else "Expired") if e.get("kind") == "upload" \
                else "Completed"
            w.writerow([e.get("kind", ""), e.get("date", ""), len(files),
                        "; ".join(x.get("name", "") for x in files), e.get("total_bytes", 0),
                        e.get("link", ""), e.get("email", ""),
                        exp.isoformat(timespec="minutes") if exp else "", status,
                        e.get("save_path", "")])
    return len(entries)
