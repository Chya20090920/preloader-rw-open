@echo off
rem Program launcher (double-click me). Logic lives in scripts\launch_gui.bat.
setlocal
cd /d "%~dp0\.."
call "%~dp0launch_gui.bat" %*
set "RC=%ERRORLEVEL%"
endlocal
exit /b %RC%
