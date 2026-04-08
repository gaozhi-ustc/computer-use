"""Serialize workflow documents to JSON/YAML and optional Markdown summary."""

from __future__ import annotations

import json
from pathlib import Path

import structlog

from workflow_recorder.config import OutputConfig
from workflow_recorder.output.schema import Workflow

log = structlog.get_logger()


class WorkflowWriter:
    """Write workflow documents to disk."""

    def __init__(self, config: OutputConfig):
        self.config = config

    def write(self, workflow: Workflow) -> Path:
        """Write the workflow to the configured output directory.

        Returns the path to the written file.
        """
        output_dir = Path(self.config.directory)
        output_dir.mkdir(parents=True, exist_ok=True)

        session_id = workflow.metadata.session_id
        base_name = f"workflow_{session_id[:8]}"

        # Write JSON
        if self.config.format in ("json", "both"):
            json_path = output_dir / f"{base_name}.json"
            data = workflow.model_dump(by_alias=True)
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            log.info("workflow_json_written", path=str(json_path))

        # Write YAML
        if self.config.format in ("yaml", "both"):
            import yaml
            yaml_path = output_dir / f"{base_name}.yaml"
            data = workflow.model_dump(by_alias=True)
            with open(yaml_path, "w", encoding="utf-8") as f:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
            log.info("workflow_yaml_written", path=str(yaml_path))

        # Write Markdown summary
        if self.config.include_markdown_summary:
            md_path = output_dir / f"{base_name}.md"
            self._write_markdown(workflow, md_path)
            log.info("workflow_markdown_written", path=str(md_path))

        primary_path = output_dir / f"{base_name}.{self.config.format}"
        if self.config.format == "both":
            primary_path = output_dir / f"{base_name}.json"
        return primary_path

    def _write_markdown(self, workflow: Workflow, path: Path) -> None:
        """Generate a human-readable Markdown summary of the workflow."""
        lines = [
            f"# Workflow Recording: {workflow.metadata.session_id[:8]}",
            "",
            f"- **Recorded**: {workflow.metadata.recorded_at}",
            f"- **Duration**: {workflow.metadata.duration_seconds}s",
            f"- **Frames captured**: {workflow.metadata.total_frames_captured}",
            f"- **Steps**: {workflow.metadata.total_steps}",
            f"- **Environment**: {workflow.environment.os} ({workflow.environment.hostname})",
            "",
            "---",
            "",
        ]

        for step in workflow.steps:
            lines.append(f"## Step {step.step_id}: {step.description}")
            lines.append("")
            lines.append(f"**App**: {step.application.process_name} — {step.application.window_title}")
            lines.append(f"**Confidence**: {step.confidence}")
            lines.append("")

            if step.actions:
                lines.append("**Actions**:")
                for action in step.actions:
                    lines.append(f"  - `{_format_action(action)}`")
                lines.append("")

            if step.wait_after_seconds > 0:
                lines.append(f"*Wait {step.wait_after_seconds}s before next step*")
                lines.append("")

            if step.reference_screenshot:
                lines.append(f"![Step {step.step_id}]({step.reference_screenshot})")
                lines.append("")

            lines.append("---")
            lines.append("")

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))


def _format_action(action) -> str:
    """Format an action as a concise string."""
    if action.type == "click":
        coords = f" at {action.coordinates}" if action.coordinates else ""
        return f"click({action.target}{coords}, {action.button})"
    elif action.type == "type":
        text = action.text
        if action.is_variable:
            text = f"{{{text}}}"
        return f'type("{text}")'
    elif action.type == "key":
        return f"key({action.keys})"
    elif action.type == "scroll":
        return f"scroll({action.direction}, {action.amount})"
    elif action.type == "wait":
        return f"wait({action.amount}s)"
    return f"{action.type}(...)"
