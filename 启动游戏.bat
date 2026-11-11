@echo off
rem ---------------------------------------------------------------------------
rem  Text Interactive Game - Windows player launcher (double-click entry).
rem  Starts scripts\play.ps1 -Launcher: a small launcher window + the browser.
rem  This .bat is intentionally ASCII-only (no Chinese text, no UTF-8 BOM):
rem  cmd.exe code pages vary between machines and must never break startup.
rem  Extra arguments are forwarded, e.g.  launcher.bat -Port 8001
rem ---------------------------------------------------------------------------
setlocal EnableExtensions
set "EXITCODE=0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\play.ps1" -Launcher %*
set "EXITCODE=%ERRORLEVEL%"
if not "%EXITCODE%"=="0" (
    echo.
    echo The game launcher exited with code %EXITCODE%. Read the message above.
    pause
)
exit /b %EXITCODE%
