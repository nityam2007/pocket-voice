@echo off
setlocal
cd /d "%~dp0"
if not exist ".runtime-temp" mkdir ".runtime-temp"
if not exist ".pip-cache" mkdir ".pip-cache"
set "TMP=%CD%\.runtime-temp"
set "TEMP=%CD%\.runtime-temp"
set "PIP_CACHE_DIR=%CD%\.pip-cache"
if not exist ".venv\Scripts\python.exe" (
  echo Creating Python environment...
  py -3.14 -m venv .venv
  if errorlevel 1 exit /b 1
)
".venv\Scripts\python.exe" -m pip --version >nul 2>&1
if errorlevel 1 (
  echo Bootstrapping pip...
  ".venv\Scripts\python.exe" -m ensurepip --upgrade --default-pip
  if errorlevel 1 exit /b 1
)
echo Installing Pocket Voice dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
echo.
echo Setup complete. Run run.bat to start Pocket Voice.
