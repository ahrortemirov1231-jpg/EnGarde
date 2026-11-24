@echo off
REM Engarde Analytics on Tim's data (data_tim folder), at http://127.0.0.1:8002. Close this window to stop.
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\activate.bat (
  echo Setup has not been run on this PC yet. Run setup.bat first.
  pause
  exit /b 1
)
if not exist data_tim\tim_import.json (
  echo Tim's data is not loaded yet. Run tim_data.bat first.
  pause
  exit /b 1
)
netstat -ano | findstr /r /c:":8002 .*LISTENING" >nul && (
  echo Something is already running on port 8002 - probably another start_tim window. Close it first.
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
set "PYTHONUTF8=1"
set "PATH=%CD%\.venv\Scripts;%PATH%"
set "ENGARDE_DATA=%CD%\data_tim"
start "" cmd /c "timeout /t 4 >nul & start http://127.0.0.1:8002"
echo Engarde Analytics (Tim's data) is running at http://127.0.0.1:8002 - keep this window open; close it to stop.
python -W ignore -m uvicorn engarde.api:app --app-dir backend --host 127.0.0.1 --port 8002
pause
