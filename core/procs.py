"""Make sure no browser process outlives the app.

Uploads drive a hidden Chrome/Edge through chromedriver.  ``driver.quit()`` normally
removes both, but a hard kill (cancel, quit mid-transfer, crash) can leave Chrome's
helper processes behind — they would keep showing up in Task Manager after the app
is gone.  This module tracks what we start and kills the whole tree on demand.

``kill_all()`` is safe to call any number of times, from any thread, at any moment.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading

try:                                    # psutil gives reliable process trees on every OS
    import psutil
except Exception:                       # noqa: BLE001 — degrade to OS commands
    psutil = None

BROWSER_NAMES = ("chromedriver", "msedgedriver", "chrome", "msedge", "chromium",
                 "chromium-browser", "google-chrome", "headless_shell", "chrome_crashpad_handler")

_lock = threading.Lock()
_pids: set[int] = set()
_profiles: set[str] = set()


def register(pid: int | None, profile: str | None = None) -> None:
    with _lock:
        if pid:
            _pids.add(int(pid))
        if profile:
            _profiles.add(profile)


def unregister(pid: int | None, profile: str | None = None) -> None:
    with _lock:
        if pid:
            _pids.discard(int(pid))
        if profile:
            _profiles.discard(profile)


def kill_tree(pid: int | None) -> None:
    """Kill a process and everything it started."""
    if not pid:
        return
    if psutil is not None:
        try:
            parent = psutil.Process(int(pid))
            procs = parent.children(recursive=True) + [parent]
        except Exception:               # noqa: BLE001 — already gone
            return
        for p in procs:
            try:
                p.kill()
            except Exception:           # noqa: BLE001
                pass
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=8,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            os.kill(int(pid), 9)
    except Exception:                   # noqa: BLE001
        pass


def _kill_our_browsers() -> None:
    """Kill any browser/driver that is a descendant of this app (covers a browser that
    was still starting up, when we did not yet know its pid)."""
    if psutil is None:
        return
    try:
        kids = psutil.Process(os.getpid()).children(recursive=True)
    except Exception:                   # noqa: BLE001
        return
    for p in kids:
        try:
            name = (p.name() or "").lower()
            if name.endswith(".exe"):
                name = name[:-4]
            if name in BROWSER_NAMES:
                p.kill()
        except Exception:               # noqa: BLE001
            pass


def kill_profile(profile: str | None) -> None:
    """Kill leftover browser processes started with this user-data-dir and delete it."""
    if not profile:
        return
    if psutil is not None:
        for p in psutil.process_iter(["cmdline"]):
            try:
                if any(profile in (a or "") for a in (p.info.get("cmdline") or [])):
                    p.kill()
            except Exception:           # noqa: BLE001
                pass
    shutil.rmtree(profile, ignore_errors=True)


def kill_all() -> None:
    """Kill every tracked browser tree and sweep temp profiles."""
    with _lock:
        pids, profiles = list(_pids), list(_profiles)
        _pids.clear()
        _profiles.clear()
    for pid in pids:
        kill_tree(pid)
    _kill_our_browsers()
    for prof in profiles:
        kill_profile(prof)


def hard_exit(code: int = 0) -> None:
    """Last step of every quit: kill browsers, flush, and end the process right now.

    ``os._exit`` skips interpreter shutdown, so a stuck worker thread (a hung network
    call, a ThreadPool) can never keep the process alive in Task Manager.
    """
    try:
        kill_all()
    finally:
        import sys
        for stream in (sys.stdout, sys.stderr):
            try:
                if stream is not None:
                    stream.flush()
            except Exception:           # noqa: BLE001
                pass
        os._exit(int(code))
