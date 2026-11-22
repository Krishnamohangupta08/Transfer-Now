"""Quitting must end the process and leave no browser behind (nothing in Task Manager).

Each test runs a fresh Python process, so the hard exit can really be observed.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _alive(pid: int) -> bool:
    try:
        import psutil
        p = psutil.Process(pid)
        return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
    except Exception:  # noqa: BLE001
        return False


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


class TestQuit(unittest.TestCase):
    def test_quit_ends_process_and_browser_tree(self):
        d = tempfile.mkdtemp(prefix="tn-quit-")
        pids = os.path.join(d, "pids.txt")
        script = os.path.join(d, "run_quit.py")
        open(script, "w").write(SCRIPT.format(root=ROOT, data=d, pids=pids))
        t0 = time.monotonic()
        proc = subprocess.Popen([sys.executable, script], cwd=d)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            self.fail("app did not exit after quit")
        elapsed = time.monotonic() - t0
        self.assertEqual(proc.returncode, 0)
        self.assertLess(elapsed, 20)
        child, helper = (int(x) for x in open(pids).read().split())
        time.sleep(0.5)
        self.assertFalse(_alive(child), "driver process survived quit")
        self.assertFalse(_alive(helper), "browser helper process survived quit")


if __name__ == "__main__":
    unittest.main()
