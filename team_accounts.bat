@echo off
REM Engarde Analytics - accounts from this PC, for when nobody can sign in as admin.
REM Usage: double-click to list accounts. From a command window:
REM   team_accounts.bat reset NAME        new password for NAME
REM   team_accounts.bat add NAME --role admin
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\activate.bat (echo Run setup.bat first. & pause & exit /b 1)
call .venv\Scripts\activate.bat
set "PYTHONUTF8=1"
set "PATH=%CD%\.venv\Scripts;%PATH%"
if "%~1"=="" (
  python -W ignore scripts\team_admin.py --data "%CD%\data" list
) else (
  python -W ignore scripts\team_admin.py --data "%CD%\data" %*
)
pause
