@echo off
REM Engarde Analytics - give the team server its https address through Tailscale. Run once.
REM It stays on after a restart. Turn it off with team_link_off.bat.
setlocal
set "TS=tailscale"
where tailscale >nul 2>&1 || set "TS=%ProgramFiles%\Tailscale\tailscale.exe"
"%TS%" version >nul 2>&1 || (
  echo Tailscale is not installed, or it is not signed in.
  echo Install it from https://tailscale.com/download and sign in, then run this again.
  pause
  exit /b 1
)
"%TS%" serve --bg 8000
echo.
echo ---------------------------------------------------------------
"%TS%" serve status
echo ---------------------------------------------------------------
echo The https address above is the team's link. It only works for people you shared this PC with
echo in Tailscale, and only while serve_team.bat is running.
echo If no address is shown, read "The link does not appear" in docs\TEAM_SERVER.md.
pause
