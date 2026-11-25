#!/usr/bin/env python3
"""Transfer Now release gate — local provenance runner mirroring CI exactly.

Runs, in one process each, the same stages .github/workflows/ci.yml runs:
compileall, full test discovery (per module, with skip reasons), bandit
(-ll, blocking), pip-audit (requirements.txt, blocking) and, with
--with-build, python build.py onedir plus the frozen smoke
(TN_BUILD_SMOKE=1 tests.test_packaging). Results are written to
release_results.json next to the repo root (or --out PATH):

    {
      "generated_utc": ..., "git_head": ...|null, "platform": {...},
      "python": {...}, "dependencies": {...},
      "stages": {"compileall": {...}, "tests": {...}, "bandit": {...},
                 "pip_audit": ..., "build": ..., "smoke": ...},
      "summary": {"total_discovered": N, "passed": N, "failed": N,
                  "errors": N, "skipped": N, "gate": "PASS"|"FAIL"}
    }

Exit code 0 only when every BLOCKING stage passed (lint/ruff is advisory in
CI and is not run here; run `ruff check .` separately to see findings).

Usage:
    python scripts/release_validate.py                 # gate: tests + security scans
    python scripts/release_validate.py --with-build    # also build + frozen smoke
    python scripts/release_validate.py --help
"""
from __future__ import annotations

import argparse
import io
import json
import os
import platform as pyplatform
import subprocess
import sys
import time
import unittest
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

RESULTS_DEFAULT = os.path.join(ROOT, "release_results.json")

# Mirrors the CI job list: (module, blocking, env). tests.test_network_policy
# and tests.test_linkcheck_security are the 1.0.2 security-track modules; if
# one is missing at run time it is recorded honestly as "missing", never
# silently dropped. NOTE: TN_TEST_LOCAL_HTTP is deliberately NOT set here —
# the downloader suite patches it in via mock.patch.dict where needed, and a
# global export inverts its three flag-gating negative tests (verified
# 2026-11-24: exactly 3 failures, "TransferLinkError not raised").
TEST_MODULES: list[tuple[str, bool, dict[str, str]]] = [
    ("tests.test_core", True, {"QT_QPA_PLATFORM": "offscreen"}),
    ("tests.test_quit", True, {"QT_QPA_PLATFORM": "offscreen"}),
    ("tests.test_security", True, {}),
    ("tests.test_network_policy", True, {}),
    ("tests.test_linkcheck_security", True, {}),
    ("tests.test_downloader_hardening", True, {}),
    ("tests.test_limits_zip", True, {}),
    ("tests.test_ui", True, {"QT_QPA_PLATFORM": "offscreen"}),
    ("tests.test_ui_regressions", True, {"QT_QPA_PLATFORM": "offscreen"}),
    ("tests.test_extras", True, {"QT_QPA_PLATFORM": "offscreen"}),
    ("tests.test_changelog", True, {"QT_QPA_PLATFORM": "offscreen"}),
    ("tests.test_qr_export", True, {"QT_QPA_PLATFORM": "offscreen"}),
    ("tests.test_upload_limit_ui", True, {"QT_QPA_PLATFORM": "offscreen"}),
    ("tests.test_packaging", True, {"QT_QPA_PLATFORM": "offscreen"}),
]

RUNTIME_DEPS = ["PyQt6", "selenium", "requests", "psutil", "qrcode"]
DEV_DEPS = ["ruff", "bandit", "pip-audit"]


# --------------------------------------------------------------- helpers
def run(cmd: list[str], env: dict[str, str] | None = None, timeout: int = 1800,
        cwd: str = ROOT) -> subprocess.CompletedProcess:
    """Run a subprocess with the repo env merged; never raises."""
    full_env = dict(os.environ)
    full_env.setdefault("QT_QPA_PLATFORM", "offscreen")
    if env:
        full_env.update(env)
    return subprocess.run(cmd, cwd=cwd, env=full_env, timeout=timeout,
                          capture_output=True, text=True, errors="replace")


