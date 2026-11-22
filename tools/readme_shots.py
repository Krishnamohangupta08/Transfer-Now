"""Render the Dashboard (dark + light) for the README, with demo history.

    QT_QPA_PLATFORM=offscreen QT_SCALE_FACTOR=2 python tools/readme_shots.py [outdir]
"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import render_shots  # noqa: E402  (sets TN_DATA_DIR to a temp dir, provides seed())

from PyQt6.QtCore import QEventLoop  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core.store import History, Settings  # noqa: E402
from ui.ctx import Ctx  # noqa: E402
from ui.theme import theme  # noqa: E402
from ui.window import AppWindow  # noqa: E402


def main() -> None:
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "docs", "screenshots")
    os.makedirs(out, exist_ok=True)
    render_shots.seed()
    app = QApplication([])
    ctx = Ctx(Settings(), History())
    for mode in ("dark", "light"):
        theme.apply(app, mode, None)
        w = AppWindow(ctx)
        w.resize(1240, 880)
        w.show()
        w.navigate("dashboard")
        end = time.monotonic() + 2.5                 # let entrance animations finish
        while time.monotonic() < end:
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
            time.sleep(0.01)
        w.grab().save(os.path.join(out, f"dashboard-{mode}.png"))
        w.hide()
        w.deleteLater()


if __name__ == "__main__":
    main()
