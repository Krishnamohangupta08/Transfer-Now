"""Quitting must end the process and leave no browser behind (nothing in Task Manager).

Each test runs a fresh Python process, so the hard exit can really be observed.

Diagnostics contract (third-round inspection found an undiagnosable exit-1
because the child printed nothing and nothing was captured):

* the app child's stdout AND stderr are captured and shown verbatim in every
  assertion failure message, so a crash names itself;
* a pre-flight probe imports what the child needs: a missing dependency is an
  explicit skip that NAMES the missing module; any other probe failure is a
  real failure with the probe output verbatim -- never a silent skip;
* every subprocess wait has a finite timeout, with a terminate-then-kill
  fallback in a finally so a failing test leaks no processes (the app child,
  the fake driver or the fake browser helper are all reaped via psutil).

Dependencies are declared through tests/_deps.py: the test runs whenever the
environment can run the app (no skips in a properly provisioned CI).
"""
from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tests import _deps  # dependency policy: explicit skips only, never silent ones

APP_TIMEOUT = 30        # a healthy app quits in ~1-2 s (original budget kept)
DRAIN_TIMEOUT = 10      # pipe drain after a kill (leaked children may hold them)
TERM_TIMEOUT = 5        # terminate-then-kill fallback, both bounded
KILL_TIMEOUT = 5
PROBE_TIMEOUT = 60      # first-time PyQt6 import can be slow on cold caches
SURVIVOR_WAIT = 3.0     # bounded grace before declaring a survivor a leak

SCRIPT = textwrap.dedent('''
    import os, sys, subprocess, threading, time
    sys.path.insert(0, {root!r})
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["TN_DATA_DIR"] = {data!r}
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import QTimer
    from core import procs
    from core.store import History, Settings
    from ui.ctx import Ctx
    from ui.theme import theme
    from ui.window import AppWindow

    app = QApplication([])
    app.aboutToQuit.connect(procs.kill_all)
    theme.apply(app, "dark", None)
    ctx = Ctx(Settings(os.path.join({data!r}, "s.json")), History(os.path.join({data!r}, "h.json")))
    AppWindow.watchdog_enabled = True
    win = AppWindow(ctx)
    win.show()

    # a "browser": a driver that spawned a helper, both tracked like the engine does
    child = subprocess.Popen([sys.executable, "-c",
        "import subprocess,sys,time; h=subprocess.Popen([sys.executable,'-c','import time; time.sleep(600)']);"
        "print(h.pid, flush=True); time.sleep(600)"], stdout=subprocess.PIPE, text=True)
    helper = int(child.stdout.readline())
    procs.register(child.pid)
    open({pids!r}, "w").write(f"{{child.pid}} {{helper}}")

    # a stuck non-daemon worker thread that would normally keep Python alive forever
    threading.Thread(target=lambda: time.sleep(600)).start()

    QTimer.singleShot(400, lambda: win.close(force=True))
    code = app.exec()
    procs.hard_exit(code)
''')

PROBE = textwrap.dedent('''
    import os, sys
    sys.path.insert(0, {root!r})
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    import PyQt6.QtCore                                   # noqa: F401
    import PyQt6.QtWidgets                                # noqa: F401
    from core import procs, store                         # noqa: F401
    from ui import ctx, theme, window                     # noqa: F401
    print("probe-ok")
''')


def _verbatim(out: str, err: str) -> str:
    """Child output, verbatim, for inclusion in assertion failure messages."""
    return ("child stdout:\n" + (out or "<empty>")
            + "\nchild stderr:\n" + (err or "<empty>"))


def _alive(pid: int) -> bool:
    """True while the pid exists and is not a zombie (psutil-verified)."""
    import psutil
    try:
        p = psutil.Process(pid)
        return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:                     # NoSuchProcess etc.: gone
        return False


