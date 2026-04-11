"""Generate a comparison report between two model workflow outputs."""

from __future__ import annotations

import statistics
from pathlib import Path
from datetime import datetime

import structlog

from workflow_recorder.output.schema import Workflow

log = structlog.get_logger()


def generate_comparison(
    label_a: str,
    workflow_a: Workflow,
    label_b: str,
    workflow_b: Workflow,
    output_dir: Path,
    session_id: str,
) -> Path:
    """Generate a markdown comparison report and return its path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"comparison_{session_id[:8]}.md"

    steps_a = workflow_a.steps
    steps_b = workflow_b.steps

    # Align steps by timestamp proximity
    pairs = _align_steps(steps_a, steps_b)

    # Compute statistics
    conf_a = [s.confidence for s in steps_a if s.confidence > 0]
    conf_b = [s.confidence for s in steps_b if s.confidence > 0]

    # Build report
    lines: list[str] = []
    lines.append(f"# Dual-Model Workflow Comparison")
    lines.append("")
    lines.append(f"**Session**: `{session_id[:8]}`")
    lines.append(f"**Generated**: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"**Duration**: {workflow_a.metadata.duration_seconds:.0f}s")
    lines.append(f"**Models**: {label_a} vs {label_b}")
    lines.append("")

    # Summary table
    lines.append("## Summary")
    lines.append("")
    lines.append(f"| Metric | {label_a} | {label_b} |")
    lines.append("|--------|--------|--------|")
    lines.append(f"| Total Steps | {len(steps_a)} | {len(steps_b)} |")
    lines.append(f"| Frames Captured | {workflow_a.metadata.total_frames_captured} | {workflow_b.metadata.total_frames_captured} |")

    if conf_a:
        lines.append(f"| Avg Confidence | {statistics.mean(conf_a):.2f} | {statistics.mean(conf_b):.2f} |")
        lines.append(f"| Min Confidence | {min(conf_a):.2f} | {min(conf_b):.2f} |")
        lines.append(f"| Median Confidence | {statistics.median(conf_a):.2f} | {statistics.median(conf_b):.2f} |")
    lines.append("")

    # Step-by-step comparison
    lines.append("## Step-by-Step Comparison")
    lines.append("")
    lines.append(f"| # | Time | {label_a} | {label_b} | Match | Conf A | Conf B |")
    lines.append("|---|------|--------|--------|-------|--------|--------|")

    matched_count = 0
    partial_count = 0
    diff_count = 0
    unique_a = 0
    unique_b = 0

    for idx, (sa, sb) in enumerate(pairs, 1):
        if sa and sb:
            match_type, _ = _compare_descriptions(sa.description, sb.description)
            if match_type == "yes":
                matched_count += 1
            elif match_type == "partial":
                partial_count += 1
            else:
                diff_count += 1

            time_str = sa.timestamp[11:19] if len(sa.timestamp) > 19 else sa.timestamp
            desc_a = _truncate(sa.description, 40)
            desc_b = _truncate(sb.description, 40)
            lines.append(
                f"| {idx} | {time_str} | {desc_a} | {desc_b} | "
                f"{match_type} | {sa.confidence:.2f} | {sb.confidence:.2f} |"
            )
        elif sa:
            unique_a += 1
            time_str = sa.timestamp[11:19] if len(sa.timestamp) > 19 else sa.timestamp
            lines.append(f"| {idx} | {time_str} | {_truncate(sa.description, 40)} | — | unique_a | {sa.confidence:.2f} | — |")
        else:
            unique_b += 1
            assert sb is not None
            time_str = sb.timestamp[11:19] if len(sb.timestamp) > 19 else sb.timestamp
            lines.append(f"| {idx} | {time_str} | — | {_truncate(sb.description, 40)} | unique_b | — | {sb.confidence:.2f} |")

    lines.append("")

    # Match statistics
    total_pairs = len(pairs)
    lines.append("## Alignment Statistics")
    lines.append("")
    lines.append(f"- **Exact matches**: {matched_count}/{total_pairs}")
    lines.append(f"- **Partial matches**: {partial_count}/{total_pairs}")
    lines.append(f"- **Different descriptions**: {diff_count}/{total_pairs}")
    lines.append(f"- **Unique to {label_a}**: {unique_a}")
    lines.append(f"- **Unique to {label_b}**: {unique_b}")
    lines.append("")

    # Action differences
    action_diffs = _collect_action_diffs(pairs, label_a, label_b)
    if action_diffs:
        lines.append("## Action Differences")
        lines.append("")
        for diff in action_diffs:
            lines.append(f"### Step {diff['step']} ({diff['time']})")
            lines.append(f"- **{label_a}**: {diff['actions_a']}")
            lines.append(f"- **{label_b}**: {diff['actions_b']}")
            lines.append("")

    # Confidence distribution
    if conf_a and conf_b:
        lines.append("## Confidence Distribution")
        lines.append("")
        lines.append(f"| Stat | {label_a} | {label_b} |")
        lines.append("|------|--------|--------|")
        lines.append(f"| Mean | {statistics.mean(conf_a):.3f} | {statistics.mean(conf_b):.3f} |")
        lines.append(f"| Median | {statistics.median(conf_a):.3f} | {statistics.median(conf_b):.3f} |")
        lines.append(f"| Std Dev | {statistics.stdev(conf_a):.3f} | {statistics.stdev(conf_b):.3f} |")
        lines.append(f"| Min | {min(conf_a):.3f} | {min(conf_b):.3f} |")
        lines.append(f"| Max | {max(conf_a):.3f} | {max(conf_b):.3f} |")
        lines.append("")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    log.info("comparison_report_generated", path=str(report_path))
    return report_path


def _align_steps(
    steps_a: list, steps_b: list,
) -> list[tuple]:
    """Align steps from two workflows by timestamp proximity.

    Returns list of (step_a | None, step_b | None) tuples.
    Uses greedy nearest-neighbor matching on timestamps.
    """
    from workflow_recorder.output.schema import WorkflowStep

    def _ts_minutes(step: WorkflowStep) -> float:
        """Parse ISO timestamp to minutes-since-start (rough)."""
        ts = step.timestamp
        if "T" in ts:
            parts = ts[11:19].split(":")
            if len(parts) == 3:
                return int(parts[0]) * 60 + int(parts[1]) + int(parts[2]) / 60.0
        return 0.0

    pairs: list[tuple] = []
    used_b: set[int] = set()

    for sa in steps_a:
        ta = _ts_minutes(sa)
        best_idx = -1
        best_dist = float("inf")
        for i, sb in enumerate(steps_b):
            if i in used_b:
                continue
            tb = _ts_minutes(sb)
            dist = abs(ta - tb)
            if dist < best_dist:
                best_dist = dist
                best_idx = i
        # Match if within 1 minute of each other
        if best_idx >= 0 and best_dist < 1.0:
            pairs.append((sa, steps_b[best_idx]))
            used_b.add(best_idx)
        else:
            pairs.append((sa, None))

    # Add unmatched steps from b
    for i, sb in enumerate(steps_b):
        if i not in used_b:
            pairs.append((None, sb))

    return pairs


def _compare_descriptions(desc_a: str, desc_b: str) -> tuple[str, float]:
    """Compare two step descriptions. Returns (match_type, similarity_ratio).

    match_type: "yes", "partial", or "no"
    """
    tokens_a = set(desc_a.lower().split())
    tokens_b = set(desc_b.lower().split())
    if not tokens_a or not tokens_b:
        return ("no", 0.0)

    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    jaccard = len(intersection) / len(union)

    if jaccard > 0.6:
        return ("yes", jaccard)
    elif jaccard > 0.25:
        return ("partial", jaccard)
    return ("no", jaccard)


def _format_actions(actions: list) -> str:
    """Format a list of actions as a concise string."""
    parts = []
    for a in actions:
        if a.type == "click":
            parts.append(f"click({a.target})")
        elif a.type == "type":
            parts.append(f'type("{a.text[:20]}")')
        elif a.type == "key":
            parts.append(f"key({a.keys})")
        elif a.type == "scroll":
            parts.append(f"scroll({a.direction})")
        else:
            parts.append(f"{a.type}()")
    return "; ".join(parts) if parts else "(none)"


def _collect_action_diffs(
    pairs: list[tuple], label_a: str, label_b: str,
) -> list[dict]:
    """Collect steps where actions differ between the two models."""
    diffs = []
    for idx, (sa, sb) in enumerate(pairs, 1):
        if not sa or not sb:
            continue
        actions_a_str = _format_actions(sa.actions)
        actions_b_str = _format_actions(sb.actions)
        if actions_a_str != actions_b_str:
            time_str = sa.timestamp[11:19] if len(sa.timestamp) > 19 else sa.timestamp
            diffs.append({
                "step": idx,
                "time": time_str,
                "actions_a": actions_a_str,
                "actions_b": actions_b_str,
            })
    return diffs


def _truncate(s: str, max_len: int) -> str:
    """Truncate string for table display."""
    if len(s) <= max_len:
        return s
    return s[:max_len - 3] + "..."
