@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0.."
echo === VideoMonster Ecosystem EXE build ===
echo Requires: pip install pyinstaller
echo Output: dist\ecosystem\
echo.

python -c "import PyInstaller" 2>nul
if errorlevel 1 (
  echo PyInstaller not installed. Run: pip install pyinstaller
  exit /b 1
)

set APPS=reader_app translator_app tts_app subtitle_studio_app srt_editor_app quick_dub_app audio_reader_app
set COMMON_DATA=--add-data "templates;templates" --add-data "static;static" --add-data "data;data" --add-data "engines;engines" --add-data "api;api" --add-data "modules;modules"
set COMMON_HID=--hidden-import flask --hidden-import jinja2 --hidden-import werkzeug --hidden-import edge_tts --hidden-import deep_translator --hidden-import langdetect --hidden-import faster_whisper --hidden-import pydub --hidden-import webview --hidden-import engines.license_manager --hidden-import engines.stt_engine --hidden-import engines.tts --hidden-import engines.dub_engine

if not exist build\ecosystem mkdir build\ecosystem
if not exist dist\ecosystem mkdir dist\ecosystem

set FAILED=0
for %%A in (%APPS%) do (
  echo.
  echo Building VM_%%A ...
  pyinstaller --noconfirm --clean ^
    --name "VM_%%A" ^
    --distpath "dist\ecosystem" ^
    --workpath "build\ecosystem\%%A" ^
    --specpath "build\ecosystem" ^
    --onefile ^
    --windowed ^
    %COMMON_DATA% ^
    %COMMON_HID% ^
    apps\%%A.py
  if errorlevel 1 set FAILED=1
)

echo.
if !FAILED! equ 0 (
  echo Done. 7 EXE in dist\ecosystem\
  echo Install: install_ecosystem.bat
  exit /b 0
)
echo Build finished with errors.
exit /b 1
endlocal
