"""Tests for workflow output writer."""

from __future__ import annotations

import json
from pathlib import Path

from workflow_recorder.config import OutputConfig
from workflow_recorder.output.schema import (
    Action,
    ApplicationInfo,
    Workflow,
    WorkflowMetadata,
    WorkflowStep,
)
from workflow_recorder.output.writer import WorkflowWriter, _format_action


def _make_workflow(session_id: str = "test-1234-5678") -> Workflow:
    return Workflow(
        metadata=WorkflowMetadata(
            session_id=session_id,
            recorded_at="2025-01-01T00:00:00+00:00",
            duration_seconds=30.0,
            total_frames_captured=10,
            total_steps=2,
        ),
        steps=[
            WorkflowStep(
                step_id=1,
                timestamp="2025-01-01T00:00:00+00:00",
                application=ApplicationInfo(
                    process_name="notepad.exe",
                    window_title="Test",
                ),
                description="clicking Save button",
                actions=[Action(type="click", target="Save", coordinates=[100, 200])],
                confidence=0.9,
                source_frames=[1],
            ),
            WorkflowStep(
                step_id=2,
                timestamp="2025-01-01T00:00:05+00:00",
                application=ApplicationInfo(
                    process_name="notepad.exe",
                    window_title="Test",
                ),
                description="typing hello",
                actions=[Action(type="type", text="hello world")],
                confidence=0.85,
                source_frames=[2, 3],
            ),
        ],
    )


def test_write_json(tmp_output_dir):
    config = OutputConfig(
        directory=str(tmp_output_dir),
        format="json",
        include_markdown_summary=False,
    )
    writer = WorkflowWriter(config)
    path = writer.write(_make_workflow())

    assert path.exists()
    assert path.suffix == ".json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["metadata"]["session_id"] == "test-1234-5678"
    assert len(data["steps"]) == 2


def test_write_yaml(tmp_output_dir):
    import yaml
    config = OutputConfig(
        directory=str(tmp_output_dir),
        format="yaml",
        include_markdown_summary=False,
    )
    writer = WorkflowWriter(config)
    path = writer.write(_make_workflow())

    assert path.exists()
    assert path.suffix == ".yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["metadata"]["session_id"] == "test-1234-5678"


def test_write_both(tmp_output_dir):
    config = OutputConfig(
        directory=str(tmp_output_dir),
        format="both",
        include_markdown_summary=False,
    )
    writer = WorkflowWriter(config)
    writer.write(_make_workflow())

    files = list(tmp_output_dir.glob("workflow_*"))
    extensions = {f.suffix for f in files}
    assert ".json" in extensions
    assert ".yaml" in extensions


def test_write_markdown_summary(tmp_output_dir):
    config = OutputConfig(
        directory=str(tmp_output_dir),
        format="json",
        include_markdown_summary=True,
    )
    writer = WorkflowWriter(config)
    writer.write(_make_workflow())

    md_files = list(tmp_output_dir.glob("*.md"))
    assert len(md_files) == 1
    content = md_files[0].read_text(encoding="utf-8")
    assert "clicking Save button" in content
    assert "notepad.exe" in content


def test_format_action_click():
    a = Action(type="click", target="Save", coordinates=[100, 200], button="left")
    assert "click" in _format_action(a)
    assert "Save" in _format_action(a)


def test_format_action_type():
    a = Action(type="type", text="hello")
    assert 'type("hello")' == _format_action(a)


def test_format_action_key():
    a = Action(type="key", keys="ctrl+s")
    assert "key(ctrl+s)" == _format_action(a)


def test_format_action_scroll():
    a = Action(type="scroll", direction="down", amount=3)
    assert "scroll(down, 3)" == _format_action(a)


def test_format_action_wait():
    a = Action(type="wait", amount=2)
    assert "wait(2s)" == _format_action(a)


def test_format_action_variable():
    a = Action(type="type", text="password", is_variable=True)
    result = _format_action(a)
    assert "{password}" in result
