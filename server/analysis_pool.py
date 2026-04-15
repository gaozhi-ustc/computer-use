"""AnalysisPool — per-API-key worker threads for group-level frame analysis.

Each worker thread:
1. Claims the next pending frame_group (atomic UPDATE...RETURNING)
2. Loads all frame images + metadata for the group
3. Calls the vision API with multi-image prompt
4. Parses response into SOP steps
5. Writes steps to DB, marks group as done
6. When all groups for a session are done, auto-creates SOP
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from server import db

log = structlog.get_logger()

EMPTY_QUEUE_POLL_INTERVAL_SECONDS = 2.0
MAX_ANALYSIS_ATTEMPTS = 3


class AnalysisWorker:
    """One worker per API key — processes frame groups."""

    def __init__(self, key: str, key_index: int, stop_event: threading.Event,
                 vision_client: Any = None):
        self.key = key
        self.label = f"worker-{key_index}"
        self._stop = stop_event
        self._client = vision_client

    def _build_client(self):
        """Build OpenAI client for multi-image calls."""
        from openai import OpenAI
        return OpenAI(
            api_key=self.key,
            base_url="https://coding.dashscope.aliyuncs.com/v1",
        )

    def run(self) -> None:
        log.info("analysis_worker_started", label=self.label)
        client = self._client or self._build_client()

        while not self._stop.is_set():
            group = db.claim_next_pending_group()
            if group is None:
                self._stop.wait(timeout=EMPTY_QUEUE_POLL_INTERVAL_SECONDS)
                continue
            try:
                self._analyze_group(client, group)
            except Exception as exc:
                log.exception("group_analysis_error",
                              group_id=group["id"], label=self.label)
                self._handle_failure(group["id"], group["analysis_attempts"],
                                     str(exc))

        log.info("analysis_worker_stopped", label=self.label)

    def _analyze_group(self, client: Any, group: dict) -> None:
        from server.group_analysis import (
            GROUP_SYSTEM_PROMPT, build_user_prompt,
            build_image_content_blocks, parse_steps_response,
        )

        group_id = group["id"]
        session_id = group["session_id"]
        frame_ids = group["frame_ids"]

        # Load frames ordered by frame_index
        frames = []
        for fid in frame_ids:
            f = db.get_frame(fid)
            if f:
                frames.append(f)
        frames.sort(key=lambda f: f.get("frame_index", 0))

        if not frames:
            db.mark_group_failed(group_id, "no frames found")
            return

        # Build multi-image prompt
        user_text = build_user_prompt(frames)
        image_blocks = build_image_content_blocks(frames)

        if not image_blocks:
            db.mark_group_failed(group_id, "no valid images")
            return

        content: list[dict] = [{"type": "text", "text": user_text}] + image_blocks

        model = os.environ.get("ANALYSIS_MODEL", "qwen3.6-plus")
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": GROUP_SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            max_tokens=4000,
            temperature=0.1,
        )

        raw_text = response.choices[0].message.content or ""
        steps = parse_steps_response(raw_text)

        if not steps:
            db.mark_group_failed(group_id, "empty steps from LLM")
            return

        db.mark_group_done(group_id)
        _store_group_steps(session_id, group["group_index"], steps)

        if db.all_groups_done(session_id):
            _auto_create_sop(session_id, group["employee_id"])

    def _handle_failure(self, group_id: int, attempts: int, reason: str) -> None:
        if attempts >= MAX_ANALYSIS_ATTEMPTS:
            db.mark_group_failed(group_id, reason)
            log.warning("group_analysis_permanently_failed",
                        group_id=group_id, reason=reason)
        else:
            db.reset_group_to_pending(group_id)
            log.info("group_analysis_retry",
                     group_id=group_id, attempts=attempts)


def _store_group_steps(session_id: str, group_index: int,
                       steps: list[dict]) -> None:
    """Persist group analysis steps for later SOP assembly."""
    db.store_group_analysis_result(session_id, group_index, steps)


def _auto_create_sop(session_id: str, employee_id: str) -> None:
    """Create a draft SOP from all completed group analyses."""
    log.info("auto_creating_sop", session_id=session_id)
    db.update_session_status(session_id, "analyzed")

    groups = db.list_frame_groups(session_id)
    all_steps: list[dict] = []
    group_ids: list[int] = []

    for g in groups:
        group_ids.append(g["id"])
        result = db.get_group_analysis_result(session_id, g["group_index"])
        if result:
            all_steps.extend(result)

    for i, step in enumerate(all_steps):
        step["step_order"] = i + 1

    sop_id = db.insert_sop(
        title=f"SOP - {employee_id} / {session_id[:8]}",
        created_by="system",
    )
    db.update_sop_group_ids(sop_id, group_ids)

    for step in all_steps:
        db.insert_sop_step(
            sop_id=sop_id,
            step_order=step.get("step_order", 0),
            title=step.get("title", ""),
            description=step.get("human_description", ""),
            application=step.get("application", ""),
            action_type=step.get("machine_actions", [{}])[0].get("type", "")
                if step.get("machine_actions") else "",
            action_detail=step.get("machine_actions", []),
            source_frame_ids=step.get("key_frame_indices", []),
            confidence=0.0,
            human_description=step.get("human_description", ""),
            machine_actions=step.get("machine_actions", []),
        )

    log.info("sop_auto_created", sop_id=sop_id, step_count=len(all_steps))


class AnalysisPool:
    """Manages a pool of analysis worker threads, one per API key."""

    def __init__(self, keys: list[str], worker_factory=None):
        self._keys = keys
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._worker_factory = worker_factory

    def start(self) -> None:
        if not self._keys:
            log.warning("analysis_pool_no_keys",
                        msg="api_keys.txt empty/missing — uploaded frames "
                            "will sit in 'pending' forever.")
            return

        for i, key in enumerate(self._keys):
            if self._worker_factory:
                worker = self._worker_factory(key, i, self._stop)
            else:
                worker = AnalysisWorker(key, i, self._stop)
            t = threading.Thread(target=worker.run, daemon=True,
                                 name=f"analysis-worker-{i}")
            t.start()
            self._threads.append(t)
        log.info("analysis_pool_started", worker_count=len(self._threads))

    def stop(self, timeout: float = 30.0) -> None:
        if not self._threads:
            return
        self._stop.set()
        per_thread = max(1.0, timeout / len(self._threads))
        for t in self._threads:
            t.join(timeout=per_thread)
        log.info("analysis_pool_stopped", worker_count=len(self._threads))
