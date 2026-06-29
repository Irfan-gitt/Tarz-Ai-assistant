@echo off
setlocal

set "APP_DIR=%~dp0"
set "PYTHON=%APP_DIR%venv\Scripts\python.exe"
set "TRAY_SCRIPT=%APP_DIR%tarz_tray.py"

if not exist "%PYTHON%" (
    echo Python not found: %PYTHON%
    pause
    exit /b 1
)

if not exist "%TRAY_SCRIPT%" (
    echo tarz_tray.py not found: %TRAY_SCRIPT%
    pause
    exit /b 1
)

cd /d "%APP_DIR%"
"%PYTHON%" "%TRAY_SCRIPT%"
pause
endlocal
s