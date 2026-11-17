@echo off
setlocal

set "APP_DIR=%~dp0"
set "PYTHON=%APP_DIR%venv\Scripts\python.exe"

if not exist "%PYTHON%" set "PYTHON=python"

cd /d "%APP_DIR%"
"%PYTHON%" "%APP_DIR%Main\v3_ui.py"
if errorlevel 1 pause
endlocal
