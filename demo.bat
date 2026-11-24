@echo off
REM Engarde Analytics - load a SYNTHETIC demo (5 bouts of stick figures with labels) and open the app on it.
REM Uses a separate demo_data folder, so your real data\ folder is never touched. Close this window to stop.
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\activate.bat (echo Run setup.bat first. & pause & exit /b 1)
netstat -ano | findstr /r /c:":8001 .*LISTENING" >nul && (
  echo Something is already running on port 8001 - probably another Engarde window.
  echo Close the other black Engarde window, or open http://127.0.0.1:8001 if you meant to use it.
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
set "PYTHONUTF8=1"
set "PATH=%CD%\.venv\Scripts;%PATH%"
if exist demo_data (
  echo Removing the previous demo_data folder...
  rmdir /s /q demo_data
)
echo Loading 5 synthetic bouts with blind labels. This takes about 1-2 minutes...
python -W ignore scripts\demo_fixture.py --data demo_data --with-labels --bouts 5 --blind > demo_data_load.json || (echo demo load failed - see the messages above & pause & exit /b 1)
set "ENGARDE_DATA=%CD%\demo_data"
start "" cmd /c "timeout /t 4 >nul & start http://127.0.0.1:8001"
echo DEMO running at http://127.0.0.1:8001 (the demo address; your real app uses :8000) on SYNTHETIC data - keep this window open; close it to stop.
python -W ignore -m uvicorn engarde.api:app --app-dir backend --host 127.0.0.1 --port 8001
pause
