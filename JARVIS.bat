@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Run JARVIS_install.bat first to install JARVIS.
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" app.py
