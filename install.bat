@echo off
title TARZ AI Assistant - Setup
color 0B
echo.
echo ████████╗ █████╗ ██████╗ ███████╗
echo ╚══██╔══╝██╔══██╗██╔══██╗╚══███╔╝
echo    ██║   ███████║██████╔╝  ███╔╝
echo    ██║   ██╔══██║██╔══██╗ ███╔╝
echo    ██║   ██║  ██║██║  ██║███████╗
echo    ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝
echo.
echo  TARZ AI Assistant - Setup
echo  ================================
echo.

:: Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Python not found. Downloading...
    curl -L -o python_setup.exe https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe
    echo [!] Installing Python...
    python_setup.exe /quiet InstallAllUsers=1 PrependPath=1 Include_test=0
    del python_setup.exe
    echo [✓] Python installed
) else (
    echo [✓] Python found
)

:: Create virtual environment
echo.
echo [*] Setting up virtual environment...
python -m venv venv
echo [✓] Done

:: Install requirements
echo.
echo [*] Installing dependencies (this may take a few minutes)...
call venv\Scripts\pip install -r requirements.txt --quiet
echo [✓] Dependencies installed

:: Setup .env if not exists
if not exist .env (
    echo.
    echo [*] Setting up API keys...
    echo.
    set /p GROQ_KEY=Enter your Groq API key: 
    set /p GEMINI_KEY=Enter your Gemini API key: 
    set /p CEREBRAS_KEY=Enter your Cerebras API key: 
    set /p OPENWEATHER_KEY=Enter your OpenWeather API key: 
    
    echo groq_api=%GROQ_KEY% > .env
    echo GEMINI_API_KEY=%GEMINI_KEY% >> .env
    echo CEREBRAS_API_KEY=%CEREBRAS_KEY% >> .env
    echo OPENWEATHER_KEY=%OPENWEATHER_KEY% >> .env
    echo [✓] API keys saved
)

:: Add to startup
echo.
echo [*] Adding TARZ to Windows startup...
set STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
copy launch.bat "%STARTUP%\TARZ.bat" >nul
echo [✓] TARZ will start automatically on boot

echo.
echo ================================
echo  [✓] TARZ Setup Complete!
echo  Just say "Hey TARZ" anytime
echo  TARZ is starting now...
echo ================================
echo.
pause
start launch.bat