@echo off
rem Opens RuneLite with the Bankstanding plugin loaded (needs Java 17 or newer).
cd /d "%~dp0"
call gradlew.bat run
pause
