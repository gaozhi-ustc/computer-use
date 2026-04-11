"""First-run initialization wizard.

Prompts the operator for any missing mandatory fields — employee ID and the
DashScope (Bailian) API key — then persists them back to the JSON config file
so subsequent launches skip the prompt.

The wizard only runs when invoked explicitly from the CLI entrypoint after
load_config(). It mutates the in-memory AppConfig as well as the on-disk
JSON, so the current process uses the freshly-entered values without a
reload.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

from workflow_recorder.config import AppConfig


def needs_wizard(config: AppConfig) -> bool:
    """True when the config is missing values the operator must supply."""
    if not config.employee_id.strip():
        return True
    if not config.analysis.openai_api_key.strip():
        return True
    return False


def run_wizard(config: AppConfig, config_path: Optional[str]) -> AppConfig:
    """Prompt for missing fields interactively and persist them.

    Returns the (possibly updated) config. If stdin is not a TTY, prints a
    helpful error and raises SystemExit rather than blocking on input().
    """
    missing_employee = not config.employee_id.strip()
    missing_api_key = not config.analysis.openai_api_key.strip()

    if not (missing_employee or missing_api_key):
        return config

    if not sys.stdin.isatty():
        _print_noninteractive_error(config_path, missing_employee, missing_api_key)
        raise SystemExit(2)

    _print_header()

    if missing_employee:
        config.employee_id = _prompt_nonempty(
            "  Employee ID (员工工号):                  ",
            field="employee_id",
        )

    if missing_api_key:
        config.analysis.openai_api_key = _prompt_nonempty(
            "  DashScope (百炼) API key (sk-...):       ",
            field="openai_api_key",
            secret=True,
        )

    print()
    print("-" * 58)

    # Persist back to disk so future runs skip the wizard.
    if config_path:
        try:
            _persist_to_json(Path(config_path), config)
            print(f"  Saved to {Path(config_path).resolve()}")
        except Exception as exc:
            print(f"  WARNING: failed to persist config ({exc}).")
            print("  You'll be prompted again next launch.")
    else:
        print("  WARNING: no config file path — values kept in memory only.")
        print("  Run with -c <path.json> to persist future edits.")

    print()
    return config


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _print_header() -> None:
    print()
    print("=" * 58)
    print("  Workflow Recorder — First-time Setup")
    print("=" * 58)
    print()
    print("  Missing required fields. Please fill them in now.")
    print("  (Values are saved to your config file so you won't be")
    print("   asked again.)")
    print()


def _print_noninteractive_error(
    config_path: Optional[str],
    missing_employee: bool,
    missing_api_key: bool,
) -> None:
    print()
    print("  ERROR: required config fields are missing and stdin is not a TTY.")
    print("  Cannot run the interactive wizard in non-interactive mode.")
    print()
    print("  Missing:")
    if missing_employee:
        print("    - employee_id")
    if missing_api_key:
        print("    - analysis.openai_api_key  (or the active preset's openai_api_key)")
    print()
    if config_path:
        print(f"  Edit {Path(config_path).resolve()} and add the missing values,")
        print(f"  then re-run workflow-recorder.")
    else:
        print("  Pass -c <path.json> pointing to a config file with these fields set.")
    print()


def _prompt_nonempty(prompt: str, *, field: str, secret: bool = False) -> str:
    """Repeatedly prompt until the user gives a non-empty answer."""
    while True:
        try:
            if secret:
                import getpass
                value = getpass.getpass(prompt)
            else:
                value = input(prompt)
        except (EOFError, KeyboardInterrupt):
            print("\n  Cancelled.")
            raise SystemExit(1)

        value = value.strip()
        if value:
            return value
        print(f"  {field} cannot be empty. Please try again.")


def _persist_to_json(path: Path, config: AppConfig) -> None:
    """Write employee_id and api_key back into the JSON config.

    The JSON file may use a `model_presets` + `active_preset` layout (the
    installer writes this), in which case we put the api_key into the
    active preset. Otherwise we fall back to `analysis.openai_api_key` at
    top level.
    """
    if not path.exists():
        # Create a minimal JSON scaffold that load_config can read back.
        data = {
            "employee_id": config.employee_id,
            "analysis": {
                "openai_api_key": config.analysis.openai_api_key,
                "model": config.analysis.model,
                "base_url": config.analysis.base_url,
            },
        }
    else:
        suffix = path.suffix.lower()
        if suffix != ".json":
            raise ValueError(
                f"Init wizard can only persist to .json configs (got {suffix}). "
                "Edit your file by hand."
            )
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        data["employee_id"] = config.employee_id

        presets = data.get("model_presets") or {}
        active = data.get("active_preset")
        if presets and active and active in presets:
            presets[active]["openai_api_key"] = config.analysis.openai_api_key
        elif presets and active == "__all__":
            # Dual mode layout — write the key to every preset that lacks one.
            for preset in presets.values():
                if not preset.get("openai_api_key"):
                    preset["openai_api_key"] = config.analysis.openai_api_key
        else:
            analysis = data.setdefault("analysis", {})
            analysis["openai_api_key"] = config.analysis.openai_api_key

    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp_path.replace(path)
