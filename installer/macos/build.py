"""One-click builder for the Workflow Recorder macOS installer.

Usage:
    python installer/macos/build.py              # PyInstaller only
    python installer/macos/build.py --pkg        # PyInstaller + .pkg
    python installer/macos/build.py --pkg --sign "Developer ID Installer: Your Name (TEAMID)"

Output:
    dist/WorkflowRecorder.app                    # PyInstaller bundle
    dist/WorkflowRecorder-<version>-macos.pkg    # distributable installer
"""

from __future__ import annotations

import argparse
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALLER_DIR = Path(__file__).resolve().parent
SPEC_FILE = INSTALLER_DIR / "workflow_recorder_macos.spec"
PLIST_TEMPLATE = INSTALLER_DIR / "com.workflow-recorder.plist"
SCRIPTS_DIR = INSTALLER_DIR / "scripts"
UNINSTALL_SH = INSTALLER_DIR / "uninstall.sh"

DIST_DIR = PROJECT_ROOT / "dist"
APP_BUNDLE = DIST_DIR / "WorkflowRecorder.app"

# Read the version straight from pyproject so we don't drift.
def _read_version() -> str:
    pyproject = PROJECT_ROOT / "pyproject.toml"
    for line in pyproject.read_text().splitlines():
        line = line.strip()
        if line.startswith("version"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return "0.0.0"


VERSION = _read_version()
PKG_ID = "com.workflow-recorder.pkg"


def run(cmd: list[str], **kwargs) -> None:
    print(f"\n{'='*64}\n  $ {' '.join(cmd)}\n{'='*64}")
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT), **kwargs)
    if result.returncode != 0:
        print(f"\nFAILED (exit {result.returncode})", file=sys.stderr)
        sys.exit(result.returncode)


def step_pyinstaller() -> None:
    print("\n[1/4] PyInstaller — building WorkflowRecorder.app")
    # Clean any stale bundle so PyInstaller's --noconfirm doesn't silently
    # leave old plugin binaries behind.
    if APP_BUNDLE.exists():
        shutil.rmtree(APP_BUNDLE)
    run([
        sys.executable, "-m", "PyInstaller",
        "--clean", "--noconfirm",
        str(SPEC_FILE),
    ])
    if not APP_BUNDLE.exists():
        sys.exit("PyInstaller did not produce WorkflowRecorder.app")


def step_smoke_test() -> None:
    print("\n[2/4] Smoke test — workflow-recorder --help")
    # The bundle's CLI is at Contents/MacOS/workflow-recorder.
    exe = APP_BUNDLE / "Contents" / "MacOS" / "workflow-recorder"
    if not exe.exists():
        sys.exit(f"missing executable inside bundle: {exe}")
    run([str(exe), "--help"])


def step_stage_resources() -> None:
    """Copy the LaunchAgent plist template into the bundle's Resources so
    the postinstall script can find it after install."""
    print("\n[3/4] Staging LaunchAgent template in bundle Resources")
    resources_dir = APP_BUNDLE / "Contents" / "Resources"
    resources_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PLIST_TEMPLATE, resources_dir / "com.workflow-recorder.plist")

    # pkgbuild requires the postinstall script to be named exactly
    # `postinstall` in the --scripts directory and executable.
    # We already made it executable on disk — pkgbuild preserves that.


def step_pkgbuild(sign_identity: str | None) -> Path:
    """Wrap the .app into a .pkg via pkgbuild + productbuild.

    Uses a fresh tempdir for the package root so we never have to worry
    about stale staging (pkgbuild can leave files with odd ownership
    that the user-scoped shutil.rmtree can't clean up on the next run).
    """
    print("\n[4/4] pkgbuild — assembling .pkg")
    staging_parent = Path(tempfile.mkdtemp(prefix="workflow-recorder-pkg-",
                                           dir=str(DIST_DIR)))
    try:
        applications = staging_parent / "Applications"
        applications.mkdir(parents=True)
        # Mirror what should appear under /. pkgbuild handles permissions
        # and the cpio payload from here.
        shutil.copytree(APP_BUNDLE, applications / "WorkflowRecorder.app",
                        symlinks=True)

        # Drop uninstall.sh into the bundle's Resources — admins have a
        # known location to grab it from.
        resources = applications / "WorkflowRecorder.app" / "Contents" / "Resources"
        shutil.copy2(UNINSTALL_SH, resources / "uninstall.sh")

        # Build the component pkg first.
        component_pkg = DIST_DIR / f"WorkflowRecorder-component-{VERSION}.pkg"
        run([
            "pkgbuild",
            "--root", str(staging_parent),
            "--identifier", PKG_ID,
            "--version", VERSION,
            "--install-location", "/",
            "--scripts", str(SCRIPTS_DIR),
            str(component_pkg),
        ])

        # Wrap it into a product archive (the thing users double-click).
        final_pkg = DIST_DIR / f"WorkflowRecorder-{VERSION}-macos.pkg"
        prod_cmd = [
            "productbuild",
            "--package", str(component_pkg),
            "--identifier", PKG_ID,
            "--version", VERSION,
        ]
        if sign_identity:
            prod_cmd += ["--sign", sign_identity]
        prod_cmd.append(str(final_pkg))
        run(prod_cmd)

        component_pkg.unlink(missing_ok=True)
        return final_pkg
    finally:
        # chmod first: pkgbuild can drop files we no longer have write
        # permission to. Ignore errors — best effort.
        subprocess.run(["chmod", "-R", "u+w", str(staging_parent)],
                       stderr=subprocess.DEVNULL)
        shutil.rmtree(staging_parent, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pkg", action="store_true",
                        help="Build the .pkg installer (not just the .app)")
    parser.add_argument("--sign", default=None,
                        help='Developer ID Installer cert, e.g. '
                             '"Developer ID Installer: Name (TEAMID)"')
    args = parser.parse_args()

    step_pyinstaller()
    step_smoke_test()

    if args.pkg:
        step_stage_resources()
        pkg = step_pkgbuild(args.sign)
        size_mb = pkg.stat().st_size / (1024 * 1024)
        print("\n" + "=" * 64)
        print("  BUILD COMPLETE")
        print(f"    App bundle: {APP_BUNDLE}")
        print(f"    Installer:  {pkg}  ({size_mb:.1f} MB)")
        print("=" * 64)
    else:
        print("\n" + "=" * 64)
        print("  BUILD COMPLETE (app bundle only; --pkg to also build installer)")
        print(f"    App bundle: {APP_BUNDLE}")
        print("=" * 64)


if __name__ == "__main__":
    main()
