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
_pid_ct: dict[int, float | None] = {}     # pid -> create_time at registration
_profiles: set[str] = set()

_DRIVER_NAMES = ("chromedriver", "msedgedriver", "geckodriver")


def register(pid: int | None, profile: str | None = None) -> None:
    with _lock:
        if pid:
            pid = int(pid)
            _pids.add(pid)
            _pid_ct[pid] = None
            if psutil is not None:
                try:
                    _pid_ct[pid] = psutil.Process(pid).create_time()
                except Exception:       # noqa: BLE001 — died before we looked
                    pass
        if profile:
            _profiles.add(profile)


def unregister(pid: int | None, profile: str | None = None) -> None:
    with _lock:
        if pid:
            pid = int(pid)
            _pids.discard(pid)
            _pid_ct.pop(pid, None)
        if profile:
            _profiles.discard(profile)


def _identity_ok(pid: int, proc) -> bool:
    """True when ``proc`` is really the process we tracked (A4-04/L7).

    A tracked PID can be recycled minutes later (uploads run up to ~30 min);
    killing blind would take out an unrelated user process and its whole
    tree.  Accept the recorded create_time (±1 s clock slack) or a
    WebDriver/browser name; when identity cannot be verified, fail CLOSED —
    skip the targeted kill (``_kill_our_browsers`` still sweeps any real
    orphan by ancestry).
    """
    if psutil is None:
        return True                     # no psutil: no identity data at all
    try:
        ct = _pid_ct.get(pid)
        name = (proc.name() or "").lower()
        if name.endswith(".exe"):
            name = name[:-4]
        if ct is not None and abs(proc.create_time() - ct) <= 1.0:
            return True
        if any(d in name for d in _DRIVER_NAMES) or name in BROWSER_NAMES:
            return True
    except Exception:                   # noqa: BLE001 — unverifiable → skip
        pass
    return False


def kill_tree(pid: int | None) -> None:
    """Kill a process and everything it started (identity-verified)."""
    if not pid:
        return
    pid = int(pid)
    if psutil is not None:
        try:
            parent = psutil.Process(pid)
        except Exception:               # noqa: BLE001 — already gone
            return
        if not _identity_ok(pid, parent):
            return
        try:
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
            os.kill(pid, 9)
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


def relaunch() -> bool:
    """Start a fresh, detached copy of this app that waits for us to exit first."""
    import sys
    env = dict(os.environ, TN_WAIT_PID=str(os.getpid()))
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"      # onefile builds: don't reuse our temp dir
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, *sys.argv[1:]]
    else:
        cmd = [sys.executable, os.path.abspath(sys.argv[0]), *sys.argv[1:]]
    kw: dict = {}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200      # DETACHED_PROCESS | NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    try:
        subprocess.Popen(cmd, env=env, close_fds=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
        return True
    except OSError:
        return False


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
