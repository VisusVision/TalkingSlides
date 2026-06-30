@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\visus-launcher.ps1" %*
set "VISUS_EXIT=%ERRORLEVEL%"
if not "%VISUS_EXIT%"=="0" (
    echo.
    echo VISUS VidLab launcher exited with code %VISUS_EXIT%.
    pause
)
exit /b %VISUS_EXIT%
