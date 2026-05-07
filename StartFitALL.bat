@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  call "%~dp0Install.bat"
  exit /b %errorlevel%
)

if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" "%~dp0desktop_app.py"
) else (
  start "" ".venv\Scripts\python.exe" "%~dp0desktop_app.py"
)
