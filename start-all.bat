@echo off
rem ============================================================
rem  Anonymizer — start ALL SERVERS in separate windows:
rem    1. Agent API   http://localhost:8080
rem    2. Add-in      http://localhost:3000
rem    3. ADK Web UI  http://localhost:8000
rem  Provisions the shared venv ONCE here first, so the three
rem  windows never race pip on a first run.
rem ============================================================
title Anonymizer Launcher
cd /d "%~dp0"

where python >nul 2>nul || (
    echo [ERROR] Python not found in PATH. Install Python 3.11+ first.
    pause & exit /b 1
)

if not exist "agent\.venv\.deps-ok" (
    if not exist "agent\.venv" (
        echo [SETUP] Creating virtual environment...
        python -m venv agent\.venv || (echo [ERROR] venv creation failed & pause & exit /b 1)
    )
    echo [SETUP] Installing dependencies - first time only, takes a few minutes...
    "agent\.venv\Scripts\python" -m pip install --quiet --upgrade pip
    "agent\.venv\Scripts\python" -m pip install -e "agent[dev]" || (
        echo [ERROR] dependency installation failed - rerun this script to retry.
        pause & exit /b 1)
    echo ok > "agent\.venv\.deps-ok"
)

if not exist "agent\.env" (
    copy "agent\.env.example" "agent\.env" >nul
    echo.
    echo [ACTION NEEDED] agent\.env was just created.
    echo Put your key in it:  GROQ_API_KEY=gsk_...
    notepad "agent\.env"
)

echo [1/3] Starting Agent API  (port 8080)...
start "Anonymizer Agent" cmd /k "%~dp0start-agent.bat"
timeout /t 2 >nul

echo [2/3] Starting Add-in server  (port 3000)...
start "Anonymizer Add-in" cmd /k "%~dp0start-addin.bat"
timeout /t 2 >nul

echo [3/3] Starting ADK Web UI  (port 8000)...
start "Anonymizer ADK Web" cmd /k "%~dp0start-adk-web.bat"

echo.
echo All three servers launched:
echo   Agent API : http://localhost:8080/health
echo   Add-in    : http://localhost:3000/taskpane.html
echo   ADK Web   : http://localhost:8000
echo.
echo Open Word and start the Anonymizer add-in.
timeout /t 8 >nul
