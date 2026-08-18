@echo off
setlocal

set "PROJECT_ROOT=%~dp0.."
set "PYTHONPATH=%PROJECT_ROOT%\src"

set "PYTHON="
if defined PYTHON_EXE set "PYTHON=%PYTHON_EXE%"
if not defined PYTHON if exist "%PROJECT_ROOT%\.venv\Scripts\python.exe" set "PYTHON=%PROJECT_ROOT%\.venv\Scripts\python.exe"
if not defined PYTHON if exist "D:\hundsun\software\QwenPaw\python.exe" set "PYTHON=D:\hundsun\software\QwenPaw\python.exe"
if not defined PYTHON set "PYTHON=python"

set "HOST=127.0.0.1"
set "PORT=8765"

:parse_args
if "%~1"=="" goto run_server
if /I "%~1"=="-HostName" set "HOST=%~2" & shift & shift & goto parse_args
if /I "%~1"=="--host" set "HOST=%~2" & shift & shift & goto parse_args
if /I "%~1"=="-Port" set "PORT=%~2" & shift & shift & goto parse_args
if /I "%~1"=="--port" set "PORT=%~2" & shift & shift & goto parse_args
if not "%~1"=="" set "HOST=%~1"
if not "%~2"=="" set "PORT=%~2"

:run_server
echo Starting Intent Router Agent Demo...
echo URL: http://%HOST%:%PORT%/demo
echo Root URL will redirect to /demo when frontend/dist exists.
echo.

"%PYTHON%" -B -m universal_entry_agent web --host "%HOST%" --port "%PORT%"
