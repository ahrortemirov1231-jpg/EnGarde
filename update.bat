@echo off
REM Engarde Analytics - get the latest version from GitHub. Close the app (start.bat window) first.
REM Your data\ folder, videos and labels are not touched: they are not stored in GitHub.
setlocal
cd /d "%~dp0"
where git >nul 2>&1 || (echo Git is not installed. Install "Git for Windows" from https://git-scm.com/download/win & pause & exit /b 1)
if not exist .git (echo This folder did not come from GitHub. See docs\GITHUB.md, "Getting the code". & pause & exit /b 1)
git pull --ff-only || (
  echo.
  echo Could not update. Most often this means a code file was changed on this computer.
  echo Take a screenshot of the message above and send it to the model team.
  pause & exit /b 1
)
if exist .venv\Scripts\activate.bat (
  call .venv\Scripts\activate.bat
  python -m pip install -q -r requirements.txt
)
echo.
echo Up to date. Start the app with start.bat.
pause