def tail(text: str, n: int = 30) -> str:
    lines = (text or "").splitlines()
    return "\n".join(lines[-n:])


def git_head() -> str | None:
    try:
        cp = run(["git", "rev-parse", "HEAD"], timeout=30)
        return cp.stdout.strip() if cp.returncode == 0 else None
    except Exception:  # noqa: BLE001 — no git / detached sandbox
        return None


def dep_versions() -> dict[str, str | None]:
    from importlib import metadata
    out: dict[str, str | None] = {}
    for name in RUNTIME_DEPS + DEV_DEPS:
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:  # pragma: no cover
            out[name] = None
    return out


# ----------------------------------------------------------------- stages
def stage_compileall() -> dict:
    """python -m compileall on the same trees CI compiles (blocking)."""
    cp = run([sys.executable, "-m", "compileall", "-q",
              "core", "ui", "tests", "tools", "build.py", "main.py", "scripts"])
    return {"blocking": True, "command": "python -m compileall -q core ui tests tools build.py main.py scripts",
            "returncode": cp.returncode, "passed": cp.returncode == 0,
            "output_tail": tail(cp.stdout + cp.stderr)}


def _module_env(name: str) -> dict[str, str]:
    for mod, _blocking, env in TEST_MODULES:
        if mod == name:
            return env
    return {}


def stage_tests() -> dict:
    """Full discovery per module, in a fresh process each (mirrors CI), with
    per-module discovered/passed/failed/errors/skipped counts + skip reasons."""
    results: dict[str, dict] = {}
    totals = {"discovered": 0, "passed": 0, "failed": 0, "errors": 0,
              "skipped": 0}

    for module, blocking, _env in TEST_MODULES:
        entry: dict = {"blocking": blocking}
        path = os.path.join(ROOT, module.replace(".", os.sep) + ".py")
        if not os.path.exists(path):
            entry.update({"status": "missing", "discovered": 0, "passed": 0,
                          "failed": 0, "errors": 0, "skipped": 0,
                          "skip_reasons": ["module file not present in tree yet"],
                          "duration_s": 0.0, "ok": False,
                          "output_tail": None})
            results[module] = entry
            continue

        env = dict(_module_env(module))
        env["TN_DATA_DIR"] = env.get("TN_DATA_DIR") or os.path.join(
            ROOT, "build", f"_release_data_{module.replace('.', '_')}")

        started = time.monotonic()
        cp = run([sys.executable, "-m", "unittest", module, "-v"],
                 env=env, timeout=1800)
        entry.update(_parse_text(cp.stdout + cp.stderr))
        entry["duration_s"] = round(time.monotonic() - started, 2)
        entry["returncode"] = cp.returncode
        entry["ok"] = cp.returncode == 0
        entry["output_tail"] = tail(cp.stdout + cp.stderr)
        for k in ("discovered", "passed", "failed", "errors", "skipped"):
            totals[k] += entry.get(k, 0)
        results[module] = entry

    return {"blocking": True, "modules": results, "totals": totals}


def _parse_text(output: str) -> dict:
    """Parse `python -m unittest -v` output for counts and skip reasons."""
    counts = {"discovered": 0, "passed": 0, "failed": 0, "errors": 0,
              "skipped": 0, "skip_reasons": []}
    for line in output.splitlines():
        line = line.strip()
        if " ... ok" in line:
            counts["passed"] += 1
        elif " ... ERROR" in line:
            counts["errors"] += 1
        elif " ... FAIL" in line:
            counts["failed"] += 1
        elif " ... skipped" in line:
            counts["skipped"] += 1
            reason = line.split("skipped", 1)[1].strip().strip("'\"")
            counts["skip_reasons"].append(f"{line.split(' ... ')[0]}: {reason}")
        elif line.startswith("Ran ") and " test" in line:
            try:
                counts["discovered"] = int(line.split()[1])
            except (ValueError, IndexError):
                pass
    if counts["discovered"] == 0:
        counts["discovered"] = counts["passed"] + counts["failed"] + counts["errors"] + counts["skipped"]
    else:  # trust "Ran N"; recompute passed so totals stay consistent
        counts["passed"] = (counts["discovered"] - counts["failed"]
                            - counts["errors"] - counts["skipped"])
    return counts


