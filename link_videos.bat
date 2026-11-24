@echo off
REM Link an imported analysis (data\ANALYSIS_MANIFEST.json) to the original videos on this PC.
REM Copy the analysis files (e.g. the contents of mvp-p1) into data\ first.
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\activate.bat (echo Run setup.bat first. & pause & exit /b 1)
call .venv\Scripts\activate.bat
set "PYTHONUTF8=1"
set "PATH=%CD%\.venv\Scripts;%PATH%"
python scripts\link_videos.py
pause
