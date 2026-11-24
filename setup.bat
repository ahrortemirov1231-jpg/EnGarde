@echo off
REM Engarde Analytics - one-time setup on Windows. Double-click this file.
setlocal
cd /d "%~dp0"
echo == Engarde Analytics setup ==
echo "%CD%" | find /I "OneDrive" >nul && (
  echo.
  echo WARNING: this folder is inside OneDrive. OneDrive syncs and locks the thousands of files that
  echo setup creates. Copy the engarde-mvp folder to e.g. C:\engarde-mvp and run setup.bat there.
  echo.
  choice /M "Continue here anyway"
  if errorlevel 2 exit /b 1
)
set "PY="
for %%V in (3.13 3.12 3.11) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
  )
)
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo Python 3.11 or newer is required. Install it from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" during install, then run this again.
  pause & exit /b 1
)
%PY% --version
where ffmpeg >nul 2>&1 && where ffprobe >nul 2>&1 || (
  echo ffmpeg is required. In PowerShell run:  winget install Gyan.FFmpeg
  echo then close and reopen this window and run setup.bat again.
  pause & exit /b 1
)
if not exist .venv %PY% -m venv .venv
call .venv\Scripts\activate.bat || (echo could not activate .venv & pause & exit /b 1)
set "PYTHONUTF8=1"
set "PATH=%CD%\.venv\Scripts;%PATH%"
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt || (echo pip install failed - see the messages above & pause & exit /b 1)
if not exist weights mkdir weights
if not exist data mkdir data
if not exist weights\yolo11n-pose.pt (
  curl.exe -L --fail -o weights\yolo11n-pose.pt https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n-pose.pt || (echo weights download failed & pause & exit /b 1)
)
echo Expected weights SHA256: 869e83fcdffdc7371fa4e34cd8e51c838cc729571d1635e5141e3075e9319dc0
certutil -hashfile weights\yolo11n-pose.pt SHA256 | findstr /v /c:"hash"
if not exist frontend\dist\index.html (
  where npm >nul 2>&1 && (pushd frontend & call npm install & call npm run build & popd) || echo frontend\dist missing and npm not found
)
echo.
echo Setup complete. Double-click start.bat to open Engarde Analytics.
pause
