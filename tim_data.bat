@echo off
REM Engarde Analytics - bring in Tim's tagged touches (Model comparison page) and run our pipeline on them.
REM Steps: check the file, download clips, load them into data_tim, analyse every touch. Safe to re-run.
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\activate.bat (
  echo Setup has not been run on this PC yet. Run setup.bat first.
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
set "PYTHONUTF8=1"
set "PATH=%CD%\.venv\Scripts;%PATH%"
echo === 1/4 Download data.json and the touch clips ===
python scripts\import_tim_data.py download || goto :fail
echo.
echo === 2/4 Check the file and re-score Tim's models ===
python scripts\import_tim_data.py check || goto :fail
echo.
echo === 3/4 Load the touches into data_tim ===
python scripts\import_tim_data.py load || goto :fail
echo.
echo === 4/4 Analyse every touch (pose, tracking, features, rule baseline) ===
python scripts\import_tim_data.py analyze || goto :fail
echo.
echo Done. Next: double-click start_tim.bat, open Insights ^> Models, train XGBoost and TCN with NO frozen
echo test bout, then double-click compare_tim.bat.
pause
exit /b 0
:fail
echo.
echo A step failed. The error is above. Fix it and run tim_data.bat again; finished steps are skipped.
pause
exit /b 1
