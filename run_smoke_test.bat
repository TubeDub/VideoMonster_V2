@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === VideoMonster V2 Master Checks ===
python scripts\run_master_checks.py
set ERR=%ERRORLEVEL%
echo.
echo Log: output\master_check_results.txt
echo ZIP: output\VideoMonster_V2_ready.zip
pause
exit /b %ERR%
