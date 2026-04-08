"""Map high-level GPT action descriptions to computer-use action primitives."""

from __future__ import annotations

import re

from workflow_recorder.analysis.frame_analysis import FrameAnalysis
from workflow_recorder.output.schema import Action


def map_to_actions(analysis: FrameAnalysis) -> list[Action]:
    """Convert a FrameAnalysis into a list of Action primitives.

    Uses rule-based pattern matching on user_action text.
    """
    text = analysis.user_action.lower()
    actions: list[Action] = []

    # Pattern: clicking something
    click_match = re.search(
        r"click(?:ing|ed|s)?\s+(?:on\s+)?(?:the\s+)?(.+?)(?:\s+(?:button|link|icon|tab|menu|at))?",
        text,
    )
    if click_match:
        target = click_match.group(1).strip().rstrip(" .")
        coords = _best_coordinates(analysis, target)
        button = "right" if "right-click" in text or "right click" in text else "left"
        if "double" in text:
            actions.append(Action(type="click", target=target, coordinates=coords, button=button))
            actions.append(Action(type="click", target=target, coordinates=coords, button=button))
        else:
            actions.append(Action(type="click", target=target, coordinates=coords, button=button))
        return actions

    # Pattern: typing text
    type_match = re.search(r"typ(?:ing|ed|es?)\s+(?:in\s+)?(?:the\s+)?['\"]?(.+?)['\"]?$", text)
    if type_match:
        typed_text = type_match.group(1).strip().rstrip(" .")
        actions.append(Action(type="type", text=typed_text))
        return actions

    # Pattern: pressing keyboard shortcut
    key_match = re.search(
        r"press(?:ing|ed|es)?\s+(.+?)$",
        text,
    )
    if key_match:
        keys = key_match.group(1).strip().rstrip(" .")
        # Normalize: "Ctrl + S" -> "ctrl+s"
        keys = re.sub(r"\s*\+\s*", "+", keys).lower()
        actions.append(Action(type="key", keys=keys))
        return actions

    # Pattern: scrolling
    scroll_match = re.search(r"scroll(?:ing|ed|s)?\s+(up|down)", text)
    if scroll_match:
        direction = scroll_match.group(1)
        actions.append(Action(type="scroll", direction=direction, amount=3))
        return actions

    # Pattern: waiting / reading / loading
    if any(w in text for w in ("waiting", "loading", "reading", "viewing", "idle")):
        actions.append(Action(type="wait", amount=1))
        return actions

    # Fallback: record as a generic description
    if analysis.mouse_position_estimate:
        actions.append(Action(
            type="click",
            target=analysis.user_action,
            coordinates=analysis.mouse_position_estimate,
        ))
    else:
        actions.append(Action(type="wait", amount=1))

    return actions


def _best_coordinates(analysis: FrameAnalysis, target: str) -> list[int]:
    """Find the best matching coordinates for a target from UI elements."""
    target_lower = target.lower()

    # Try exact match first, then substring
    for elem in analysis.ui_elements_visible:
        if elem.name.lower() == target_lower and elem.coordinates:
            return elem.coordinates

    for elem in analysis.ui_elements_visible:
        if target_lower in elem.name.lower() and elem.coordinates:
            return elem.coordinates

    # Fall back to mouse position estimate
    if analysis.mouse_position_estimate:
        return analysis.mouse_position_estimate

    return []
