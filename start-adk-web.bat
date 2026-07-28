@echo off
rem ============================================================
rem  Anonymizer — ADK Web UI (port 8000)
rem  Inspect the agent: sessions, per-stage events, session state
rem  (actors, links, payload, metrics). Runs from the REPO ROOT
rem  so ADK discovers the `agent` package.
rem ============================================================
title Anonymizer ADK Web
cd /d "%~dp0"

where python >nul 2>nul || (
    echo [ERROR] Python not found in PATH. Install Python 3.11+ first.
    pause & exit /b 1
)

rem Same self-provisioning as start-agent.bat (shared venv in agent\.venv)
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

netstat -ano 2>nul | findstr /r ":8000 .*LISTENING" >nul && (
    echo [WARNING] Port 8000 is already in use - ADK web may already be
    echo           running, or another app holds the port.
    echo.
)

echo.
echo [ADK WEB] Starting on http://localhost:8000  - press Ctrl+C to stop
echo           Pick the agent named "agent", then send a .docx file path
echo           as the message (or attach the file).
echo.
"agent\.venv\Scripts\python" -m google.adk.cli web
pause
