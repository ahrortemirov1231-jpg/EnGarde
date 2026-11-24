@echo off
REM Engarde Analytics - copy Tim's 5 tagged bouts (164 touches, from Andre's data.json) into the team
REM workspace, with his tags as labels and the finished analysis. Nothing is analysed again.
REM Safe to run twice. The benchmark folder (data_tim) is only read.
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\activate.bat (echo Run setup.bat first. & pause & exit /b 1)
netstat -ano | findstr /r /c:":8000 .*LISTENING" >nul && (
  echo The team server is running. Close its black window first, then run this again.
  pause
  exit /b 1
)
echo "%CD%" | find /I "OneDrive" >nul && (
  echo.
  echo WARNING: this folder is inside OneDrive. Everything copied into its data folder, including the
  echo videos of Tim's fencers, is uploaded to Microsoft's cloud by OneDrive. Move the app to a folder
  echo outside OneDrive first - see "Move the team server out of OneDrive" in docs\TEAM_SERVER.md.
  echo.
  choice /M "Continue here anyway"
  if errorlevel 2 exit /b 1
)
set "SRC=%CD%\data_tim"
set "JSON=%CD%\tim_data\data.json"
if not exist "%SRC%\tim_import.json" set "SRC=C:\EnGarde\data_tim"
if not exist "%JSON%" set "JSON=C:\EnGarde\tim_data\data.json"
if not exist "%SRC%\tim_import.json" set "SRC=C:\engarde-mvp-v0.3.0\data_tim"
if not exist "%JSON%" set "JSON=C:\engarde-mvp-v0.3.0\tim_data\data.json"
if not exist "%SRC%\tim_import.json" (
  echo Cannot find Tim's analysed bouts. Expected a data_tim folder here or in C:\EnGarde
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
set "PYTHONUTF8=1"
set "PATH=%CD%\.venv\Scripts;%PATH%"
echo Copying from %SRC%
echo        into %CD%\data
echo.
python -W ignore scripts\team_add_tim_bouts.py --src "%SRC%" --json "%JSON%" --dst "%CD%\data" || goto fail
echo.
echo Done. Start serve_team.bat: the 5 bouts are on the Videos page, titled "Tim rec-...".
pause
exit /b 0
:fail
echo.
echo It did not finish - the message is above. Bouts that were already copied are kept; run this again
echo after fixing the problem and it continues with the rest.
pause
exit /b 1
