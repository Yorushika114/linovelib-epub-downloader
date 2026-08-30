@echo off
title linovelib downloader

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.10+ and check "Add to PATH".
    pause
    exit /b 1
)

python launcher.py
pause
