@echo off
REM Transfer Now -> .exe builder (Windows).
REM   build_exe.bat            asks what to build
REM   build_exe.bat onefile    single portable TransferNow.exe
REM   build_exe.bat onedir     folder build (starts faster)
REM   build_exe.bat both       both of the above
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
  echo Setting up a private build environment ^(first time only^)...
  py -3 -m venv .venv || python -m venv .venv
  .venv\Scripts\python.exe -m pip install --upgrade pip
  .venv\Scripts\python.exe -m pip install -r requirements.txt
)
.venv\Scripts\python.exe -m pip install -q -r requirements-build.txt

set MODE=%1
if "%MODE%"=="" (
  echo.
  echo   1  Single file   ^(dist\onefile\TransferNow.exe^)
  echo   2  Folder        ^(dist\onedir\TransferNow\TransferNow.exe^)
  echo   3  Both
  choice /c 123 /n /m "Build which? [1/2/3]: "
  if errorlevel 3 (set MODE=both) else if errorlevel 2 (set MODE=onedir) else (set MODE=onefile)
)

.venv\Scripts\python.exe build.py %MODE% --zip
echo.
pause
