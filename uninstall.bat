@echo off
rem Undo install.bat: stop starting with Windows and remove the icons. Your data is kept.
cd /d "%~dp0"
where pyw >nul 2>nul
if %errorlevel%==0 (
  start "" pyw -3 run.py --uninstall
  exit /b
)
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw run.py --uninstall
  exit /b
)
echo Python 3 was not found.
echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH", then run this again.
pause
