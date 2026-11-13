@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
    echo Сначала один раз запусти start.bat, чтобы установились зависимости.
    pause
    exit /b 1
)
.venv\Scripts\python -m unittest -v
pause
