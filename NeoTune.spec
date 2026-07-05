# -*- mode: python ; coding: utf-8 -*-
# NEO TUNE build spec
# Build on Windows:  pyinstaller --noconfirm --clean NeoTune.spec
# Output:            dist\NeoTune.exe  (single-file, no console)

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# Bundle the app icon + CustomTkinter's theme/font assets.
# (Without the CustomTkinter assets the exe builds but crashes at launch
#  with a "theme .json not found" error -- this is the usual gotcha.)
datas = [('neotune_ico.ico', '.')]
datas += collect_data_files('customtkinter')

hiddenimports = ['psutil', 'PIL', 'customtkinter']
hiddenimports += collect_submodules('pystray')   # pulls in the win32 tray backend


a = Analysis(
    ['neo_tune_pro.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='NeoTune',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,               # set to False if your antivirus flags the build
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # GUI app, no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='neotune_ico.ico',
    uac_admin=True,         # request Administrator on launch (DNS/Defender/SFC fixes need it)
)
