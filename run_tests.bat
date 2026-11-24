@echo off
REM Engarde Analytics - run the 69 automated tests. Takes about 5 minutes. Close the app (start.bat) first.
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\activate.bat (echo Run setup.bat first. & pause & exit /b 1)
call .venv\Scripts\activate.bat
set "PYTHONUTF8=1"
set "PATH=%CD%\.venv\Scripts;%PATH%"
cd backend
echo Running tests... the last line should say "73 passed".
python -m pytest -q -p no:cacheprovider
echo.
echo Done. Take a screenshot of the last lines if anything failed.
pause
