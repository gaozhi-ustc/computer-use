# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Workflow Recorder on macOS.

Produces a .app bundle at dist/WorkflowRecorder.app whose executable is
the daemon CLI. It's packaged as a bundle (rather than a loose binary)
so macOS's Privacy & Security dialog can identify it by name when the
user grants Screen Recording / Accessibility permissions.

The bundle is invoked headlessly by a LaunchAgent (installed by the
.pkg postinstall script), not by double-click — so Info.plist has
LSUIElement=1 to suppress the Dock icon.
"""

from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None
project_root = Path(SPECPATH).resolve().parent.parent


def _read_version() -> str:
    """Keep the bundle version in lock-step with pyproject.toml."""
    for line in (project_root / "pyproject.toml").read_text().splitlines():
        s = line.strip()
        if s.startswith("version"):
            return s.split("=", 1)[1].strip().strip('"').strip("'")
    return "0.0.0"


PROJECT_VERSION = _read_version()

hidden_imports = [
    # pyobjc — imported lazily inside capture/* so PyInstaller misses them
    'objc',
    'AppKit', 'Foundation', 'CoreFoundation',
    'Quartz', 'Quartz.CoreGraphics',
    'ApplicationServices', 'ApplicationServices.HIServices',
    # pydantic v2 dynamic validators
    *collect_submodules('pydantic'),
    # imagehash + transitive deps
    'imagehash', 'scipy', 'scipy.fft', 'scipy.fftpack', 'numpy',
    # openai SDK chain
    'httpx', 'httpcore', 'anyio', 'anyio._backends._asyncio',
    'sniffio', 'certifi', 'distro', 'jiter',
    # mss darwin backend
    'mss.darwin',
    # stdlib TOML on 3.11+
    'tomllib',
    # structlog
    'structlog', 'structlog.dev', 'structlog.processors',
    # PIL plugins
    'PIL.PngImagePlugin', 'PIL.JpegImagePlugin',
]

excludes = [
    'tkinter', 'matplotlib',
    'pytest', 'pytest_asyncio', '_pytest',
    # NOTE: do NOT exclude 'unittest' or 'test' — scipy.fft (pulled in by
    # imagehash.phash) lazily imports them during its module init path.
    # Stripping them breaks drop_idle_duplicate_frames silently with
    # "No module named 'unittest'" warnings in production.
    # Windows-only
    'win32gui', 'win32process', 'win32con', 'win32api',
    'win32service', 'win32serviceutil', 'pywintypes',
]

a = Analysis(
    [str(project_root / 'src' / 'workflow_recorder' / '__main__.py')],
    pathex=[str(project_root / 'src')],
    binaries=[],
    datas=[
        (str(project_root / 'config.example.yaml'), '.'),
        (str(project_root / 'model_config.example.json'), '.'),
    ],
    hiddenimports=hidden_imports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
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
    upx=False,
    console=True,  # keeps stderr for LaunchAgent StandardErrorPath
)

coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=False, upx_exclude=[],
    name='workflow-recorder',
)

app = BUNDLE(
    coll,
    name='WorkflowRecorder.app',
    icon=None,
    bundle_identifier='com.workflow-recorder.daemon',
    info_plist={
        'CFBundleName': 'WorkflowRecorder',
        'CFBundleDisplayName': 'Workflow Recorder',
        'CFBundleShortVersionString': PROJECT_VERSION,
        'CFBundleVersion': PROJECT_VERSION,
        'CFBundleIdentifier': 'com.workflow-recorder.daemon',
        # LSUIElement=1: background app, no Dock icon, no menu bar entry.
        'LSUIElement': True,
        # Minimum macOS: Big Sur (matches pyobjc wheels' baseline).
        'LSMinimumSystemVersion': '11.0',
        # Screen Recording / Accessibility permission descriptions —
        # shown in the system prompt the first time the binary asks
        # for access. Plain text only, user-facing.
        'NSScreenCaptureUsageDescription':
            'Workflow Recorder captures the screen to analyze workflow steps.',
        'NSAppleEventsUsageDescription':
            'Workflow Recorder reads the active window title.',
        # Not strictly required for our current APIs, but useful if AX
        # falls back to Apple Events for window title.
    },
)
