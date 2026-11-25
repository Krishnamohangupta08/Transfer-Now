"""Launch smoke for a frozen Windows build — run on windows-latest from .github/workflows/ci.yml.

    python scripts/launch_smoke_windows.py [path\\to\\TransferNow.exe]

Boots the packaged app with TN_DATA_DIR pointed at a fresh temp dir, waits up
to 60 s for it to stay alive AND to write its first-start data (sounds/*.wav —
main.py -> ui.sounds.setup() writes these on every first launch), then kills
it with taskkill and asserts the process is really gone.

Exit code 0 = smoke passed; non-zero (with a message on stderr) = failed.
No OS limitation is papered over here: if the app cannot launch headless on a
GitHub Windows runner, this script FAILS LOUDLY (see workflow comment).
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

APP_NAME = "TransferNow"
DEFAULT_EXE = os.path.join("dist", "onedir", APP_NAME, APP_NAME + ".exe")  # build.py result_path("onedir")
ALIVE_SECONDS = 20          # how long the app must stay up once detected
DEADLINE_SECONDS = 60       # overall budget for launch + data marker
KILL_GRACE_SECONDS = 30     # taskkill + wait for exit


def fail(msg: str) -> "NoReturn":  # type: ignore[valid-type]
    print(f"LAUNCH-SMOKE-FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)


def find_data_marker(data_dir: pathlib.Path) -> tuple[bool, str]:
    """(found, detail) — first-start marker is <TN_DATA_DIR>/sounds with .wav files."""
    sounds = data_dir / "sounds"
    if sounds.is_dir():
        wavs = sorted(p.name for p in sounds.glob("*.wav"))
        if wavs:
            return True, f"{len(wavs)} wav files (first: {wavs[0]})"
        return False, "sounds/ dir exists but no .wav written yet"
    return False, "no sounds/ dir yet"


def main() -> int:
    exe = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_EXE).resolve()
    if not exe.is_file():
        fail(f"built exe not found: {exe} (expected build.py result_path: {DEFAULT_EXE})")
    if os.name != "nt":
        fail(f"this smoke is Windows-only, os.name={os.name!r}")

    data_dir = pathlib.Path(tempfile.mkdtemp(prefix="tn-smoke-win-"))
    env = dict(os.environ, TN_DATA_DIR=str(data_dir))
    # Windows runners have an interactive desktop session; offscreen is set so a
    # missing display adapter can never be the reason the GUI fails to start.
    env["QT_QPA_PLATFORM"] = "offscreen"
    print(f"[smoke] exe      : {exe}")
    print(f"[smoke] data dir: {data_dir}")

    try:
        proc = subprocess.Popen([str(exe)], env=env, cwd=str(data_dir),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, errors="replace")
    except OSError as exc:
        fail(f"could not start {exe}: {exc}")

    alive = False
    marker = "never checked"
    deadline = time.monotonic() + DEADLINE_SECONDS
    while time.monotonic() < deadline:
        rc = proc.poll()
        if rc is not None:
            out = proc.communicate()[0]
            fail(f"app exited early (rc={rc}) after {DEADLINE_SECONDS - (deadline - time.monotonic()):.0f}s\n{out[-4000:]}")
        if not alive and time.monotonic() - (deadline - DEADLINE_SECONDS) >= 2:
            alive = True          # still running 2 s after launch
        found, marker = find_data_marker(data_dir)
        if found and alive:
            break
        time.sleep(1.0)

    if not (alive and marker and find_data_marker(data_dir)[0]):
        proc.kill()
        out = proc.communicate()[0]
        fail(f"app did not reach 'alive + data written' within {DEADLINE_SECONDS}s "
             f"(alive={alive}, marker={marker})\n{out[-4000:]}")

    # prove it stays alive (not a start-then-crash) and prints no traceback
    print(f"[smoke] data marker: {marker}")
    stay_deadline = time.monotonic() + ALIVE_SECONDS
    while time.monotonic() < stay_deadline:
        if proc.poll() is not None:
            out = proc.communicate()[0]
            fail(f"app died during the {ALIVE_SECONDS}s stability window (rc={proc.returncode})\n{out[-4000:]}")
        time.sleep(1.0)
    print(f"[smoke] alive after {ALIVE_SECONDS}s")

    # hard kill like a user closing a hung app, then assert it is gone
    subprocess.run(["taskkill", "/IM", f"{APP_NAME}.exe", "/F"], check=False,
                   capture_output=True, text=True)
    gone_deadline = time.monotonic() + KILL_GRACE_SECONDS
    while time.monotonic() < gone_deadline:
        if proc.poll() is not None:
            break
        time.sleep(1.0)
    if proc.poll() is None:
        fail(f"process still running {KILL_GRACE_SECONDS}s after taskkill")
    out = proc.communicate()[0]
    print(f"[smoke] terminated, exit code {proc.returncode}")
    if "Traceback (most recent call last)" in out:
        fail(f"app printed a traceback at runtime:\n{out[-4000:]}")

    # writes must be confined to TN_DATA_DIR (spot-check: nothing in the exe dir)
    exe_dir_before = {p.name for p in exe.parent.iterdir()}
    if "settings.json" in exe_dir_before or "history.json" in exe_dir_before:
        fail("app wrote settings/history next to the exe — data not confined to TN_DATA_DIR")
    written = sorted(p.name for p in data_dir.rglob("*"))
    print(f"[smoke] confined writes OK: {len(written)} entries under TN_DATA_DIR")

    shutil.rmtree(data_dir, ignore_errors=True)
    print("[smoke] PASS: launched, stayed alive, wrote data under TN_DATA_DIR, killed cleanly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
