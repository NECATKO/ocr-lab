@echo off
setlocal
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0kurulum.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"
if "%OCR_NO_PAUSE%"=="" (
    echo.
    pause
)
exit /b %EXIT_CODE%
