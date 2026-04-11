"""Background-thread HTTP pusher for per-frame analysis results.

Design:
- Producer: Daemon.analysis thread calls `pusher.enqueue(analysis)` after
  each successful FrameAnalysis. Non-blocking; drops into a local buffer
  file if the queue is full.
- Consumer: one dedicated pusher thread pulls from the queue and POSTs to
  the configured server URL. On HTTP / network failure, retries with
  exponential backoff up to `max_retries`, then persists the failed payload
  to a JSONL buffer file.
- Startup recovery: before serving the live queue, the pusher drains
  `buffer_path` (lines from a previous crashed / offline session) and
  replays them to the server. Successfully-replayed lines are removed from
  the file.

The pusher is intentionally decoupled from the analysis path so network
hiccups don't slow down the capture pipeline.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from workflow_recorder.config import ServerConfig

if TYPE_CHECKING:
    from workflow_recorder.analysis.frame_analysis import FrameAnalysis

log = structlog.get_logger()


_STOP_SENTINEL: dict = {"__stop__": True}


class FramePusher:
    """Pushes per-frame analyses to the collection server in a background thread."""

    def __init__(
        self,
        server_config: ServerConfig,
        employee_id: str,
        session_id: str,
    ):
        self.config = server_config
        self.employee_id = employee_id
        self.session_id = session_id

        self._queue: queue.Queue[dict] = queue.Queue(maxsize=server_config.queue_size)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._buffer_path = Path(server_config.buffer_path)

        # Metrics for the summary banner
        self.pushed_ok: int = 0
        self.buffered: int = 0

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Spawn the pusher thread. No-op if push is disabled in config."""
        if not self.config.enabled:
            log.info("frame_pusher_disabled")
            return
        if self._thread is not None:
            return

        self._buffer_path.parent.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(
            target=self._run, name="frame-pusher", daemon=True,
        )
        self._thread.start()
        log.info("frame_pusher_started",
                 url=self.config.url,
                 employee_id=self.employee_id,
                 session_id=self.session_id)

    def stop(self, flush_timeout: float = 15.0) -> None:
        """Signal the pusher to drain its queue and exit.

        Waits up to `flush_timeout` seconds for in-flight / queued items
        to be sent before giving up and buffering the rest.
        """
        if self._thread is None:
            return
        self._stop_event.set()
        try:
            self._queue.put_nowait(_STOP_SENTINEL)
        except queue.Full:
            pass
        self._thread.join(timeout=flush_timeout)
        log.info("frame_pusher_stopped",
                 pushed_ok=self.pushed_ok, buffered=self.buffered)

    # ------------------------------------------------------------------
    # producer API
    # ------------------------------------------------------------------

    def enqueue(self, analysis: "FrameAnalysis") -> None:
        """Queue a frame analysis for background push. Never blocks."""
        if not self.config.enabled:
            return
        payload = self._build_payload(analysis)
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            # Don't block the analysis thread — spill to disk directly.
            self._append_to_buffer(payload)
            self.buffered += 1
            log.warning("push_queue_full_spilled_to_disk",
                        frame_index=analysis.frame_index)

    def _build_payload(self, analysis: "FrameAnalysis") -> dict:
        """Convert a FrameAnalysis + session metadata into a JSON dict."""
        data = analysis.model_dump()
        data["employee_id"] = self.employee_id
        data["session_id"] = self.session_id
        return data

    # ------------------------------------------------------------------
    # consumer thread
    # ------------------------------------------------------------------

    def _run(self) -> None:
        """Main loop: drain buffer file, then serve the live queue."""
        try:
            import httpx
        except ImportError:
            log.error("httpx_not_installed", msg="Frame push disabled.")
            return

        client = httpx.Client(
            timeout=self.config.timeout_seconds,
            headers=self._headers(),
        )
        endpoint = self.config.url.rstrip("/") + "/frames"

        try:
            self._replay_buffer(client, endpoint)
            self._serve_queue(client, endpoint)
        finally:
            client.close()

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["X-API-Key"] = self.config.api_key
        return headers

    def _replay_buffer(self, client: Any, endpoint: str) -> None:
        """Re-push any frames left over from a previous crashed session."""
        if not self._buffer_path.exists():
            return

        try:
            with open(self._buffer_path, encoding="utf-8") as f:
                lines = [ln.strip() for ln in f if ln.strip()]
        except OSError:
            log.exception("buffer_read_failed", path=str(self._buffer_path))
            return

        if not lines:
            self._buffer_path.unlink(missing_ok=True)
            return

        log.info("replaying_buffer", count=len(lines), path=str(self._buffer_path))

        remaining: list[str] = []
        for line in lines:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue  # skip corrupt line
            if self._send_with_retry(client, endpoint, payload):
                self.pushed_ok += 1
            else:
                remaining.append(line)

        if remaining:
            with open(self._buffer_path, "w", encoding="utf-8") as f:
                for line in remaining:
                    f.write(line + "\n")
            log.warning("buffer_partially_replayed", remaining=len(remaining))
        else:
            self._buffer_path.unlink(missing_ok=True)
            log.info("buffer_fully_replayed")

    def _serve_queue(self, client: Any, endpoint: str) -> None:
        """Consume the live queue until stop sentinel."""
        while True:
            try:
                payload = self._queue.get(timeout=2.0)
            except queue.Empty:
                if self._stop_event.is_set():
                    break
                continue

            if payload is _STOP_SENTINEL or payload.get("__stop__"):
                break

            if self._send_with_retry(client, endpoint, payload):
                self.pushed_ok += 1
            else:
                self._append_to_buffer(payload)
                self.buffered += 1

    def _send_with_retry(self, client: Any, endpoint: str, payload: dict) -> bool:
        """POST one payload with exponential-backoff retry. Returns True on success."""
        import httpx

        for attempt in range(self.config.max_retries):
            try:
                resp = client.post(endpoint, json=payload)
                if resp.status_code < 300:
                    return True
                # Don't retry client errors (4xx) except 408/429
                if 400 <= resp.status_code < 500 and resp.status_code not in (408, 429):
                    log.warning("push_rejected",
                                status=resp.status_code, body=resp.text[:200])
                    return False
                log.warning("push_server_error",
                            attempt=attempt + 1, status=resp.status_code)
            except httpx.HTTPError as exc:
                log.warning("push_network_error",
                            attempt=attempt + 1, error=str(exc))

            # Exponential backoff between attempts: 1s, 2s, 4s...
            if attempt + 1 < self.config.max_retries:
                time.sleep(2 ** attempt)

        return False

    def _append_to_buffer(self, payload: dict) -> None:
        """Write a payload to the JSONL buffer so it can be replayed later."""
        try:
            with open(self._buffer_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except OSError:
            log.exception("buffer_write_failed", path=str(self._buffer_path))
