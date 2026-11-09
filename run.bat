@echo off
rem AutoShorts Studio - Quick Launcher Shortcut
cd /d "%~dp0"
call "%~dp0start.bat" %*
exit /b %ERRORLEVEL%
