@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if exist ".pm-venv\Scripts\python.exe" goto install
py -3.11 -c "import tkinter" >nul 2>&1
if not errorlevel 1 (
  py -3.11 -m venv .pm-venv
) else (
  python -c "import sys,tkinter; assert sys.version_info >= (3,11)" >nul 2>&1
  if errorlevel 1 (
    echo Install Python 3.11 or newer with Tkinter and enable Add Python to PATH.
    pause
    exit /b 1
  )
  python -m venv .pm-venv
)
if not exist ".pm-venv\Scripts\python.exe" (
  echo Could not create the isolated Python environment.
  pause
  exit /b 1
)
:install
echo Installing project dependencies into .pm-venv from PyPI.
echo This does not install another Ollama model.
".pm-venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 (
  echo Installation failed. Keep this window and share the error text.
) else (
  echo Ready. Double-click START_RESEARCH_PM.bat.
)
pause
endlocal
