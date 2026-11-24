@echo off
REM Compare our latest trained model (held-out bouts) and our rule baseline with Tim's models, same touches.
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
echo === Our rule baseline vs Tim's models ===
python scripts\import_tim_data.py compare --model rule
echo.
echo === Our latest trained model vs Tim's models ===
python scripts\import_tim_data.py compare --model latest
echo.
echo Reports are saved in data_tim\reports.
pause