def _kill_tree(pid: int) -> None:
    """Terminate-then-kill a process and its whole subtree (bounded waits).

    Cleanup path only -- a failing test must leave nothing in Task Manager.
    """
    import psutil
    try:
        root = psutil.Process(pid)
    except psutil.Error:
        return
    victims = [root, *root.children(recursive=True)]
    for p in victims:
        with contextlib.suppress(psutil.Error):
            p.terminate()
    _gone, survivors = psutil.wait_procs(victims, timeout=TERM_TIMEOUT)
    for p in survivors:
        with contextlib.suppress(psutil.Error):
            p.kill()
    psutil.wait_procs(survivors, timeout=KILL_TIMEOUT)


class TestQuit(unittest.TestCase):
    def _probe_child_deps(self) -> None:
        """Pre-flight: the child interpreter must be able to import what the
        app needs.  ImportError -> explicit skip naming the missing module;
        anything else -> a real failure with the probe output verbatim."""
        try:
            probe = subprocess.run(
                [sys.executable, "-c", PROBE.format(root=ROOT)],
                capture_output=True, text=True, errors="replace",
                timeout=PROBE_TIMEOUT, cwd=ROOT, check=False)
        except subprocess.TimeoutExpired as exc:
            self.fail(f"pre-flight import probe timed out after {PROBE_TIMEOUT}s\n"
                      f"probe stdout: {exc.stdout!r}\nprobe stderr: {exc.stderr!r}")
        if probe.returncode == 0:
            return
        missing = _deps.missing_modules_from_traceback(probe.stderr)
        if missing:
            raise unittest.SkipTest(_deps.child_missing_skip_reason(
                missing, "the quit lifecycle (app + browser tree)"))
        self.fail(f"pre-flight import probe failed with exit {probe.returncode} -- "
                  "this is a real error, not a missing dependency\n"
                  + _verbatim(probe.stdout, probe.stderr))

    def setUp(self):
        self._probe_child_deps()

    def test_quit_ends_process_and_browser_tree(self):
        d = tempfile.mkdtemp(prefix="tn-quit-")
        pids = os.path.join(d, "pids.txt")
        script = os.path.join(d, "run_quit.py")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(SCRIPT.format(root=ROOT, data=d, pids=pids))
        t0 = time.monotonic()
        proc = subprocess.Popen([sys.executable, script], cwd=d,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, errors="replace")
        out = err = ""
        try:
            try:
                out, err = proc.communicate(timeout=APP_TIMEOUT)
            except subprocess.TimeoutExpired:
                # app refused to quit: keep whatever it printed, then tear down
                proc.kill()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    out, err = proc.communicate(timeout=DRAIN_TIMEOUT)
                self.fail(f"app did not exit after quit ({APP_TIMEOUT}s timeout)\n"
                          + _verbatim(out, err))
            elapsed = time.monotonic() - t0
            self.assertEqual(proc.returncode, 0,
                             "app exited with an error -- the hard exit must be clean\n"
                             + _verbatim(out, err))
            self.assertLess(elapsed, 20, "quit took too long\n" + _verbatim(out, err))
            # the non-daemon worker thread in the script must not have kept
            # Python alive (returncode set within APP_TIMEOUT above proves it)
            with open(pids, encoding="utf-8") as fh:
                child, helper = (int(x) for x in fh.read().split())
            deadline = time.monotonic() + SURVIVOR_WAIT
            while time.monotonic() < deadline and (_alive(child) or _alive(helper)):
                time.sleep(0.1)
            self.assertFalse(_alive(child),
                             "driver process survived quit\n" + _verbatim(out, err))
            self.assertFalse(_alive(helper),
                             "browser helper process survived quit\n"
                             + _verbatim(out, err))
        finally:
            # never leak processes, even when an assertion failed: reap the
            # app child (if still alive) and anything it spawned or tracked
            if proc.poll() is None:
                _kill_tree(proc.pid)
            with contextlib.suppress(OSError, ValueError), \
                    open(pids, encoding="utf-8") as fh:
                for pid in (int(x) for x in fh.read().split()):
                    _kill_tree(pid)


if __name__ == "__main__":
    unittest.main(verbosity=2)
