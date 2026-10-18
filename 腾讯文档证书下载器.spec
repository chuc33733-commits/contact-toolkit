# -*- mode: python ; coding: utf-8 -*-
"""腾讯文档证书下载器 · PyInstaller 打包配置（单文件、无控制台）。

用法（在仓库根目录）：
    pyinstaller --clean --noconfirm 腾讯文档证书下载器.spec
产物：dist/腾讯文档证书下载器.exe
"""

a = Analysis(
    ['certdown/cert_gui.py'],
    pathex=['certdown'],
    binaries=[],
    datas=[],
    hiddenimports=['cert_downloader', 'exporter'],
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
    name='腾讯文档证书下载器',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
