@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist ".pm-venv\Scripts\python.exe" (
  echo Missing .pm-venv. Run INSTALL_PM_DEPENDENCIES.bat first.
  pause
  exit /b 1
)
echo Installing tested ddgs 9.16.0 into this PM environment only.
echo No Ollama models or research data will be changed.
".pm-venv\Scripts\python.exe" -m pip install "ddgs==9.16.0"
if errorlevel 1 (
  echo Update failed. Keep this error text.
  pause
  exit /b 1
)
set PYTHONPATH=%CD%\src
".pm-venv\Scripts\python.exe" -c "from ollama_deep_researcher.pm_search_worker_v062 import provider_metadata; print(provider_metadata('duckduckgo'))"
if errorlevel 1 (
  echo Provider check failed. Do not start a long research run yet.
) else (
  echo Ready. Run START_RESEARCH_PM.bat and resume the unfinished v0.6 research.
)
pause
endlocal
