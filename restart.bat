@echo off
rem Restart Bankstanding after an update: stops the copy running in the background and starts the new code.
cd /d "%~dp0"
where pyw >nul 2>nul
if %errorlevel%==0 (
  start "" pyw -3 run.py --restart
  exit /b
)
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw run.py --restart
  exit /b
)
echo Python 3 was not found.
echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH", then run this again.
pause
