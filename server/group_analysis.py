"""Group-level vision analysis: multi-image prompt construction and response parsing."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

GROUP_SYSTEM_PROMPT = """You are a workflow SOP extraction expert. You will receive a sequence of screenshots captured over time, along with OS-level mouse cursor coordinates for each frame.

IMPORTANT — How to use cursor data to identify actions:
- The cursor coordinates are captured by the OS every few seconds (not on every mouse event).
- When the cursor jumps to a new position between frames and then stays still for 1-2 frames, the user almost certainly CLICKED at that position. The screenshot AFTER the jump shows the result of the click.
- I have pre-analyzed the cursor movement and marked frames with "⚡ LIKELY CLICK" where a significant cursor jump was detected. Use these as strong evidence of a user action.
- For each LIKELY CLICK, look at the screenshot to identify WHAT UI element is at those coordinates, and what changed between the frame before and after.
- Cursor staying in the same position across multiple frames usually means the user is reading/waiting — not a separate action.

Your task: for each detected user action, produce a reproducible SOP step that describes HOW to transition from the current state to the next state.

For each step, output:
{
    "step_order": <int>,
    "title": "<short action verb phrase, e.g. 'Click Export button', 'Select date range'>",
    "human_description": "<step-by-step instruction a human can follow: what to look for on screen, where to click/type, and what should happen after>",
    "machine_actions": [
        {
            "type": "click|double_click|right_click|type|key|scroll|drag",
            "x": <pixel x from cursor data>,
            "y": <pixel y from cursor data>,
            "target": "<UI element name visible at those coordinates>",
            "text": "<for type actions>",
            "key": "<for key actions, e.g. Enter, Ctrl+S>"
        }
    ],
    "application": "<application name>",
    "key_frame_indices": [<frame index AFTER the action that shows the result>]
}

Return a JSON object: {"steps": [...]}

Guidelines:
- Focus on ACTIONS (click, type, select, scroll), not on passive states (viewing, reading)
- Every step must describe what the user DID, not what the screen shows
- Use the ⚡ LIKELY CLICK markers and cursor coordinates as primary evidence for actions
- machine_actions coordinates MUST come from the actual cursor data, not guessed from screenshots
- If multiple frames show no cursor movement and no screen change, they represent ONE state — do not create separate steps for them
- human_description should tell the reader exactly where to click and what to expect after clicking"""

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
    """Build the text portion of the user message with cursor-jump analysis."""
    import math

    lines = [
        f"Here are {len(frames)} sequential screenshots from a recording session.",
        "For each frame I provide: timestamp, cursor position, and whether a likely click was detected.",
        "",
    ]

    # Pre-compute cursor jumps to detect likely clicks
    CLICK_THRESHOLD = 50  # pixels — movement above this between frames = likely click
    click_frames: list[int] = []

    for i in range(1, len(frames)):
        cx1 = frames[i - 1].get("cursor_x", -1)
        cy1 = frames[i - 1].get("cursor_y", -1)
        cx2 = frames[i].get("cursor_x", -1)
        cy2 = frames[i].get("cursor_y", -1)
        if cx1 >= 0 and cy1 >= 0 and cx2 >= 0 and cy2 >= 0:
            dist = math.sqrt((cx2 - cx1) ** 2 + (cy2 - cy1) ** 2)
            if dist > CLICK_THRESHOLD:
                click_frames.append(i)

    lines.append(f"Detected {len(click_frames)} likely click actions based on cursor movement.")
    lines.append("")
    lines.append("Frame data:")

    for i, f in enumerate(frames):
        cx = f.get("cursor_x", -1)
        cy = f.get("cursor_y", -1)
        ts = f.get("recorded_at", "unknown")
        fr = f.get("focus_rect") or None

        # Compute movement from previous frame
        move_info = ""
        if i > 0:
            px = frames[i - 1].get("cursor_x", -1)
            py = frames[i - 1].get("cursor_y", -1)
            if px >= 0 and py >= 0 and cx >= 0 and cy >= 0:
                dist = math.sqrt((cx - px) ** 2 + (cy - py) ** 2)
                if dist > CLICK_THRESHOLD:
                    move_info = f" ⚡ LIKELY CLICK (cursor moved {int(dist)}px from ({px},{py}))"
                elif dist < 5:
                    move_info = " (cursor stable)"

        focus_str = f", focus_rect={fr}" if fr else ""
        lines.append(
            f"- Frame {i}: timestamp={ts}, cursor=({cx}, {cy}){focus_str}{move_info}"
        )

    lines.append("")
    lines.append(
        "Based on the screenshots and cursor data above, extract the ACTION steps. "
        "Focus on what the user DID at each ⚡ LIKELY CLICK point — identify the UI element "
        "at the click coordinates, and describe the action and its effect."
    )
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
