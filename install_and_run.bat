@echo off
chcp 65001 >nul
title TubeDub — установка и запуск
cd /d "%~dp0"

echo.
echo  ========================================
echo   TubeDub — установка
echo   Powered by VideoMonster Engine
echo  ========================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ОШИБКА] Python не найден.
    echo Скачайте Python 3.10+ с https://python.org
    echo При установке отметьте "Add Python to PATH".
    pause
    exit /b 1
)

echo [1/3] Установка зависимостей...
pip install -r requirements_desktop.txt
if errorlevel 1 (
    echo Пробуем базовый набор...
    pip install flask edge-tts deep-translator langdetect pydub ffmpeg-python faster-whisper pywebview
)

echo.
echo [2/3] Проверка FFmpeg...
where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo [ВНИМАНИЕ] FFmpeg не найден в PATH.
    echo Без FFmpeg видео не обработается.
    echo Скачайте: https://ffmpeg.org/download.html
    echo.
) else (
    echo FFmpeg найден — OK
)

echo.
echo [3/3] Запуск TubeDub...
echo Откроется окно программы. Выберите видео и нажмите "Дубляж".
echo.
python desktop.py
pause
