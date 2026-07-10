@echo off
cd /d "%~dp0.."
python scripts\test_naturalizer_unit.py > output\_run_unit.log 2>&1
python scripts\test_translation_quality.py > output\_run_quality.log 2>&1
echo Exit quality: %ERRORLEVEL%
