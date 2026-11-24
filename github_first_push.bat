@echo off
REM Engarde Analytics - ONE TIME, by one person: put this folder on GitHub as a PRIVATE repository.
REM Before running: 1) install Git for Windows (https://git-scm.com/download/win)
REM                 2) on github.com create a NEW repository, set it to PRIVATE, and do NOT add a README.
setlocal
cd /d "%~dp0"
where git >nul 2>&1 || (
  echo Git is not installed. Install "Git for Windows" from https://git-scm.com/download/win
  echo then close this window and run github_first_push.bat again.
  pause & exit /b 1
)
if exist .git (
  echo This folder is already connected to Git. Use update.bat to get changes, or GitHub Desktop to send them.
  pause & exit /b 0
)
echo.
echo Create an EMPTY, PRIVATE repository on github.com first. Do not tick "Add a README".
set "REPO="
set /p "REPO=Paste its address (looks like https://github.com/NAME/engarde.git) and press Enter: "
if "%REPO%"=="" (echo No address given. Nothing was changed. & pause & exit /b 1)
git config user.name >nul 2>&1 || (
  set /p "GNAME=Your name for the change history: "
  set /p "GMAIL=Your GitHub email (or the noreply address under GitHub Settings, Emails): "
)
git init -b main || goto fail
if defined GNAME git config user.name "%GNAME%"
if defined GMAIL git config user.email "%GMAIL%"
git add -A || goto fail
for %%F in (*.command) do git update-index --chmod=+x "%%F"
git update-index --chmod=+x scripts/run_usa.sh >nul 2>&1
echo.
echo Checking that no video, database or sponsor data is about to be uploaded...
git ls-files --cached | findstr /i /r "\.mp4$ \.mov$ \.webm$ \.mkv$ \.sqlite ^data/ ^data_tim/ ^tim_data/ ^demo_data ^incoming/ cookies\.txt \.pt$" >nul && (
  echo.
  echo STOP: the list of files to upload contains a video, a database, model weights or a data folder:
  git ls-files --cached | findstr /i /r "\.mp4$ \.mov$ \.webm$ \.mkv$ \.sqlite ^data/ ^data_tim/ ^tim_data/ ^demo_data ^incoming/ cookies\.txt \.pt$"
  echo Nothing was uploaded. Delete the hidden .git folder in this folder, fix .gitignore, and run this again.
  pause & exit /b 1
)
echo OK: only code and documents.
git commit -q -m "Engarde MVP v0.5.6" || goto fail
git remote add origin "%REPO%" || goto fail
echo.
echo Uploading. A GitHub sign-in window may open the first time.
git push -u origin main || goto fail
echo.
echo Done. The code is on GitHub. Next: on github.com open the repository, Settings, Collaborators, and add the team.
pause
exit /b 0
:fail
echo.
echo Something went wrong - the message is above. Take a screenshot and nothing on GitHub was changed
echo unless the upload line had already started.
pause
exit /b 1
