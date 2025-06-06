# -*- mode: python ; coding: utf-8 -*-
# このファイルはPyInstallerによって自動生成されたもので、それをカスタマイズして使用しています。
from PyInstaller.utils.hooks import collect_data_files
import os

datas = [
    ('resources', 'resources'),
    ('engine_manifest.json', '.'),
    ('licenses.json', '.'),
]
datas += collect_data_files('pyopenjtalk')
datas += collect_data_files('e2k')

core_model_dir_path = os.environ.get('CORE_MODEL_DIR_PATH')
if core_model_dir_path:
    print('CORE_MODEL_DIR_PATH is found:', core_model_dir_path)
    if not os.path.isdir(core_model_dir_path):
        raise Exception("CORE_MODEL_DIR_PATH was found, but it is not directory!")
    datas += [(core_model_dir_path, "model")]

# コアとONNX Runtimeはバイナリであるが、`binaries`に加えると
# 依存関係のパスがPyInstallerに書き換えらるので、`datas`に加える
# 参考: https://github.com/VOICEVOX/voicevox_engine/pull/446#issuecomment-1210052318
libcore_path = os.environ.get('LIBCORE_PATH')
if libcore_path:
    print('LIBCORE_PATH is found:', libcore_path)
    if not os.path.isfile(libcore_path):
        raise Exception("LIBCORE_PATH was found, but it is not file!")
    datas += [(libcore_path, ".")]

libonnxruntime_path = os.environ.get('LIBONNXRUNTIME_PATH')
if libonnxruntime_path:
    print('LIBONNXRUNTIME_PATH is found:', libonnxruntime_path)
    if not os.path.isfile(libonnxruntime_path):
        raise Exception("LIBCORE_PATH was found, but it is not file!")
    datas += [(libonnxruntime_path, ".")]


block_cipher = None


a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        # NumPy 2.x で生成した Pickle を含む .npy/.npz ファイルを NumPy 1.x で読み込むために必要
        # numpy._core 以下のダミーモジュールを明示的に hiddenimports に追加しないと ModuleNotFoundError が発生する
        # ref: https://github.com/numpy/numpy/issues/24844
        'numpy._core',
        'numpy._core._dtype_ctypes',
        'numpy._core._dtype',
        'numpy._core._internal',
        'numpy._core._multiarray_umath',
        'numpy._core.multiarray',
        'numpy._core.umath',
    ],
    hookspath=[],
    hooksconfig={},
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
    name='run',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='run',
)
