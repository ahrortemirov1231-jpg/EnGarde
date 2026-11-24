@echo off
REM Engarde Analytics - remove the team's https address. The app keeps running on this PC only.
setlocal
set "TS=tailscale"
where tailscale >nul 2>&1 || set "TS=%ProgramFiles%\Tailscale\tailscale.exe"
"%TS%" serve reset
echo The team link is off. Run team_link_on.bat to turn it on again.
pause
