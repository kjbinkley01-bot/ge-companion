@echo off
rem Opens RuneLite with the Bankstanding plugin loaded (needs Java 11 or newer).
cd /d "%~dp0"
call gradlew.bat run
pause
