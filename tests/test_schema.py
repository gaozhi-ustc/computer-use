"""Tests for workflow output schema models."""

from __future__ import annotations

import json

from workflow_recorder.output.schema import (
    Action,
    ApplicationInfo,
    EnvironmentInfo,
    Verification,
    Variable,
    Workflow,
    WorkflowMetadata,
    WorkflowStep,
)


def test_action_click():
    a = Action(type="click", target="Save", coordinates=[100, 200], button="left")
    assert a.type == "click"
    assert a.coordinates == [100, 200]


def test_action_type():
    a = Action(type="type", text="hello")
    assert a.text == "hello"
    assert a.is_variable is False


def test_action_defaults():
    a = Action(type="wait")
    assert a.target == ""
    assert a.coordinates == []
    assert a.button == "left"
    assert a.amount == 3


def test_verification():
    v = Verification(expected_window_title="Notepad", expected_elements=["Save", "Cancel"])
    assert v.expected_window_title == "Notepad"
    assert len(v.expected_elements) == 2


def test_workflow_step():
    step = WorkflowStep(
        step_id=1,
        timestamp="2025-01-01T00:00:00+00:00",
        application=ApplicationInfo(process_name="notepad.exe", window_title="Test"),
        description="clicking Save",
        actions=[Action(type="click", target="Save")],
        confidence=0.9,
        source_frames=[1, 2],
    )
    assert step.step_id == 1
    assert step.application.process_name == "notepad.exe"
    assert len(step.actions) == 1


def test_workflow_metadata():
    meta = WorkflowMetadata(
        session_id="abc-123",
        recorded_at="2025-01-01T00:00:00+00:00",
        duration_seconds=120.0,
        total_frames_captured=40,
        total_steps=5,
    )
    assert meta.recorder_version == "0.1.0"


def test_workflow_creation():
    wf = Workflow(
        metadata=WorkflowMetadata(
            session_id="test-session",
            recorded_at="2025-01-01T00:00:00+00:00",
            duration_seconds=60.0,
            total_frames_captured=20,
            total_steps=3,
        ),
    )
    assert wf.schema_version == "workflow-recorder/v1"
    assert wf.steps == []
    assert wf.variables == {}


def test_workflow_json_serialization():
    wf = Workflow(
        metadata=WorkflowMetadata(
            session_id="test",
            recorded_at="2025-01-01T00:00:00+00:00",
            duration_seconds=10.0,
            total_frames_captured=5,
            total_steps=1,
        ),
        steps=[
            WorkflowStep(
                step_id=1,
                timestamp="2025-01-01T00:00:00+00:00",
                application=ApplicationInfo(process_name="test.exe", window_title="Test"),
                description="test action",
                actions=[Action(type="click", target="OK", coordinates=[100, 200])],
                confidence=0.8,
            ),
        ],
    )
    data = wf.model_dump(by_alias=True)
    assert "$schema" in data
    assert data["$schema"] == "workflow-recorder/v1"

    # Round-trip through JSON
    json_str = json.dumps(data)
    parsed = json.loads(json_str)
    assert parsed["metadata"]["session_id"] == "test"
    assert len(parsed["steps"]) == 1


def test_variable():
    v = Variable(description="User password", type="string", sensitive=True)
    assert v.sensitive is True
