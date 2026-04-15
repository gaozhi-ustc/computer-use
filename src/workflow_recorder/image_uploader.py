"""Background image uploader for the offline analysis architecture.

Same pattern as the old FramePusher (thread + bounded queue + JSONL buffer
on failure, with startup replay), but the payload is a multipart PNG upload
to /frames/upload instead of a JSON POST to /frames.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path
from typing import Any, Optional

import structlog


log = structlog.get_logger()


# Queue size — bounded so a stuck uploader doesn't OOM the client
DEFAULT_QUEUE_SIZE = 200


_STOP_SENTINEL = object()


def _build_client():
    """Factory for the httpx client. Indirection lets tests monkey-patch it."""
    import httpx
    return httpx.Client()


class ImageUploader:
    """Uploads captured PNGs with OS metadata to /frames/upload."""

    def __init__(
        self,
        server_url: str,
        api_key: str,
        employee_id: str,
        session_id: str,
        buffer_path: str,
        timeout: float = 10.0,
        max_retries: int = 3,
        queue_size: int = DEFAULT_QUEUE_SIZE,
    ):
        self.server_url = server_url.rstrip("/")
        self.api_key = api_key
        self.employee_id = employee_id
        self.session_id = session_id
        self.buffer_path = Path(buffer_path)
        self.timeout = timeout
        self.max_retries = max_retries

        self._queue: queue.Queue = queue.Queue(maxsize=queue_size)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Spawn the uploader thread (idempotent)."""
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="image-uploader", daemon=True
        )
        self._thread.start()
        log.info("image_uploader_started", server_url=self.server_url,
                 employee_id=self.employee_id, session_id=self.session_id)

    def stop(self, timeout: float = 15.0) -> None:
        """Drain the queue and stop. Safe to call without start.

        Gives the worker `timeout` seconds to finish its current upload
        and any queue items. If the worker is stuck (slow/hanging server)
        and join times out, the main thread flushes remaining queue items
        to the JSONL buffer so they can be replayed next session — nothing
        is lost.
        """
        if self._thread is None:
            return
        try:
            self._queue.put_nowait(_STOP_SENTINEL)
        except queue.Full:
            pass
        self._stop_event.set()
        self._thread.join(timeout=timeout)
        # Regardless of whether the worker drained cleanly, salvage any
        # items that never got a chance to be uploaded.
        flushed = self._flush_queue_to_buffer()
        if flushed:
            log.info("image_uploader_stop_flushed_to_buffer", count=flushed)
        self._thread = None

    def enqueue(
        self,
        image_path: Path,
        frame_index: int,
        timestamp: float,
        cursor_x: int = -1,
        cursor_y: int = -1,
        focus_rect: list[int] | None = None,
    ) -> None:
        """Queue one image for upload. Non-blocking; spills to buffer on full queue."""
        item = {
            "image_path": str(image_path),
            "frame_index": int(frame_index),
            "timestamp": float(timestamp),
            "cursor_x": int(cursor_x),
            "cursor_y": int(cursor_y),
            "focus_rect": focus_rect,
        }
        if self._thread is None:
            # Not started — buffer for next start
            self._append_to_buffer(item)
            return
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            log.warning("image_uploader_queue_full", frame_index=frame_index)
            self._append_to_buffer(item)

    # ------------------------------------------------------------------
    # Worker loop
    # ------------------------------------------------------------------

    def _run(self) -> None:
        client = _build_client()
        try:
            # Replay any leftover buffer from a prior session
            self._replay_buffer(client)
            # Serve live queue. Process items FIFO; exit cleanly on sentinel.
            while True:
                item = self._queue.get()
                if item is _STOP_SENTINEL:
                    break
                self._upload_item(client, item)
        finally:
            try:
                client.close()
            except Exception:
                pass
            log.info("image_uploader_stopped")

    def _flush_queue_to_buffer(self) -> int:
        """Drain any remaining in-memory queue items to the JSONL buffer
        so they can be replayed on the next session start. Returns the
        count of items flushed."""
        count = 0
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is _STOP_SENTINEL:
                continue
            try:
                self._append_to_buffer(item)
                count += 1
            except Exception as exc:
                log.warning("image_uploader_flush_failed", error=str(exc))
        return count

    def _upload_item(self, client, item: dict) -> None:
        image_path = Path(item["image_path"])
        if not image_path.exists():
            log.warning("image_uploader_image_missing", path=str(image_path))
            return

        focus_str = (
            json.dumps(item["focus_rect"])
            if item.get("focus_rect") is not None
            else ""
        )
        data = {
            "employee_id": self.employee_id,
            "session_id": self.session_id,
            "frame_index": str(item["frame_index"]),
            "timestamp": str(item["timestamp"]),
            "cursor_x": str(item.get("cursor_x", -1)),
            "cursor_y": str(item.get("cursor_y", -1)),
            "focus_rect": focus_str,
        }
        url = f"{self.server_url}/frames/upload"
        headers = {"X-API-Key": self.api_key} if self.api_key else {}

        for attempt in range(self.max_retries):
            try:
                with open(image_path, "rb") as fp:
                    files = {"image": (image_path.name, fp, "image/png")}
                    response = client.post(
                        url, data=data, files=files,
                        headers=headers, timeout=self.timeout,
                    )
                if 200 <= response.status_code < 300:
                    log.debug("image_uploaded",
                              frame_index=item["frame_index"],
                              response_id=response.json().get("id"))
                    return
                log.warning("image_upload_non_2xx",
                            status=response.status_code,
                            frame_index=item["frame_index"])
            except Exception as exc:
                log.warning("image_upload_error",
                            error=str(exc),
                            attempt=attempt + 1,
                            frame_index=item["frame_index"])
            # Exponential backoff: 1s, 2s, 4s, ...
            time.sleep(2 ** attempt)

        # All retries exhausted — write to buffer
        self._append_to_buffer(item)

    # ------------------------------------------------------------------
    # Buffer persistence
    # ------------------------------------------------------------------

    def _append_to_buffer(self, item: dict) -> None:
        """Persist a queued item to the JSONL buffer for later replay."""
        try:
            self.buffer_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.buffer_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        except OSError as exc:
            log.error("image_uploader_buffer_write_failed", error=str(exc))

    def _replay_buffer(self, client) -> None:
        """Attempt to re-upload each item in buffer. Delete file on success."""
        if not self.buffer_path.exists():
            return
        try:
            lines = self.buffer_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return

        survivors: list[dict] = []
        replayed = 0
        for raw in lines:
            raw = raw.strip()
            if not raw:
                continue
            try:
                item = json.loads(raw)
            except json.JSONDecodeError:
                continue
            image_path = Path(item.get("image_path", ""))
            if not image_path.exists():
                # Image was cleaned up — drop the entry
                continue
            # Try to upload (one attempt — we don't want to thrash on startup)
            try:
                self._upload_item(client, item)
                replayed += 1
            except Exception:
                survivors.append(item)

        # Rewrite buffer with only the ones that failed to replay
        if survivors:
            with open(self.buffer_path, "w", encoding="utf-8") as f:
                for item in survivors:
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")
        else:
            try:
                self.buffer_path.unlink()
            except OSError:
                pass
        if replayed:
            log.info("image_uploader_buffer_replayed", count=replayed)
