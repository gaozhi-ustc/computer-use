"""Group-level vision analysis: multi-image prompt construction and response parsing."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

GROUP_SYSTEM_PROMPT = """You are a workflow SOP extraction expert. You will receive a sequence of screenshots captured over time, along with mouse cursor coordinates and focus region data for each frame.

Your task: identify the discrete user actions and produce reproducible SOP steps.

For each step, output:
{
    "step_order": <int>,
    "title": "<short action title>",
    "human_description": "<detailed description a human can follow to reproduce this action, including specific UI elements, their locations, and what to look for>",
    "machine_actions": [
        {
            "type": "click|double_click|right_click|type|key|scroll|drag",
            "x": <pixel x>,
            "y": <pixel y>,
            "target": "<UI element name>",
            "text": "<for type actions>",
            "key": "<for key actions, e.g. Enter, Ctrl+S>"
        }
    ],
    "application": "<application name>",
    "key_frame_indices": [<indices within this group that best represent this step>]
}

Return a JSON object: {"steps": [...]}

Guidelines:
- One step = one logical user action (may span multiple frames)
- Include precise coordinates from the cursor data provided
- human_description should be detailed enough for someone unfamiliar with the workflow
- machine_actions should be precise enough for RPA replay
- key_frame_indices reference the 0-based index within the provided image sequence"""

REFINE_SYSTEM_PROMPT = """You are an SOP refinement assistant. You will receive:
1. The original screenshot sequence from a workflow recording
2. The current SOP steps (which you or a previous version generated)
3. User feedback requesting specific changes

Revise the SOP steps according to the feedback. Maintain the same output format as the original generation. Only change what the feedback requests — preserve steps that are not mentioned.

Return a JSON object: {"steps": [...]}"""


@dataclass
class GroupAnalysisInput:
    """Everything needed to analyze a frame group."""
    group_id: int
    session_id: str
    frames: list[dict[str, Any]]


def build_user_prompt(frames: list[dict[str, Any]]) -> str:
    """Build the text portion of the user message."""
    lines = [f"Here are {len(frames)} sequential screenshots from a recording session.",
             "For each frame I provide: timestamp, cursor position (x, y), and focus region [x1, y1, x2, y2] if available.",
             "",
             "Frame data:"]
    for i, f in enumerate(frames):
        cx = f.get("cursor_x", -1)
        cy = f.get("cursor_y", -1)
        fr = f.get("focus_rect") or "none"
        ts = f.get("recorded_at", "unknown")
        lines.append(f"- Frame {i}: timestamp={ts}, cursor=({cx}, {cy}), focus_rect={fr}")
    lines.append("")
    lines.append("Please extract the SOP steps from this image sequence.")
    return "\n".join(lines)


def build_refine_user_prompt(current_steps_json: str, feedback_text: str,
                              feedback_scope: str,
                              frames: list[dict[str, Any]]) -> str:
    """Build prompt for SOP refinement."""
    lines = [
        "Current SOP steps:",
        current_steps_json,
        "",
        f"User feedback (scope: {feedback_scope}):",
        f'"{feedback_text}"',
        "",
        f"Original recording has {len(frames)} frames (images attached).",
        "",
        "Please output the revised steps.",
    ]
    return "\n".join(lines)


def build_image_content_blocks(frames: list[dict[str, Any]]) -> list[dict]:
    """Build OpenAI-format image content blocks from frame image paths."""
    blocks: list[dict] = []
    for f in frames:
        path = Path(f.get("image_path", ""))
        if not path.is_file():
            continue
        data = path.read_bytes()
        suffix = path.suffix.lower()
        mime = "image/png" if suffix == ".png" else "image/jpeg"
        b64 = base64.b64encode(data).decode("ascii")
        blocks.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64}"},
        })
    return blocks


def parse_steps_response(raw_text: str) -> list[dict[str, Any]]:
    """Parse LLM response into a list of step dicts."""
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(text[start:end])
        else:
            return []

    if isinstance(parsed, dict) and "steps" in parsed:
        return parsed["steps"]
    if isinstance(parsed, list):
        return parsed
    return []
