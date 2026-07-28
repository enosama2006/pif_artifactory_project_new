@echo off
rem ============================================================
rem  Anonymizer — start EVERYTHING (agent + add-in), two windows
rem ============================================================
start "Anonymizer Agent" cmd /k "%~dp0start-agent.bat"
start "Anonymizer Add-in" cmd /k "%~dp0start-addin.bat"
echo Both windows launched. Open Word and start the Anonymizer add-in.
timeout /t 5 >nul
