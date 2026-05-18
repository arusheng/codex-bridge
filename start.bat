@echo off
title MiMo Bridge
cd /d "%~dp0"

if not exist config.json (
    echo [!] config.json not found, creating from config.example.json...
    copy config.example.json config.json
    echo.
    echo [!] Please edit config.json with your API settings, then restart.
    pause
    exit /b 1
)

echo Starting MiMo Bridge...
echo Management UI: http://127.0.0.1:3080/
echo.
py -u bridge.py
pause
