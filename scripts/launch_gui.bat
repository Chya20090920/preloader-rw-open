@echo off
rem ---------------------------------------------------------------------------
rem  Program launcher (logic lives here; ASCII-only so cmd parses it byte-exact).
rem  Picks a Python that has PySide6, then starts the GUI detached:
rem    - pythonw.exe  -> the GUI itself owns NO console window
rem    - the GUI forces CREATE_NO_WINDOW on the shell child it spawns
rem  so exactly one window appears: the GUI (this console closes right away).
rem  Config = PANE_* environment variables; defaults live in
rem  preloader_rw\relay\assembly.py.
rem  Optional user config file: config.bat AT REPO ROOT
rem  (see scripts\config.example.bat).
rem  NOTE: this file must stay pure ASCII. A non-ASCII byte or a mid-file
rem  "chcp" makes cmd re-read the file at a wrong byte offset and execute
rem  comment fragments as commands.
rem ---------------------------------------------------------------------------
setlocal EnableExtensions
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
rem This script lives in scripts\; the repo root is one level up.
cd /d "%~dp0\.."

set "PYEXE="
for %%P in (python.exe python3.exe) do (
    if not defined PYEXE (
        where %%P >nul 2>nul && set "PYEXE=%%P"
    )
)
if not defined PYEXE goto :nopython

"%PYEXE%" "scripts\_find_python.py" > "%TEMP%\_fwconsole_py.txt" 2>nul
if errorlevel 1 goto :nopython
set /p PYEXE=<"%TEMP%\_fwconsole_py.txt"
del "%TEMP%\_fwconsole_py.txt" >nul 2>nul
if not defined PYEXE goto :nopython

rem Prefer pythonw.exe next to the chosen interpreter (GUI subsystem).
set "PYW=%PYEXE:python.exe=pythonw.exe%"
if not exist "%PYW%" set "PYW=%PYEXE%"

rem Optional user config (DA1/DA2 images; see config.example.bat).
rem Auth = server-signed response, requested at runtime by the device;
rem nothing auth-related is preset here.
if exist "config.bat" call "config.bat"

echo [launcher] python: %PYW%
echo [launcher] config:  PANE_* environment variables
echo [launcher] log:     %CD%\client.log
start "" /D "%CD%" "%PYW%" -m preloader_rw.client %*
endlocal
exit /b 0

:nopython
echo.
echo [launcher] No Python with PySide6 found.
echo [launcher] Install it with:  python -m pip install PySide6
echo.
if defined PYEXE echo [launcher] tried: %PYEXE%
pause
endlocal
exit /b 2
