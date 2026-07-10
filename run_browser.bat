@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo TubeDub — запуск в браузере (без окна WebView)
set VM_BROWSER_ONLY=1
python desktop.py
pause
