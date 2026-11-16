@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  python -m venv .venv
  if errorlevel 1 (
    echo Python 3.10 or newer is required. Install it from python.org and try again.
    pause
    exit /b 1
  )
)
if exist "wheels" (
  ".venv\Scripts\python.exe" -m pip install --no-index --find-links wheels -r requirements.txt -q
) else (
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt -q
)
".venv\Scripts\python.exe" run.py %*
pause
