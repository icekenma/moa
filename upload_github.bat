@echo off
setlocal
cd /d "%~dp0"
if not exist "run.py" goto wrong_folder
if not exist "docs\index.html" goto wrong_folder
if not exist "moa\app.py" goto wrong_folder
set "GIT=git"
where git >nul 2>nul
if not errorlevel 1 goto have_git
if exist "C:\Program Files\Git\cmd\git.exe" (
  set "GIT=C:\Program Files\Git\cmd\git.exe"
  goto have_git
)
echo Git is not installed. Installing Git with winget...
where winget >nul 2>nul
if errorlevel 1 (
  echo winget is not available. Install Git from https://git-scm.com/download/win and run this again.
  pause
  exit /b 1
)
winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements
if not exist "C:\Program Files\Git\cmd\git.exe" (
  echo Git install did not finish. Install it from https://git-scm.com/download/win and run this again.
  pause
  exit /b 1
)
set "GIT=C:\Program Files\Git\cmd\git.exe"
:have_git
if not exist ".git" (
  "%GIT%" init -b main
  "%GIT%" remote add origin https://github.com/icekenma/moa.git
)
"%GIT%" config user.name >nul 2>nul || "%GIT%" config user.name "icekenma"
"%GIT%" config user.email >nul 2>nul || "%GIT%" config user.email "icekenma@users.noreply.github.com"
"%GIT%" add -A
"%GIT%" commit -m "update moa"
echo.
echo A GitHub sign-in window may open. Sign in as icekenma.
"%GIT%" push -u origin main
if errorlevel 1 (
  echo.
  echo Push failed. Check the message above, then run this again.
  pause
  exit /b 1
)
echo.
echo Done. Next: GitHub repo Settings - Pages - Branch main, folder /docs
echo Phone screen: https://icekenma.github.io/moa/
pause
exit /b 0
:wrong_folder
echo.
echo STOP: this file must be inside the moa folder (next to run.py and run.bat).
echo Current folder: %CD%
echo Nothing was uploaded.
pause
exit /b 1
