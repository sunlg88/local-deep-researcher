@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
set LANGSMITH_TRACING=false
set LANGCHAIN_TRACING_V2=false
if not exist ".pm-venv\Scripts\python.exe" (
  echo Run INSTALL_PM_DEPENDENCIES.bat first.
  pause
  exit /b 1
)
".pm-venv\Scripts\python.exe" -m ollama_deep_researcher.pm_gui
if errorlevel 1 pause
endlocal
