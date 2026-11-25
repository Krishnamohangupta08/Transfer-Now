"""Launch smoke for a frozen macOS build — run on macos-latest from .github/workflows/ci.yml.

    python scripts/launch_smoke_macos.py [path/to/TransferNow]

Boots the packaged app (onedir bundle binary: dist/onedir/TransferNow.app/Contents/MacOS/TransferNow)
with QT_QPA_PLATFORM=offscreen and TN_DATA_DIR pointed at a fresh temp dir, waits up
to 60 s for it to stay alive AND write its first-start data (sounds/*.wav —
main.py -> ui.sounds.setup() writes these on every first launch), then SIGTERMs
it, verifies the exit code and that no orphaned TransferNow children survive
(pgrep). Exit 0 = pass, non-zero = fail.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time

APP_NAME = "TransferNow"
# build.py result_path("onedir") on macOS: dist/onedir/TransferNow.app (bundle),
# executable at Contents/MacOS/TransferNow
DEFAULT_BIN = os.path.join("dist", "onedir", f"{APP_NAME}.app", "Contents", "MacOS", APP_NAME)
ALIVE_SECONDS = 20          # stability window once "alive + data written" is seen
DEADLINE_SECONDS = 60       # overall budget for launch + data marker
TERM_GRACE_SECONDS = 15     # SIGTERM -> exit grace before SIGKILL fails the smoke


def fail(msg: str) -> "NoReturn":  # type: ignore[valid-type]
    print(f"LAUNCH-SMOKE-FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)


def find_data_marker(data_dir: pathlib.Path) -> tuple[bool, str]:
    sounds = data_dir / "sounds"
    if sounds.is_dir():
        wavs = sorted(p.name for p in sounds.glob("*.wav"))
        if wavs:
            return True, f"{len(wavs)} wav files (first: {wavs[0]})"
        return False, "sounds/ dir exists but no .wav written yet"
    return False, "no sounds/ dir yet"


def orphans() -> list[str]:
    """pgrep for any surviving TransferNow process (excluding this script's pid)."""
    me = str(os.getpid())
    out = subprocess.run(["pgrep", "-f", APP_NAME], capture_output=True, text=True, check=False)
    return [p for p in out.stdout.split() if p and p != me]


def main() -> int:
    binary = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BIN).resolve()
    if not binary.is_file():
        fail(f"built binary not found: {binary} (expected build.py result_path bundle binary)")
    if sys.platform != "darwin":
        fail(f"this smoke is macOS-only, sys.platform={sys.platform!r}")

    data_dir = pathlib.Path(tempfile.mkdtemp(prefix="tn-smoke-mac-"))
    env = dict(os.environ, TN_DATA_DIR=str(data_dir), QT_QPA_PLATFORM="offscreen")
    print(f"[smoke] binary  : {binary}")
    print(f"[smoke] data dir: {data_dir}")

    try:
        proc = subprocess.Popen([str(binary)], env=env, cwd=str(data_dir),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, errors="replace")
    except OSError as exc:
        fail(f"could not start {binary}: {exc}")

    launched_at = time.monotonic()
    alive = False
    marker = "never checked"
    deadline = launched_at + DEADLINE_SECONDS
    while time.monotonic() < deadline:
        rc = proc.poll()
        if rc is not None:
            out = proc.communicate()[0]
            fail(f"app exited early (rc={rc}) after {time.monotonic() - launched_at:.0f}s\n{out[-4000:]}")
        if not alive and time.monotonic() - launched_at >= 2:
            alive = True
        found, marker = find_data_marker(data_dir)
        if found and alive:
            break
        time.sleep(1.0)

    if not (alive and find_data_marker(data_dir)[0]):
        proc.kill()
        out = proc.communicate()[0]
        fail(f"app did not reach 'alive + data written' within {DEADLINE_SECONDS}s "
             f"(alive={alive}, marker={marker})\n{out[-4000:]}")
    print(f"[smoke] data marker: {marker}")

    stay_deadline = time.monotonic() + ALIVE_SECONDS
    while time.monotonic() < stay_deadline:
        if proc.poll() is not None:
            out = proc.communicate()[0]
            fail(f"app died during the {ALIVE_SECONDS}s stability window (rc={proc.returncode})\n{out[-4000:]}")
        time.sleep(1.0)
    print(f"[smoke] alive after {ALIVE_SECONDS}s")

    proc.send_signal(signal.SIGTERM)                       # clean quit path
    try:
        proc.wait(timeout=TERM_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)
        fail(f"app ignored SIGTERM for {TERM_GRACE_SECONDS}s")
    allowed = {0, 1, 143, -signal.SIGTERM}                # 143 = 128+15 (TERM)
    out = proc.communicate()[0]
    print(f"[smoke] terminated, exit code {proc.returncode}")
    if proc.returncode not in allowed:
        fail(f"unexpected exit code {proc.returncode} (allowed {sorted(allowed)})\n{out[-4000:]}")
    if "Traceback (most recent call last)" in out:
        fail(f"app printed a traceback at runtime:\n{out[-4000:]}")

    # no orphaned children may survive the parent's death
    time.sleep(2.0)                                        # let the reaper settle
    left = orphans()
    if left:
        fail(f"orphaned {APP_NAME} processes after quit: {left}")

    exe_dir_before = {p.name for p in binary.parent.iterdir()}
    if "settings.json" in exe_dir_before or "history.json" in exe_dir_before:
        fail("app wrote settings/history next to the binary — data not confined to TN_DATA_DIR")
    written = sorted(str(p.relative_to(data_dir)) for p in data_dir.rglob("*"))
    print(f"[smoke] confined writes OK: {len(written)} entries under TN_DATA_DIR")

    shutil.rmtree(data_dir, ignore_errors=True)
    print("[smoke] PASS: launched offscreen, stayed alive, wrote data under TN_DATA_DIR, "
          "clean SIGTERM, no orphans")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