def stage_bandit() -> dict:
    """python -m bandit -r core ui -q -ll (blocking, same flags as CI)."""
    cp = run([sys.executable, "-m", "bandit", "-r", "core", "ui", "-q", "-ll"])
    return {"blocking": True, "command": "python -m bandit -r core ui -q -ll",
            "returncode": cp.returncode, "passed": cp.returncode == 0,
            "output_tail": tail(cp.stdout + cp.stderr)}


def stage_pip_audit() -> dict:
    """python -m pip_audit -r requirements.txt (blocking, same as CI)."""
    cp = run([sys.executable, "-m", "pip_audit", "-r", "requirements.txt"],
             timeout=900)
    return {"blocking": True, "command": "python -m pip_audit -r requirements.txt",
            "returncode": cp.returncode, "passed": cp.returncode == 0,
            "output_tail": tail(cp.stdout + cp.stderr)}


def stage_build(with_build: bool) -> dict | None:
    """python build.py onedir --skip-icon + sha256 of the outputs."""
    if not with_build:
        return None
    cp = run([sys.executable, "build.py", "onedir", "--skip-icon"], timeout=1800)
    stage = {"blocking": True, "command": "python build.py onedir --skip-icon",
             "returncode": cp.returncode, "passed": cp.returncode == 0,
             "output_tail": tail(cp.stdout + cp.stderr, 40)}
    import hashlib
    import build as build_mod
    out = build_mod.result_path("onedir")
    targets = [out] if os.path.isfile(out) else []
    if os.path.isdir(out):  # macOS .app bundle: hash the executable + list
        exe = os.path.join(out, "Contents", "MacOS", "TransferNow")
        if os.path.isfile(exe):
            targets = [exe]
    stage["outputs"] = []
    for t in targets:
        h = hashlib.sha256()
        with open(t, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        stage["outputs"].append({"path": os.path.relpath(t, ROOT),
                                 "sha256": h.hexdigest(),
                                 "bytes": os.path.getsize(t)})
    return stage


def stage_smoke(with_build: bool, build_stage: dict | None) -> dict | None:
    """TN_BUILD_SMOKE=1 python -m unittest tests.test_packaging -v (blocking).

    The suite boots the frozen binary for this OS, checks it stays alive,
    SIGTERMs it and asserts data was written under TN_DATA_DIR.
    """
    if not with_build or not build_stage or not build_stage.get("passed"):
        return None
    data_dir = os.path.join(ROOT, "build", "_release_smoke_data")
    os.makedirs(data_dir, exist_ok=True)
    cp = run([sys.executable, "-m", "unittest", "tests.test_packaging", "-v"],
             env={"TN_BUILD_SMOKE": "1", "TN_DATA_DIR": data_dir,
                  "QT_QPA_PLATFORM": "offscreen"},
             timeout=1800)
    parsed = _parse_text(cp.stdout + cp.stderr)
    return {"blocking": True,
            "command": "TN_BUILD_SMOKE=1 TN_DATA_DIR=build/_release_smoke_data "
                       "python -m unittest tests.test_packaging -v",
            "returncode": cp.returncode, "passed": cp.returncode == 0,
            "counts": parsed, "output_tail": tail(cp.stdout + cp.stderr, 40)}


# ------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(
        description="Transfer Now release gate - local provenance runner (mirrors CI).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Writes release_results.json; exit 0 only if every blocking stage passed.")
    ap.add_argument("--with-build", action="store_true",
                    help="also run python build.py onedir and the frozen smoke")
    ap.add_argument("--out", default=RESULTS_DEFAULT,
                    help=f"results JSON path (default: {RESULTS_DEFAULT})")
    ap.add_argument("--skip-tests", action="store_true",
                    help="compileall + scans only (still honest in the JSON)")
    args = ap.parse_args()

    print("=== Transfer Now release gate ===")
    results: dict = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "app_version": _app_version(),
        "git_head": git_head(),
        "git_head_note": "git rev-parse HEAD at gate run time (null outside a repo)",
        "platform": {"system": pyplatform.system(), "release": pyplatform.release(),
                     "machine": pyplatform.machine(), "python_implementation":
                     pyplatform.python_implementation()},
        "python": {"version": sys.version.split()[0], "executable": sys.executable},
        "dependencies": dep_versions(),
        "stages": {},
    }

    stages = results["stages"]

    print("[1/5] compileall ...")
    stages["compileall"] = stage_compileall()
    print("      ->", "PASS" if stages["compileall"]["passed"] else "FAIL")

    if args.skip_tests:
        stages["tests"] = {"blocking": True, "skipped_by_flag": True,
                           "modules": {}, "totals": None}
        print("[2/5] tests: SKIPPED by --skip-tests")
    else:
        print("[2/5] test modules (one process each, mirrors CI) ...")
        stages["tests"] = stage_tests()
        t = stages["tests"]["totals"]
        print(f"      -> discovered={t['discovered']} passed={t['passed']} "
              f"failed={t['failed']} errors={t['errors']} skipped={t['skipped']}")
        for mod, entry in stages["tests"]["modules"].items():
            if entry.get("status") == "missing":
                print(f"      !! {mod}: MISSING from tree (recorded honestly)")

    print("[3/5] bandit -ll ...")
    stages["bandit"] = stage_bandit()
    print("      ->", "PASS" if stages["bandit"]["passed"] else "FAIL")

    print("[4/5] pip-audit ...")
    stages["pip_audit"] = stage_pip_audit()
    print("      ->", "PASS" if stages["pip_audit"]["passed"] else "FAIL")

    print("[5/5] build + frozen smoke ...")
    stages["build"] = stage_build(args.with_build)
    if stages["build"] is not None:
        print("      -> build", "PASS" if stages["build"]["passed"] else "FAIL")
        for o in stages["build"].get("outputs", []):
            print(f"         {o['path']}  sha256={o['sha256'][:16]}... ({o['bytes']} bytes)")
    else:
        print("      -> skipped (no --with-build)")
    stages["smoke"] = stage_smoke(args.with_build, stages["build"])
    if stages["smoke"] is not None:
        print("      -> smoke", "PASS" if stages["smoke"]["passed"] else "FAIL")

    # ---- gate verdict: every blocking stage must pass
    gate_failures: list[str] = []
    for name, stage in stages.items():
        if not stage or not stage.get("blocking"):
            continue
        if stage.get("skipped_by_flag"):
            gate_failures.append(f"{name}: skipped via --skip-tests (not a gate pass)")
            continue
        if not stage.get("passed"):
            detail = ""
            if name == "tests":
                mods = [m for m, e in stage["modules"].items()
                        if e.get("blocking") and not e.get("ok")]
                detail = f" (failing modules: {', '.join(mods)})"
            gate_failures.append(f"{name}{detail}")
    gate = "FAIL" if gate_failures else "PASS"

    totals = stages["tests"].get("totals") or {}
    results["summary"] = {
        "total_discovered": totals.get("discovered", 0),
        "passed": totals.get("passed", 0),
        "failed": totals.get("failed", 0),
        "errors": totals.get("errors", 0),
        "skipped": totals.get("skipped", 0),
        "gate": gate,
        "gate_failures": gate_failures,
    }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"\nresults written: {os.path.abspath(args.out)}")
    print(f"GATE: {gate}")
    if gate_failures:
        for g in gate_failures:
            print(f"  - {g}")
    return 0 if gate == "PASS" else 1


def _app_version() -> str:
    try:
        from core.version import APP_VERSION  # noqa: PLC0415
        return APP_VERSION
    except Exception:  # noqa: BLE001
        return "unknown"


if __name__ == "__main__":
    raise SystemExit(main())
