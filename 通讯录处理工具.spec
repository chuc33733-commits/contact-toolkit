# -*- mode: python ; coding: utf-8 -*-
"""通讯录处理工具 · PyInstaller 打包配置（单文件、无控制台）。

用法（在仓库根目录）：
    pyinstaller --clean --noconfirm 通讯录处理工具.spec
产物：dist/通讯录处理工具.exe
"""

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=collect_dynamic_libs('chardet'),
    datas=[],
    # chardet 5.x 的 models/pipeline 是 mypyc 编译的子模块，静态分析看不到，
    # 必须显式收集，否则运行时报 No module named 'chardet.models._format__mypyc'
    hiddenimports=collect_submodules('chardet'),
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
    name='通讯录处理工具',
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
