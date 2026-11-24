@echo off
REM Engarde Analytics - stop starting the team server when you sign in to Windows.
setlocal
set "FILE=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Engarde team server.bat"
if exist "%FILE%" del "%FILE%"
echo Done. The team server no longer starts by itself. Start it with serve_team.bat.
pause
