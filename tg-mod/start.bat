@echo off
chcp 65001 >nul
title TG-mod
cd /d "%~dp0"

if not exist .venv (
    echo Первый запуск: создаю окружение Python...
    py -3 -m venv .venv 2>nul || python -m venv .venv
    if errorlevel 1 (
        echo Не найден Python. Установи его с https://www.python.org/downloads/
        echo и при установке поставь галочку "Add python.exe to PATH".
        pause
        exit /b 1
    )
)
call .venv\Scripts\activate.bat
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt
rem cryptg ускоряет загрузку медиа, но не обязателен
python -m pip install -q cryptg 2>nul

:loop
python main.py
if %errorlevel%==0 goto end
echo.
echo Мод остановился с ошибкой, перезапуск через 10 секунд... (закрой окно, чтобы выйти)
timeout /t 10 >nul
goto loop

:end
pause
