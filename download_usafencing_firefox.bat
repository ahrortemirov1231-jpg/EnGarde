@echo off
rem Downloads USA Fencing sabre bouts using the YouTube sign-in from Firefox (spare account).
rem Close every Firefox window before running this.
cd /d "%~dp0"
call download_usafencing.bat 60 firefox
