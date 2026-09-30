@echo off
rem Opens RuneLite with the GE Companion plugin loaded (needs Java 11 or newer).
cd /d "%~dp0"
call gradlew.bat run
pause
