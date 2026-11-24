@echo off
REM Engarde Analytics - start the local app and open it in the browser. Close this window to stop.
REM If setup has not been run yet, this runs setup.bat first.
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\activate.bat (
  echo Setup has not been run on this PC yet ^(no .venv folder^). Running setup.bat now...
  echo.
  call "%~dp0setup.bat"
  cd /d "%~dp0"
)
if not exist .venv\Scripts\activate.bat (
  echo.
  echo Setup did not finish, so the app cannot start. Read the messages above, fix the problem, and try again.
  pause
  exit /b 1
)
netstat -ano | findstr /r /c:":8000 .*LISTENING" >nul && (
  echo Something is already running on port 8000 - probably another Engarde window.
  echo Close the other black Engarde window, or open http://127.0.0.1:8000 if you meant to use it.
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
set "PYTHONUTF8=1"
set "PATH=%CD%\.venv\Scripts;%PATH%"
set "ENGARDE_DATA=%CD%\data"
start "" cmd /c "timeout /t 4 >nul & start http://127.0.0.1:8000"
echo Engarde Analytics is running at http://127.0.0.1:8000 - keep this window open; close it to stop.
python -W ignore -m uvicorn engarde.api:app --app-dir backend --host 127.0.0.1 --port 8000
echo.
echo The app stopped. If this was not on purpose, the error is above.
pause
