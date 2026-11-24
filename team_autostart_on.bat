@echo off
REM Engarde Analytics - start the team server by itself each time you sign in to Windows.
REM Run serve_team.bat by hand once first, so the admin account exists.
setlocal
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "FILE=%STARTUP%\Engarde team server.bat"
> "%FILE%" echo @echo off
>> "%FILE%" echo start "Engarde team server" /min cmd /c ""%~dp0serve_team.bat""
if exist "%FILE%" (
  echo Done. From now on the team server starts, minimised, when you sign in to Windows.
  echo To undo this, run team_autostart_off.bat.
) else (
  echo Could not write to the Startup folder: %STARTUP%
)
pause
