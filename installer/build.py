"""One-click build script for Workflow Recorder installer.

Usage:
    python installer/build.py              # PyInstaller only
    python installer/build.py --installer  # PyInstaller + Inno Setup
"""

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SPEC_FILE = PROJECT_ROOT / "installer" / "workflow_recorder.spec"
ISS_FILE = PROJECT_ROOT / "installer" / "workflow_recorder.iss"
VERIFY_SCRIPT = PROJECT_ROOT / "installer" / "verify_build.py"
DIST_DIR = PROJECT_ROOT / "dist" / "WorkflowRecorder"

# Common Inno Setup locations
ISCC_PATHS = [
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
]


def run(cmd, **kwargs):
    print(f"\n{'='*60}")
    print(f"  {' '.join(cmd) if isinstance(cmd, list) else cmd}")
    print(f"{'='*60}\n")
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT), **kwargs)
    if result.returncode != 0:
        print(f"\nFAILED (exit code {result.returncode})")
        sys.exit(result.returncode)


def find_iscc():
    for p in ISCC_PATHS:
        if Path(p).exists():
            return p
    # Try PATH
    try:
        subprocess.run(["iscc", "/?"], capture_output=True)
        return "iscc"
    except FileNotFoundError:
        return None


def main():
    build_installer = "--installer" in sys.argv

    # Step 1: PyInstaller
    print("\n[1/4] Building with PyInstaller...")
    run([sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm",
         str(SPEC_FILE)])

    # Step 2: Verify
    print("\n[2/4] Verifying build...")
    run([sys.executable, str(VERIFY_SCRIPT)])

    # Step 3: Smoke test
    print("\n[3/4] Smoke test...")
    exe = DIST_DIR / "workflow-recorder.exe"
    run([str(exe), "--help"])

    # Step 4: Inno Setup (optional)
    if build_installer:
        print("\n[4/4] Compiling Inno Setup installer...")
        iscc = find_iscc()
        if iscc is None:
            print("ERROR: Inno Setup 6 not found.")
            print("Download from: https://jrsoftware.org/isdl.php")
            print("Or run with just PyInstaller output: python installer/build.py")
            sys.exit(1)
        run([iscc, str(ISS_FILE)])
        setup_exe = list((PROJECT_ROOT / "dist").glob("WorkflowRecorder-*-Setup.exe"))
        if setup_exe:
            print(f"\nInstaller created: {setup_exe[0]}")
            print(f"Size: {setup_exe[0].stat().st_size / (1024*1024):.0f} MB")
    else:
        print("\n[4/4] Skipping Inno Setup (use --installer to build .exe installer)")

    print("\n" + "="*60)
    print("  BUILD COMPLETE")
    print(f"  Dist folder: {DIST_DIR}")
    print("="*60)


if __name__ == "__main__":
    main()
