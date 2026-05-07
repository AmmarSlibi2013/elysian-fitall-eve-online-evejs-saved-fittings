@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title Elysian FitALL Installer

echo.
echo Elysian FitALL
echo Adds a saved fitting for every single ship to all characters.
echo.

call :CheckDisk || exit /b 1
call :FindPython
if not defined PYTHON call :InstallPython
call :FindPython
if not defined PYTHON (
  echo Python could not be installed automatically.
  echo Install Python 3.10 or newer, then run Install.bat again.
  pause
  exit /b 1
)

"%PYTHON%" %PYTHON_ARGS% -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 (
  echo Python 3.10 or newer is required.
  pause
  exit /b 1
)

if not exist ".venv" "%PYTHON%" %PYTHON_ARGS% -m venv ".venv"
if errorlevel 1 (
  echo Could not create the local Python environment.
  pause
  exit /b 1
)

set "VENV_PY=%CD%\.venv\Scripts\python.exe"
"%VENV_PY%" -m pip install --upgrade pip
if errorlevel 1 exit /b 1
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 exit /b 1

call :ChooseEveJS
echo.
echo Launching Elysian FitALL...
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" "%CD%\desktop_app.py"
) else (
  start "" "%VENV_PY%" "%CD%\desktop_app.py"
)
exit /b 0

:FindPython
set "PYTHON="
set "PYTHON_ARGS="
where py >nul 2>&1
if not errorlevel 1 (
  py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
  if not errorlevel 1 (
    set "PYTHON=py"
    set "PYTHON_ARGS=-3"
  )
)
if defined PYTHON exit /b 0
where python >nul 2>&1
if not errorlevel 1 (
  python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
  if not errorlevel 1 (
    set "PYTHON=python"
    set "PYTHON_ARGS="
  )
)
if defined PYTHON exit /b 0
for %%P in ("%LocalAppData%\Programs\Python\Python312\python.exe" "%ProgramFiles%\Python312\python.exe" "%ProgramFiles(x86)%\Python312\python.exe") do (
  if exist "%%~P" (
    "%%~P" -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
    if not errorlevel 1 (
      set "PYTHON=%%~P"
      set "PYTHON_ARGS="
      exit /b 0
    )
  )
)
exit /b 0

:InstallPython
echo Python 3.10+ was not found. Installing Python now...
where winget >nul 2>&1
if not errorlevel 1 (
  winget install --id Python.Python.3.12 -e --source winget --accept-package-agreements --accept-source-agreements
  exit /b 0
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $url='https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe'; $out=Join-Path $env:TEMP 'python-3.12.10-amd64.exe'; Invoke-WebRequest -Uri $url -OutFile $out; Start-Process -FilePath $out -ArgumentList '/quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_pip=1' -Wait"
exit /b 0

:CheckDisk
for /f %%A in ('powershell -NoProfile -Command "[int]((Get-PSDrive -Name ((Get-Location).Path.Substring(0,1))).Free / 1GB)"') do set "FREE_GB=%%A"
if not defined FREE_GB set "FREE_GB=0"
if %FREE_GB% LSS 1 (
  echo FitALL needs at least 1GB free disk space for setup.
  pause
  exit /b 1
)
exit /b 0

:ChooseEveJS
if exist "config\evejs.path" (
  set /p EVEJS_PATH=<"config\evejs.path"
) else (
  set "EVEJS_PATH="
)

:AskPath
if not defined EVEJS_PATH (
  echo.
  echo Drag your EVE JS folder into this window, or paste its path, then press Enter.
  set /p "EVEJS_PATH=EVE JS folder: "
)
set "EVEJS_PATH=%EVEJS_PATH:"=%"
"%VENV_PY%" -c "import pathlib, sys, fitall; fitall.configure_evejs_root(pathlib.Path(sys.argv[1])); fitall.ensure_evejs_runtime_ready(); print('EVE JS ready:', fitall.REPO_ROOT)" "%EVEJS_PATH%"
if errorlevel 1 (
  echo.
  echo That folder was not an EVE JS checkout with the expected database files.
  set "EVEJS_PATH="
  goto AskPath
)
exit /b 0
