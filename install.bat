@echo off
rem One time setup: Bankstanding starts quietly with Windows and gets desktop and Start menu icons.
cd /d "%~dp0"
where pyw >nul 2>nul
if %errorlevel%==0 (
  start "" pyw -3 run.py --install
  exit /b
)
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw run.py --install
  exit /b
)
echo Python 3 was not found.
echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH", then run this again.
pause
