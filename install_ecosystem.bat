@echo off
setlocal
cd /d "%~dp0"
echo === VideoMonster Ecosystem install ===
echo Target: %LOCALAPPDATA%\VideoMonsterFreeApps
echo.

python -c "from engines.ecosystem_installer import install_ecosystem; r=install_ecosystem(build_if_missing=False); print('Install dir:', r.get('install_dir')); print('Copied:', len(r.get('copied') or [])); print('Installed:', len(r.get('installed') or [])); missing=r.get('missing') or []; print('Missing:', len(missing)); [print('  -', m) for m in missing]; exit(0 if not missing else 1)"
if errorlevel 1 (
  echo.
  echo EXE not found. Build first: scripts\build_ecosystem.bat
  exit /b 1
)
echo Done.
endlocal
