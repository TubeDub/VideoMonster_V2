@echo off
chcp 65001 >nul
echo ============================================
echo   VideoMonster V2 — Сборка Windows-приложения
echo ============================================
echo.

:: Проверяем Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ОШИБКА] Python не найден. Установите Python 3.10+ с python.org
    pause
    exit /b 1
)

echo [1/4] Установка зависимостей...
pip install -q -r requirements_desktop.txt
if errorlevel 1 (
    echo [ПРЕДУПР.] argostranslate может не установиться на Python 3.13 — это нормально.
    pip install -q flask edge-tts deep-translator langdetect pydub ffmpeg-python faster-whisper pywebview pyinstaller
)

echo [2/4] Очистка старой сборки...
if exist dist\VideoMonster rmdir /s /q dist\VideoMonster
if exist build rmdir /s /q build

echo [3/4] Сборка exe через PyInstaller...
pyinstaller desktop.spec --noconfirm
if errorlevel 1 (
    echo [ОШИБКА] Сборка провалилась. Проверьте вывод выше.
    pause
    exit /b 1
)

echo [4/4] Создание папки output...
if not exist dist\VideoMonster\output mkdir dist\VideoMonster\output

echo.
echo ============================================
echo  ГОТОВО! Папка: dist\VideoMonster\
echo  Запускай: dist\VideoMonster\VideoMonster.exe
echo ============================================
pause
