@echo off
REM Transfer Now launcher (Windows): creates a private virtual env on first run.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Setting up Transfer Now for the first time...
  py -3 -m venv .venv || python -m venv .venv
  .venv\Scripts\python.exe -m pip install --upgrade pip
  .venv\Scripts\python.exe -m pip install -r requirements.txt
)
start "" .venv\Scripts\pythonw.exe main.py
