@echo off
setlocal
cd /d "%~dp0"

call start_gui.bat
exit /b %ERRORLEVEL%
