# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path

block_cipher = None
ROOT = Path(SPECPATH)

a = Analysis(
    [str(ROOT / 'desktop.py')],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / 'templates'), 'templates'),
        (str(ROOT / 'static'),    'static'),
        (str(ROOT / 'data'),      'data'),
        (str(ROOT / 'engines'),   'engines'),
        (str(ROOT / 'api'),       'api'),
        (str(ROOT / 'modules'),   'modules'),
    ],
    hiddenimports=[
        'flask', 'jinja2', 'werkzeug',
        'edge_tts', 'asyncio',
        'deep_translator',
        'langdetect',
        'faster_whisper',
        'pydub',
        'ffmpeg',
        'webview',
        'engineio', 'socketio',
        'clr_loader',
        'engines.license_manager',
        'engines.license_server_client',
        'engines.translation',
        'engines.translation_compat',
        'engines.stt_engine',
        'engines.tts',
        'engines.timing_engine',
        'engines.dub_engine',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='TubeDub',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='TubeDub',
)
