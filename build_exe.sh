#!/usr/bin/env bash
# Transfer Now -> standalone app builder (macOS / Linux). For a Windows .exe use build_exe.bat on Windows.
# A .zip is always created in dist/ (ready to upload).
#   ./build_exe.sh [onefile|onedir|both]
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv && .venv/bin/pip install -q --upgrade pip && .venv/bin/pip install -q -r requirements.txt
fi
.venv/bin/pip install -q -r requirements-build.txt
exec .venv/bin/python build.py "${1:-both}" --zip
