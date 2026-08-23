@echo off
setlocal
cd /d "%~dp0"
if not exist ".runtime-temp" mkdir ".runtime-temp"
set "TMP=%CD%\.runtime-temp"
set "TEMP=%CD%\.runtime-temp"
set "HF_HUB_CACHE=%CD%\models\huggingface"
set "HF_XET_CACHE=%CD%\models\xet"
if not exist ".venv\Scripts\python.exe" (
  echo Pocket Voice is not installed yet. Running setup...
  call setup.bat
  if errorlevel 1 exit /b 1
)
set OMP_NUM_THREADS=2
set MKL_NUM_THREADS=2
set TOKENIZERS_PARALLELISM=false
echo Starting Pocket Voice at http://127.0.0.1:8000
start "" http://127.0.0.1:8000
".venv\Scripts\python.exe" -m uvicorn app:app --host 127.0.0.1 --port 8000
