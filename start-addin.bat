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

echo [ADD-IN] Serving taskpane on http://localhost:3000  (Ctrl+C to stop)
echo          In Word: Insert ^> My Add-ins ^> SHARED FOLDER ^> Anonymizer
echo.
python -m http.server 3000
pause
