"""Verify the PyInstaller build includes all required files."""

import sys
from pathlib import Path

dist_dir = Path(__file__).resolve().parent.parent / "dist" / "WorkflowRecorder"
internal_dir = dist_dir / "_internal"

required_files = [
    "workflow-recorder.exe",
    "workflow-recorder-service.exe",
]

# Files inside _internal (PyInstaller 6.x layout)
required_internal = [
    "config.example.yaml",
]

# pywin32 DLLs (version-specific name, may be in _internal/pywin32_system32/)
pywin32_dlls = list(dist_dir.rglob("pywintypes*.dll"))
pythoncom_dlls = list(dist_dir.rglob("pythoncom*.dll"))

print(f"Checking dist dir: {dist_dir}")
print()

errors = []
for f in required_files:
    path = dist_dir / f
    if path.exists():
        size_mb = path.stat().st_size / (1024 * 1024)
        print(f"  OK  {f} ({size_mb:.1f} MB)")
    else:
        print(f"  MISSING  {f}")
        errors.append(f)

for f in required_internal:
    path = internal_dir / f
    if path.exists():
        print(f"  OK  _internal/{f}")
    else:
        print(f"  MISSING  _internal/{f}")
        errors.append(f"_internal/{f}")

if pywin32_dlls:
    print(f"  OK  {pywin32_dlls[0].name}")
else:
    print("  MISSING  pywintypes*.dll")
    errors.append("pywintypes*.dll")

if pythoncom_dlls:
    print(f"  OK  {pythoncom_dlls[0].name}")
else:
    print("  MISSING  pythoncom*.dll")
    errors.append("pythoncom*.dll")

# Count total size
total_size = sum(f.stat().st_size for f in dist_dir.rglob("*") if f.is_file())
print(f"\nTotal dist size: {total_size / (1024 * 1024):.0f} MB")

if errors:
    print(f"\nBUILD VERIFICATION FAILED: {len(errors)} missing file(s)")
    sys.exit(1)
else:
    print("\nBuild verification PASSED.")
