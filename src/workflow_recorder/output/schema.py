"""Pydantic models for the workflow output document."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Action(BaseModel):
    """A single computer-use action primitive."""
    type: str  # click, type, key, scroll, wait
    target: str = ""  # semantic label of the UI element
    coordinates: list[int] = Field(default_factory=list)  # [x, y]
    button: str = "left"  # left, right, middle (for click)
    text: str = ""  # for type action
    keys: str = ""  # for key action, e.g. "ctrl+s"
    direction: str = ""  # for scroll: up, down
    amount: int = 3  # for scroll
    is_variable: bool = False  # if True, text is a {placeholder}


class Verification(BaseModel):
    """Expected state after executing the step, for replay validation."""
    expected_window_title: str = ""
    expected_elements: list[str] = Field(default_factory=list)


class ApplicationInfo(BaseModel):
    process_name: str
    window_title: str
    window_rect: list[int] = Field(default_factory=list)  # [left, top, right, bottom]


class WorkflowStep(BaseModel):
    """A single logical step in the workflow."""
    step_id: int
    timestamp: str  # ISO format
    application: ApplicationInfo
    description: str
    actions: list[Action]
    wait_after_seconds: float = 0.0
    reference_screenshot: str = ""
    verification: Verification = Field(default_factory=Verification)
    confidence: float = 0.0
    source_frames: list[int] = Field(default_factory=list)


class Variable(BaseModel):
    description: str = ""
    type: str = "string"
    sensitive: bool = False


class EnvironmentInfo(BaseModel):
    screen_resolution: list[int] = Field(default_factory=list)  # [width, height]
    os: str = ""
    hostname: str = ""


class WorkflowMetadata(BaseModel):
    session_id: str
    recorded_at: str  # ISO format
    duration_seconds: float
    total_frames_captured: int
    total_steps: int
    recorder_version: str = "0.1.0"


class Workflow(BaseModel):
    """The complete workflow document."""
    schema_version: str = Field(default="workflow-recorder/v1", alias="$schema")
    metadata: WorkflowMetadata
    environment: EnvironmentInfo = Field(default_factory=EnvironmentInfo)
    steps: list[WorkflowStep] = Field(default_factory=list)
    variables: dict[str, Variable] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}
