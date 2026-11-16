@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -m pip install pytest -q
".venv\Scripts\python.exe" -m pytest -q tests
pause
