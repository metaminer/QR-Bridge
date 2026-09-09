@echo off
setlocal

cd /d "%~dp0"
title QR Stream Transfer

where py >nul 2>nul
if errorlevel 1 goto use_python

py -3 -m ui.app
set "RUN_EXIT_CODE=%ERRORLEVEL%"
goto finished

:use_python
python -m ui.app
set "RUN_EXIT_CODE=%ERRORLEVEL%"

:finished
if not "%RUN_EXIT_CODE%"=="0" (
    echo.
    echo UI execution failed with exit code %RUN_EXIT_CODE%.
    pause
)

exit /b %RUN_EXIT_CODE%
