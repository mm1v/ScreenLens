@echo off
title ScreenLens (Dev Terminal)
chcp 65001 >nul
cd /d "%~dp0"

echo ===================================================
echo   ScreenLens Dev Terminal
echo   Hotkey: Ctrl + Alt + Space
echo ===================================================
echo.

".venv\Scripts\python.exe" app.py
pause
