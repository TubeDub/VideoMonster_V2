@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
title VideoMonster — инструменты владельца

if not defined VM_OWNER_TOKEN set VM_OWNER_TOKEN=vm-owner-local

echo ========================================
echo  VideoMonster V2 — инструменты владельца
echo ========================================
echo.
echo Токен: %VM_OWNER_TOKEN%
echo.

:menu
echo Выберите действие:
echo  1. Сгенерировать TEST-7 ключ
echo  2. Сгенерировать TEST-30 ключ
echo  3. Сгенерировать PREMIUM-MONTH ключ
echo  4. Сгенерировать LIFETIME ключ
echo  5. Создать тестовую сборку TEST-7 (ZIP)
echo  6. Создать тестовую сборку TEST-30 (ZIP)
echo  7. Список тестовых сборок
echo  8. Отключить ключ
echo  9. Продлить ключ (+7 дней)
echo  10. Повторить инициализацию владельца (VM_DEV_MODE)
echo  0. Выход
echo.
set /p choice="Номер: "

if "%choice%"=="1" goto gen_t7
if "%choice%"=="2" goto gen_t30
if "%choice%"=="3" goto gen_pm
if "%choice%"=="4" goto gen_life
if "%choice%"=="5" goto build_t7
if "%choice%"=="6" goto build_t30
if "%choice%"=="7" goto list_builds
if "%choice%"=="8" goto revoke
if "%choice%"=="9" goto extend
if "%choice%"=="10" goto reinit
if "%choice%"=="0" exit /b 0
goto menu

:gen_t7
python scripts\generate_license_key.py TEST-7
goto menu

:gen_t30
python scripts\generate_license_key.py TEST-30
goto menu

:gen_pm
python scripts\generate_license_key.py PREMIUM-MONTH
goto menu

:gen_life
python scripts\generate_license_key.py LIFETIME
goto menu

:build_t7
python -c "import sys; sys.path.insert(0,'.'); from engines.test_build_manager import create_test_build; r=create_test_build('TEST-7'); print(r.get('message') or r.get('error'))"
goto menu

:build_t30
python -c "import sys; sys.path.insert(0,'.'); from engines.test_build_manager import create_test_build; r=create_test_build('TEST-30'); print(r.get('message') or r.get('error'))"
goto menu

:list_builds
python -c "import sys; sys.path.insert(0,'.'); from engines.test_build_manager import list_test_builds; bs=list_test_builds(); print('Сборок:', len(bs)); [print(b.get('key_type'), b.get('key'), b.get('zip_name'), 'REVOKED' if b.get('revoked') else '') for b in bs]"
goto menu

:revoke
set /p KEY="Ключ VM-...: "
python -c "import sys; sys.path.insert(0,'.'); from engines.test_build_manager import revoke_test_license; ok,m=revoke_test_license('%KEY%'); print(m)"
goto menu

:extend
set /p KEY="Ключ VM-...: "
python -c "import sys; sys.path.insert(0,'.'); from engines.test_build_manager import extend_test_license; ok,m=extend_test_license('%KEY%', days=7); print(m)"
goto menu

:reinit
set VM_DEV_MODE=1
python -c "import sys; sys.path.insert(0,'.'); from engines.owner_first_run import run_init; ok,m=run_init(force=True); print(m)"
set VM_DEV_MODE=
goto menu
