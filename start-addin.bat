@echo off
rem ============================================================
rem  Anonymizer — serve the Word ADD-IN taskpane (port 3000)
rem  Sideload addin\manifest.xml once — see RUNBOOK.md section 3
rem ============================================================
title Anonymizer Add-in
cd /d "%~dp0addin"

where python >nul 2>nul || (
    echo [ERROR] Python not found in PATH. Install Python 3.11+ first.
    pause & exit /b 1
)

if not exist "taskpane.html" (
    echo [ERROR] taskpane.html not found - run this script from the repo copy.
    pause & exit /b 1
)

netstat -ano 2>nul | findstr /r ":3000 .*LISTENING" >nul && (
    echo [WARNING] Port 3000 is already in use - the add-in server may already
    echo           be running. If startup fails, close the other process
    echo           or change the port here AND in addin\manifest.xml.
    echo.
)

echo [ADD-IN] Serving taskpane on http://localhost:3000  - press Ctrl+C to stop
echo          In Word: Insert / My Add-ins / SHARED FOLDER / Anonymizer
echo.
python serve.py 3000
pause
