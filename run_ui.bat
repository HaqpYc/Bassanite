@echo off
rem Start the Bassanite web UI: double-click this file, or run it from a terminal.
rem It opens http://localhost:8080 in the default browser. To stop the server, close
rem this window or press Ctrl+C. Extra arguments are passed on, e.g.
rem     run_ui.bat --no-browser
rem
rem Python used: %BASSANITE_PYTHON% if set, else the project venv in
rem %USERPROFILE%\.venvs\bassanite, else the first python on PATH.
setlocal
set "PY=%BASSANITE_PYTHON%"
if not defined PY if exist "%USERPROFILE%\.venvs\bassanite\Scripts\python.exe" set "PY=%USERPROFILE%\.venvs\bassanite\Scripts\python.exe"
if not defined PY set "PY=python"
echo Starting Bassanite with %PY%
"%PY%" "%~dp0ui\app.py" %*
rem Keep the window open on an error, so the message can be read.
if errorlevel 1 pause
