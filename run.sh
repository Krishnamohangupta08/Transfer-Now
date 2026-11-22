#!/usr/bin/env bash
# Transfer Now launcher (macOS / Linux): creates a private virtual env on first run.
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Setting up Transfer Now for the first time..."
  python3 -m venv .venv && .venv/bin/pip install -q --upgrade pip && .venv/bin/pip install -q -r requirements.txt
fi
exec .venv/bin/python main.py
