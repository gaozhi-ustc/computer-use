"""SessionFinalizer — daemon thread that detects idle sessions and triggers grouping.

Polls the sessions table for sessions in 'active' status whose last_frame_at
is older than SESSION_IDLE_TIMEOUT_SECONDS. For each such session:
1. Set status -> 'finalizing'
2. Load all frames, run FrameGrouper
3. Insert frame_groups records
4. Set status -> 'grouped'
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone, timedelta

import structlog

from server import db
from server.frame_grouper import group_frames

log = structlog.get_logger()

SESSION_IDLE_TIMEOUT_SECONDS = int(os.environ.get("SESSION_IDLE_TIMEOUT", "300"))
FINALIZER_POLL_INTERVAL_SECONDS = int(os.environ.get("FINALIZER_POLL_INTERVAL", "60"))


class SessionFinalizer:
    """Background thread that finalizes idle recording sessions."""

    def __init__(
        self,
        stop_event: threading.Event,
        idle_timeout: int = SESSION_IDLE_TIMEOUT_SECONDS,
        poll_interval: float = FINALIZER_POLL_INTERVAL_SECONDS,
    ):
        self._stop = stop_event
        self._idle_timeout = idle_timeout
        self._poll_interval = poll_interval

    def run(self) -> None:
        log.info("session_finalizer_started",
                 idle_timeout=self._idle_timeout,
                 poll_interval=self._poll_interval)
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception:
                log.exception("session_finalizer_error")
            self._stop.wait(timeout=self._poll_interval)
        log.info("session_finalizer_stopped")

    def _poll_once(self) -> None:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=self._idle_timeout)
        ).isoformat(timespec="seconds")
        idle_sessions = db.list_idle_sessions(cutoff_iso=cutoff)

        for sess in idle_sessions:
            session_id = sess["session_id"]
            try:
                self._finalize_session(sess)
            except Exception:
                log.exception("session_finalize_failed", session_id=session_id)
                db.update_session_status(session_id, "failed")

    def _finalize_session(self, sess: dict) -> None:
        session_id = sess["session_id"]
        employee_id = sess["employee_id"]
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")

        log.info("session_finalizing", session_id=session_id)
        db.update_session_status(session_id, "finalizing", finalized_at=now)

        # Load all frames for this session
        frames = db.query_frames(session_id=session_id, limit=100_000)
        if not frames:
            db.update_session_status(session_id, "failed")
            return

        # Sort by frame_index ASC (query_frames returns DESC)
        frames.sort(key=lambda f: f.get("frame_index", 0))

        # Run grouper
        groups = group_frames(frames, use_phash=True)

        # Insert frame_groups
        for g in groups:
            db.insert_frame_group(
                session_id=session_id,
                employee_id=employee_id,
                group_index=g.group_index,
                frame_ids=g.frame_ids,
                primary_application=g.primary_application,
            )

        db.update_session_status(session_id, "grouped")
        log.info("session_grouped", session_id=session_id, group_count=len(groups))
