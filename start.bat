@echo off
title Bankstanding
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 run.py %*
  goto end
)
where python >nul 2>nul
if %errorlevel%==0 (
  python run.py %*
  goto end
)
echo Python 3 was not found.
echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
:end
pause
