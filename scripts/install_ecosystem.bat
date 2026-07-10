@echo off
setlocal
cd /d "%~dp0.."
echo === VideoMonster Ecosystem install ===
echo Target: %%LOCALAPPDATA%%\VideoMonsterFreeApps (separate from VideoMonster uninstall)
echo.

python -c "from engines.ecosystem_installer import install_ecosystem; import json; r=install_ecosystem(build_if_missing=False); print(json.dumps(r, ensure_ascii=False, indent=2)); import sys; sys.exit(0 if r.get('installed') else 2)"

if errorlevel 2 (
  echo.
  echo No EXEs in dist\ecosystem. Build first:
  echo   scripts\build_ecosystem.bat
)

endlocal
pause
