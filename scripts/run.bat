@echo off
setlocal
set "PROJECT_ROOT=%~dp0.."
set "PYTHONPATH=%PROJECT_ROOT%\src"

if not "%PYTHON_EXE%"=="" (
  set "PYTHON=%PYTHON_EXE%"
) else if exist "%PROJECT_ROOT%\.venv\Scripts\python.exe" (
  set "PYTHON=%PROJECT_ROOT%\.venv\Scripts\python.exe"
) else if exist "D:\hundsun\software\QwenPaw\python.exe" (
  set "PYTHON=D:\hundsun\software\QwenPaw\python.exe"
) else (
  set "PYTHON=python"
)

if "%~1"=="" (
  "%PYTHON%" -B -m universal_entry_agent chat
) else (
  "%PYTHON%" -B -m universal_entry_agent %*
)
