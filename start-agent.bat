@echo off
rem ============================================================
rem  Anonymizer — start the AGENT (HTTP API on port 8080)
rem  First run: creates the venv, installs deps, prepares .env
rem ============================================================
title Anonymizer Agent
cd /d "%~dp0agent"

where python >nul 2>nul || (
    echo [ERROR] Python not found in PATH. Install Python 3.11+ first.
    pause & exit /b 1
)

rem .deps-ok marker: written only after a SUCCESSFUL install, so a
rem half-finished first run retries instead of starting broken.
if not exist ".venv\.deps-ok" (
    if not exist ".venv" (
        echo [SETUP] Creating virtual environment...
        python -m venv .venv || (echo [ERROR] venv creation failed & pause & exit /b 1)
    )
    echo [SETUP] Installing dependencies - first time only, takes a few minutes...
    ".venv\Scripts\python" -m pip install --quiet --upgrade pip
    ".venv\Scripts\python" -m pip install -e ".[dev]" || (
        echo [ERROR] dependency installation failed - rerun this script to retry.
        pause & exit /b 1)
    echo ok > ".venv\.deps-ok"
)

if not exist ".env" (
    copy .env.example .env >nul
    echo.
    echo [ACTION NEEDED] agent\.env was just created.
    echo Put your key in it:  GROQ_API_KEY=gsk_...
    notepad .env
)

netstat -ano 2>nul | findstr /r ":8080 .*LISTENING" >nul && (
    echo [WARNING] Port 8080 is already in use - the agent may already be
    echo           running, or another app holds the port. If startup fails,
    echo           close the other process or edit this script to another port.
    echo.
)

echo.
echo [AGENT] Starting on http://localhost:8080  - press Ctrl+C to stop
echo         Health check: http://localhost:8080/health
echo.
".venv\Scripts\python" -m uvicorn app.api.routes:app --port 8080
pause
