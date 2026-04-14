"""Data models for single-frame analysis results."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class UIElement(BaseModel):
    name: str
    element_type: str = ""  # button, input, menu, link, etc.
    coordinates: list[int] = Field(default_factory=list)  # [x, y] approximate center


class FrameAnalysis(BaseModel):
    """Result of analyzing a single screenshot with GPT vision."""

    frame_index: int
    timestamp: float
    application: str
    window_title: str
    user_action: str
    ui_elements_visible: list[UIElement] = Field(default_factory=list)
    text_content: str = ""
    mouse_position_estimate: list[int] = Field(default_factory=list)  # [x, y]
    confidence: float = 0.0
    context_data: dict[str, Any] = Field(default_factory=dict)
    """Application-specific structured context (Excel headers, browser title, etc.)."""
