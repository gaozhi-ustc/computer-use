# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Workflow Recorder.

Builds two executables sharing one dist folder:
  - workflow-recorder.exe       (CLI entry point)
  - workflow-recorder-service.exe (Windows service)
"""

from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None
project_root = Path(SPECPATH).parent

# ── Shared hidden imports ──────────────────────────────────────────────
common_hidden = [
    # pywin32 (conditionally imported)
    'win32gui', 'win32process', 'win32con', 'win32api',
    'win32service', 'win32serviceutil', 'win32event',
    'servicemanager', 'pywintypes', 'pythoncom',
    # pydantic v2 dynamic validators
    *collect_submodules('pydantic'),
    # imagehash + transitive deps
    'imagehash', 'scipy', 'scipy.fft', 'scipy.fftpack', 'numpy',
    # openai SDK chain
    'httpx', 'httpcore', 'anyio', 'anyio._backends._asyncio',
    'sniffio', 'certifi', 'distro', 'jiter',
    # mss platform backend
    'mss.windows',
    # stdlib TOML (Python 3.11+)
    'tomllib',
    # structlog
    'structlog', 'structlog.dev', 'structlog.processors',
    # PIL plugins used by mss/imagehash
    'PIL.PngImagePlugin', 'PIL.JpegImagePlugin',
]

common_excludes = [
    'tkinter', 'matplotlib', 'test', 'unittest',
    'pytest', 'pytest_asyncio', '_pytest',
]

# ── Analysis: main CLI ─────────────────────────────────────────────────
a = Analysis(
    [str(project_root / 'src' / 'workflow_recorder' / '__main__.py')],
    pathex=[str(project_root / 'src')],
    binaries=[],
    datas=[
        (str(project_root / 'config.example.yaml'), '.'),
    ],
    hiddenimports=common_hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=common_excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='workflow-recorder',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    icon=str(project_root / 'installer' / 'icon.ico')
        if (project_root / 'installer' / 'icon.ico').exists() else None,
)

# ── Analysis: Windows service ──────────────────────────────────────────
svc_a = Analysis(
    [str(project_root / 'setup_service.py')],
    pathex=[str(project_root / 'src')],
    binaries=[],
    datas=[],
    hiddenimports=common_hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=common_excludes,
    cipher=block_cipher,
    noarchive=False,
)

svc_pyz = PYZ(svc_a.pure, svc_a.zipped_data, cipher=block_cipher)

svc_exe = EXE(
    svc_pyz, svc_a.scripts, [],
    exclude_binaries=True,
    name='workflow-recorder-service',
    debug=False,
    strip=False,
    upx=True,
    console=True,
    icon=str(project_root / 'installer' / 'icon.ico')
        if (project_root / 'installer' / 'icon.ico').exists() else None,
)

# ── Collect all into single folder ─────────────────────────────────────
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    svc_exe, svc_a.binaries, svc_a.zipfiles, svc_a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='WorkflowRecorder',
)
