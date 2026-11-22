# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
root = Path(SPECPATH).parent
assets = root / 'assets'
a = Analysis(
    [str(root / 'src' / 'autozrzuty.py')],
    pathex=[str(root / 'tools')],
    binaries=[],
    datas=[(str(root / 'licenses'), 'licenses'),
           (str(root / 'THIRD_PARTY_LICENSES.txt'), '.'),
           (str(root / 'INSTALACJA_DMG.txt'), '.')],
    hiddenimports=['mss.darwin', 'pynput.keyboard._darwin',
                   'pynput.mouse._darwin', 'pynput._util.darwin',
                   'pystray._darwin', 'pystray._util',
                   'Quartz', 'Quartz.CoreGraphics', 'AppKit', 'Foundation',
                   'ApplicationServices', 'check_bundle_runtime'],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['Xlib', 'pygetwindow', 'win32gui', 'PyObjCTest', 'pytest',
              'setuptools', 'pip', 'PyInstaller'],
    noarchive=False, optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name='AutoZrzuty',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False, argv_emulation=False,
    target_arch='universal2', codesign_identity=None, entitlements_file=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='AutoZrzuty')
app = BUNDLE(
    coll, name='AutoZrzuty.app', icon=str(assets / 'AutoZrzuty.icns'),
    bundle_identifier='local.autozrzuty.app',
    version='1.0.1',
    info_plist={
        'CFBundleDisplayName': 'AutoZrzuty',
        'CFBundleShortVersionString': '1.0.1',
        'CFBundleVersion': '1.0.1',
        'LSMinimumSystemVersion': '11.0',
        'LSApplicationCategoryType': 'public.app-category.utilities',
        'NSHighResolutionCapable': True,
        'NSInputMonitoringUsageDescription': 'Skróty Start/Pauza/Stop oraz wykrywanie czasu bezczynności. Klawisze nie są zapisywane.',
        'NSScreenCaptureUsageDescription': 'Zapisywanie lokalnych zrzutów ekranu JPG w wybranym folderze.',
    },
)
