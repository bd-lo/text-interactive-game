@echo off
rem ---------------------------------------------------------------------------
rem  Text Interactive Game - fallback "exit game" entry (double-click).
rem  Finds the process listening on 127.0.0.1:<port> and stops its process tree:
rem  exact netstat column match + taskkill /T /F. The detection/kill logic and
rem  the Chinese console messages live in scripts\play.ps1 -Stop, because that
rem  file is UTF-8 with BOM so Chinese displays correctly on any code page.
rem  This .bat itself stays ASCII-only to avoid cmd.exe code page problems.
rem  Optional first argument = port, e.g.  exit-game.bat 8001
rem ---------------------------------------------------------------------------
setlocal EnableExtensions
set "STOP_ARGS=-Stop"
if "%~1"=="" goto run
set "FIRST=%~1"
if "%FIRST:~0,1%"=="-" goto passthrough
set "STOP_ARGS=-Stop -Port %~1"
goto run
:passthrough
set "STOP_ARGS=-Stop %*"
:run
set "EXITCODE=0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\play.ps1" %STOP_ARGS%
set "EXITCODE=%ERRORLEVEL%"
echo.
pause
exit /b %EXITCODE%
