@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo VideoMonster License Server
echo URL: http://127.0.0.1:8787
echo.
echo В клиентах укажите data\license_server.json:
echo   { "enabled": true, "url": "http://YOUR-IP:8787" }
echo.
python license_server.py --host 0.0.0.0 --port 8787
pause
