"""Render every page/state in dark + light to PNG for visual QA (1.0.0.0).

    QT_QPA_PLATFORM=offscreen python tools/render_shots.py [outdir]
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time as _t
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
data = tempfile.mkdtemp(prefix="tn-shots-")
os.environ["TN_DATA_DIR"] = data

from PyQt6.QtWidgets import QApplication  # noqa: E402

from core.store import History, Settings  # noqa: E402
from ui.ctx import Ctx  # noqa: E402
from ui.theme import theme  # noqa: E402
from ui.window import AppWindow  # noqa: E402


def seed() -> None:
    now = datetime.now().astimezone()

    def e(kind, days_ago, hours, files, link="", exp="7 days"):
        return {"id": f"{kind}{days_ago}{hours}", "kind": kind,
                "date": (now - timedelta(days=days_ago, hours=hours)).isoformat(timespec="seconds"),
                "files": [{"name": n, "size": s} for n, s in files],
                "total_bytes": sum(s for _n, s in files), "link": link, "expiry": exp,
                "email": "sarah.miller21@gmail.com",
                "save_path": "/home/u/Downloads/x" if kind == "download" else ""}
    rows = [
        e("upload", 0, 1, [("Q4-board-deck.pdf", 18_200_000)], "https://www.transfernow.net/dl/AbC123xyz"),
        e("download", 0, 3, [("TransferNow-20261101.zip", 25_600_000)], "https://www.transfernow.net/dl/Zz9"),
        e("upload", 1, 2, [("brand-assets.zip", 88_000_000), ("logo.svg", 12_000)], "https://www.transfernow.net/dl/Qw3rty"),
        e("upload", 2, 5, [("notes.txt", 81_000)], "https://www.transfernow.net/dl/Nn22", "7 days"),
        e("upload", 3, 2, [("release-candidate.iso", 640_000_000)], "https://www.transfernow.net/dl/Rc44"),
        e("upload", 4, 1, [("holiday-photos.zip", 1_200_000_000)], "https://www.transfernow.net/dl/Old1"),
        e("upload", 5, 1, [("sprint-review.mp4", 220_000_000)], "https://www.transfernow.net/dl/Sp66"),
        e("upload", 6, 1, [("backup-2026.zip", 95_000_000)], "https://www.transfernow.net/dl/Bk77"),
        e("download", 6, 4, [("contract_signed.pdf", 3_100_000)], "https://www.transfernow.net/dl/Dd8"),
        e("upload", 9, 1, [("old-report.docx", 2_400_000)], "https://www.transfernow.net/dl/Old2"),
    ]
    with open(os.path.join(data, "history.json"), "w") as f:
        json.dump(rows, f)


def main() -> None:
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "screenshots")
    os.makedirs(out, exist_ok=True)
    seed()
    app = QApplication([])
    s, h = Settings(), History()
    ctx = Ctx(s, h)
    sample = tempfile.mkdtemp(prefix="tn-shots-")
    with open(os.path.join(sample, "sample.zip"), "wb") as fh:
        fh.write(os.urandom(2 * 1024 * 1024))
    with open(os.path.join(sample, "notes.txt"), "w", encoding="utf-8") as fh:
        fh.write("sample notes\n" * 200)
    for mode in ("dark", "light"):
        theme.apply(app, mode, None)
        w = AppWindow(ctx)
        w.resize(1240, 800)
        w.show()

        def shot(name: str) -> None:
            for _ in range(4):
                app.processEvents()
            w.grab().save(os.path.join(out, f"{name}_{mode}.png"))

        w.navigate("dashboard"); shot("dashboard")
        w.navigate("upload"); shot("upload_empty")
        up = w.pages["upload"]
        up.add_paths([os.path.join(sample, "sample.zip"), os.path.join(sample, "notes.txt")])
        shot("upload_files")
        from ui.pages.upload import UploadJob
        up._max_parallel = lambda: 0            # keep demo jobs from launching a browser
        sizes = {f: 1_000_000 for f in up.files}
        a = UploadJob(list(up.files), sizes, "a@b.com"); up._add_job(a)
        a.state, a.pct, a.step = "running", 48, 3
        a.started = _t.monotonic() - 12
        a.speed = 1_840_000.0
        a.speed_samples = [220_000, 640_000, 980_000, 1_240_000, 1_610_000, 1_480_000,
                           1_900_000, 2_050_000, 1_780_000, 1_960_000, 2_110_000, 1_840_000]
        b = UploadJob(list(up.files[:1]), sizes, "a@b.com"); up._add_job(b)
        c = UploadJob(list(up.files[1:]), sizes, "a@b.com"); up._add_job(c)
        c.state, c.link, c.elapsed = "done", "https://www.transfernow.net/dl/DemoLink123", 17
        d = UploadJob(list(up.files[:1]), sizes, "a@b.com"); up._add_job(d)
        d.state, d.error = "failed", "Could not start a browser. Install Google Chrome."
        sch = UploadJob(list(up.files[:1]), sizes, "a@b.com",
                        scheduled_at=_t.monotonic() + 480)
        up._add_job(sch)
        for j in up.jobs:
            up._card(j)
        up.clear_files()
        up.add_paths([os.path.join(sample, "notes.txt")])
        shot("upload_queue")
        w.navigate("queue"); shot("queue")           # dedicated queue page (same demo jobs)
        for j in list(up.jobs):
            up._remove_job(j)
        w.navigate("download"); shot("download_empty")
        dp = w.pages["download"]
        dp._token += 1
        dp.link.setText("https://www.transfernow.net/dl/abcdef")
        dp._debounce.stop()
        dp._on_info(dp._token, {"transfer_id": "abcdef", "link": "x", "title": "Files are ready!",
            "files": [{"name": "report.pdf", "size": 3_400_000}, {"name": "photo.jpg", "size": 5_100_000}],
            "total_bytes": 8_500_000, "expiry": "Nov 09, 2026"})
        shot("download_info")
        dp._set_state(3)
        dp._on_progress(4_000_000, 8_500_000, 2_300_000.0)
        dp.graph.set_values([420_000, 900_000, 1_400_000, 1_900_000, 2_600_000, 2_300_000,
                             2_450_000, 2_100_000])
        shot("download_running")
        ctx.activity.emit("", -1)                  # idle status bar for the remaining shots
        app.processEvents()
        w.navigate("history"); shot("history")
        w.navigate("settings"); shot("settings")
        w.close()
    print("saved to", out)


if __name__ == "__main__":
    main()
