@echo off
rem Downloads USA Fencing sabre bouts using the YouTube sign-in from Edge "Profile 1" (spare account).
rem Close every Edge window before running this.
cd /d "%~dp0"
call download_usafencing.bat 60 "edge:Profile 1"
