#!/usr/bin/env bash
# Запуск мода на Linux / macOS с автоперезапуском при ошибке.
cd "$(dirname "$0")" || exit 1
if [ ! -d .venv ]; then
    echo "Первый запуск: создаю окружение Python..."
    python3 -m venv .venv || { echo "Нужен Python 3.9+"; exit 1; }
fi
. .venv/bin/activate
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt
python -m pip install -q cryptg 2>/dev/null || true   # необязательное ускорение

while true; do
    python main.py && break
    echo "Мод остановился с ошибкой, перезапуск через 10 секунд... (Ctrl+C для выхода)"
    sleep 10
done
