@echo off
setlocal
cd /d "%~dp0"
set "PATH=%CD%\.venv\Scripts;%PATH%"
set PYTHONUTF8=1
echo ============================================================
echo  Download USA Fencing sabre bouts into incoming\usafencing
echo  (up to 60 per run, slowly; run it again to get more or to resume)
echo ============================================================
where ffmpeg >nul 2>&1 || (echo ffmpeg is missing. In PowerShell run:  winget install Gyan.FFmpeg & pause & exit /b 1)
echo Installing / updating the downloader (yt-dlp)...
python -m pip install -q -U "yt-dlp[default]" || (echo Could not install yt-dlp. & pause & exit /b 1)
where deno >nul 2>&1 || echo Note: if downloads fail with a JavaScript error, run in PowerShell:  winget install DenoLand.Deno   then run this again.
set "MAX=%~1"
if "%MAX%"=="" set "MAX=60"
set "COOK=%~2"
if "%COOK%"=="" (
  python scripts\fetch_usafencing.py --max %MAX%
) else (
  python scripts\fetch_usafencing.py --max %MAX% --cookies-from "%COOK%"
)
echo.
pause
