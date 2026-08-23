@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\hf.exe" (
  echo Pocket Voice is not installed yet. Run setup.bat first.
  pause
  exit /b 1
)
echo.
echo Voice cloning requires accepting Kyutai's model conditions and a free
echo Hugging Face read token. Your token is stored by Hugging Face, not by this app.
echo.
echo 1. Accept the conditions on the model page that is opening.
echo 2. Create a READ token on the token page.
echo 3. Return here and paste the token when prompted.
echo.
start "" "https://huggingface.co/kyutai/pocket-tts"
start "" "https://huggingface.co/settings/tokens/new?tokenType=read"
".venv\Scripts\hf.exe" auth login
if errorlevel 1 (
  echo.
  echo Login was not completed. You can run this file again at any time.
  pause
  exit /b 1
)
echo.
echo Login complete. If Pocket Voice is already open, retry adding your voice.
pause
