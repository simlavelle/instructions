@echo off
rem Start Label Verification on Windows: double-click this file.
rem The first run installs what the app needs; later runs start in seconds.
setlocal
cd /d "%~dp0"

set "PY="
py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1 && set "PY=py -3"
if not defined PY (
  python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo.
  echo Python 3.10 or newer is needed. Download it from https://www.python.org/downloads/
  echo When installing, tick "Add python.exe to PATH". Then double-click start.bat again.
  echo.
  pause
  exit /b 1
)

%PY% run.py %*
if errorlevel 1 pause
