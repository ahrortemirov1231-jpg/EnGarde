@echo off
REM Engarde Analytics - TEAM SERVER. Runs the app on this PC for the whole team, with sign-in.
REM Keep this window open: closing it takes the site down for everyone. See docs\TEAM_SERVER.md.
setlocal
cd /d "%~dp0"
title Engarde team server
if not exist .venv\Scripts\activate.bat (
  echo Setup has not been run on this PC yet. Run setup.bat first, then start this again.
  pause
  exit /b 1
)
netstat -ano | findstr /r /c:":8000 .*LISTENING" >nul && (
  echo Something is already running on port 8000 - probably start.bat or another team server window.
  echo Close that black window first, then start this again.
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
set "PYTHONUTF8=1"
set "PATH=%CD%\.venv\Scripts;%PATH%"
set "ENGARDE_DATA=%CD%\data"
set "ENGARDE_TEAM=1"
python -W ignore scripts\team_admin.py --data "%ENGARDE_DATA%" first-admin || (
  echo.
  echo The first admin account was not created, so the server was not started.
  pause
  exit /b 1
)
echo.
echo TEAM SERVER is running. Keep this window open; closing it takes the site down for everyone.
echo   On this PC:    http://127.0.0.1:8000
echo   For the team:  the https address that team_link_on.bat shows
echo The PC will not go to sleep while this window is open.
echo.
python -W ignore -m uvicorn engarde.api:app --app-dir backend --host 127.0.0.1 --port 8000
echo.
echo The team server stopped. If this was not on purpose, the error is above.
pause
