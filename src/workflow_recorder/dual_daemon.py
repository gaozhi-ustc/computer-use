"""Dual-model daemon: runs multiple vision models on the same screenshots.

Architecture:
  [Capture Thread] ──┬──queue_gpt──> [AnalysisWorker GPT]
                     └──queue_qwen─> [AnalysisWorker Qwen]

Each worker has its own queue, VisionClient, and frame_analyses buffer.
Capture thread fans out frames to all workers.
"""

from __future__ import annotations

import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from workflow_recorder.capture.privacy import apply_masks, should_skip_frame
from workflow_recorder.capture.screenshot import CaptureResult, capture_screenshot
from workflow_recorder.capture.window_info import WindowContext, get_active_window
from workflow_recorder.config import AnalysisConfig, AppConfig
from workflow_recorder.utils.storage import get_temp_capture_dir

if TYPE_CHECKING:
    from workflow_recorder.analysis.frame_analysis import FrameAnalysis

log = structlog.get_logger()


@dataclass
class CapturedFrame:
    """A screenshot paired with its window context."""
    capture: CaptureResult
    window_context: WindowContext | None
    frame_index: int


class AnalysisWorker:
    """A single model's analysis loop with its own queue and client."""

    def __init__(
        self,
        label: str,
        analysis_config: AnalysisConfig,
        queue_size: int = 50,
        output_dir: Path | None = None,
    ):
        self.label = label
        self.config = analysis_config
        self.frame_queue: queue.Queue[CapturedFrame | None] = queue.Queue(
            maxsize=queue_size
        )
        self.frame_analyses: list[FrameAnalysis] = []
        self._stop_event = threading.Event()
        self._output_dir = output_dir
        self._save_lock = threading.Lock()

    def run(self) -> None:
        """Main analysis loop: dequeue frames and analyze with this model."""
        try:
            from workflow_recorder.analysis.vision_client import VisionClient
            client = VisionClient(self.config)
        except Exception:
            log.warning("vision_client_unavailable", model=self.label,
                        msg=f"Analysis disabled for {self.label}")
            self._drain()
            return

        model_name = self.config.model
        while True:
            try:
                frame = self.frame_queue.get(timeout=2.0)
            except queue.Empty:
                if self._stop_event.is_set():
                    break
                continue

            if frame is None:  # sentinel
                break

            try:
                analysis = client.analyze_frame(
                    image_path=frame.capture.file_path,
                    window_context=frame.window_context,
                )
                if analysis:
                    self.frame_analyses.append(analysis)
                    log.info("frame_analyzed", model=self.label,
                             frame_index=frame.frame_index,
                             action=analysis.user_action,
                             app=analysis.application,
                             confidence=analysis.confidence)
                    self._incremental_save()
            except Exception:
                log.exception("analysis_failed", model=self.label,
                              frame_index=frame.frame_index)

        log.info("analysis_worker_stopped", model=self.label,
                 total_analyzed=len(self.frame_analyses))

    def stop(self) -> None:
        """Signal this worker to stop."""
        self._stop_event.set()
        try:
            self.frame_queue.put_nowait(None)
        except queue.Full:
            pass

    def _drain(self) -> None:
        """Drain remaining items from the queue."""
        while True:
            try:
                item = self.frame_queue.get(timeout=2.0)
                if item is None:
                    break
            except queue.Empty:
                if self._stop_event.is_set():
                    break

    def _incremental_save(self) -> None:
        """Save current analyses to a JSONL file so data survives crashes."""
        if not self._output_dir:
            return
        with self._save_lock:
            out_path = self._output_dir / f"analyses_{self.label}.jsonl"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                for a in self.frame_analyses:
                    f.write(a.model_dump_json() + "\n")

    @classmethod
    def load_analyses(cls, path: Path) -> list:
        """Load previously saved analyses from a JSONL file."""
        from workflow_recorder.analysis.frame_analysis import FrameAnalysis
        analyses = []
        if not path.exists():
            return analyses
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    analyses.append(FrameAnalysis.model_validate_json(line))
        return analyses


class DualModelDaemon:
    """Orchestrates one capture thread fanning out to multiple analysis workers."""

    def __init__(
        self,
        config: AppConfig,
        model_configs: list[tuple[str, AnalysisConfig]],
    ):
        self.config = config
        self.session_id = str(uuid.uuid4())
        self.start_time = time.time()
        self._stop_event = threading.Event()
        self._capture_dir = get_temp_capture_dir()

        # Shared capture state
        self.captured_frames: list[CapturedFrame] = []
        self._frame_counter = 0
        self._capture_lock = threading.Lock()

        # One worker per model
        queue_size = config.capture.max_queue_size
        output_dir = Path(config.output.directory)
        self.workers: list[AnalysisWorker] = []
        for label, analysis_config in model_configs:
            self.workers.append(
                AnalysisWorker(label=label, analysis_config=analysis_config,
                               queue_size=queue_size, output_dir=output_dir)
            )

    @property
    def elapsed(self) -> float:
        return time.time() - self.start_time

    def run(self) -> None:
        """Run the dual-model daemon (blocking)."""
        log.info("dual_daemon_starting", models=[w.label for w in self.workers])

        capture_thread = threading.Thread(
            target=self._capture_loop, name="capture", daemon=True,
        )
        analysis_threads = []
        for worker in self.workers:
            t = threading.Thread(
                target=worker.run, name=f"analysis-{worker.label}", daemon=True,
            )
            analysis_threads.append(t)

        capture_thread.start()
        for t in analysis_threads:
            t.start()

        log.info("dual_daemon_running",
                 session_id=self.session_id,
                 interval=self.config.capture.interval_seconds,
                 models=[w.config.model for w in self.workers])

        try:
            while not self._stop_event.is_set():
                if self.elapsed >= self.config.session.max_duration_seconds:
                    log.info("max_duration_reached")
                    break
                self._stop_event.wait(timeout=1.0)
        except KeyboardInterrupt:
            log.info("keyboard_interrupt")

        self._finalize(capture_thread, analysis_threads)

    def stop(self) -> None:
        """Signal the daemon to stop."""
        self._stop_event.set()
        for worker in self.workers:
            worker.stop()

    def _capture_loop(self) -> None:
        """Periodically capture screenshots and fan out to all workers."""
        interval = self.config.capture.interval_seconds
        while not self._stop_event.is_set():
            frame = self._capture_once()
            if frame:
                for worker in self.workers:
                    try:
                        worker.frame_queue.put_nowait(frame)
                    except queue.Full:
                        log.warning("frame_dropped_for_model",
                                    frame_index=frame.frame_index,
                                    model=worker.label)
            self._stop_event.wait(timeout=interval)
        log.info("capture_loop_stopped")

    def _capture_once(self) -> CapturedFrame | None:
        """Take a single screenshot."""
        try:
            window_ctx = get_active_window()
            if should_skip_frame(window_ctx, self.config.privacy):
                return None

            result = capture_screenshot(
                output_dir=self._capture_dir,
                monitor=self.config.capture.monitor,
                image_format=self.config.capture.image_format,
                image_quality=self.config.capture.image_quality,
                downscale_factor=self.config.capture.downscale_factor,
            )

            apply_masks(result.file_path, self.config.privacy)

            with self._capture_lock:
                self._frame_counter += 1
                idx = self._frame_counter

            frame = CapturedFrame(
                capture=result,
                window_context=window_ctx,
                frame_index=idx,
            )
            self.captured_frames.append(frame)
            log.debug("frame_captured", frame_index=idx,
                      app=window_ctx.process_name if window_ctx else "none")
            return frame
        except Exception:
            log.exception("capture_failed")
            return None

    def _finalize(
        self,
        capture_thread: threading.Thread,
        analysis_threads: list[threading.Thread],
    ) -> None:
        """Stop everything and produce workflow documents per model + comparison."""
        self.stop()
        capture_thread.join(timeout=10.0)
        for t in analysis_threads:
            t.join(timeout=30.0)

        log.info("dual_finalizing",
                 session_id=self.session_id,
                 frames_captured=len(self.captured_frames))

        try:
            from workflow_recorder.aggregation.workflow_builder import WorkflowBuilder
            from workflow_recorder.output.comparison import generate_comparison
            from workflow_recorder.output.reference_store import store_reference_screenshots
            from workflow_recorder.output.writer import WorkflowWriter
        except ImportError as e:
            log.exception("import_failed", error=e)
            return

        workflows_by_label: dict[str, object] = {}
        output_dir = Path(self.config.output.directory)

        for worker in self.workers:
            # If in-memory analyses are empty, try loading from incremental save
            if not worker.frame_analyses:
                jsonl_path = output_dir / f"analyses_{worker.label}.jsonl"
                if jsonl_path.exists():
                    worker.frame_analyses = AnalysisWorker.load_analyses(jsonl_path)
                    log.info("loaded_analyses_from_file",
                             model=worker.label, count=len(worker.frame_analyses))
            if not worker.frame_analyses:
                log.warning("no_analyses_for_model", model=worker.label)
                continue

            builder = WorkflowBuilder(self.config)
            workflow = builder.build(
                session_id=self.session_id,
                start_time=self.start_time,
                frame_analyses=worker.frame_analyses,
                captured_frames=self.captured_frames,
            )

            # Write to model-specific subdirectory
            model_output_dir = output_dir / worker.label
            if self.config.output.include_reference_screenshots:
                workflow = store_reference_screenshots(
                    workflow, self.captured_frames, model_output_dir)

            # Create output config pointing to subdirectory
            from dataclasses import replace
            sub_config = self.config.output.model_copy(
                update={"directory": str(model_output_dir)}
            )
            writer = WorkflowWriter(sub_config)
            writer.write(workflow)
            log.info("workflow_written", model=worker.label,
                     path=str(model_output_dir), steps=len(workflow.steps))
            workflows_by_label[worker.label] = workflow

        # Generate comparison report if we have 2+ workflows
        if len(workflows_by_label) >= 2:
            try:
                labels = list(workflows_by_label.keys())
                report_path = generate_comparison(
                    label_a=labels[0],
                    workflow_a=workflows_by_label[labels[0]],
                    label_b=labels[1],
                    workflow_b=workflows_by_label[labels[1]],
                    output_dir=output_dir,
                    session_id=self.session_id,
                )
                log.info("comparison_report_written", path=str(report_path))
            except Exception:
                log.exception("comparison_report_failed")


def recover_and_build(config: AppConfig, session_id: str, start_time: float) -> None:
    """Recover analyses from JSONL files and build workflow documents.

    Can be called independently if the daemon was killed before finalization.
    """
    from workflow_recorder.aggregation.workflow_builder import WorkflowBuilder
    from workflow_recorder.output.comparison import generate_comparison
    from workflow_recorder.output.writer import WorkflowWriter

    output_dir = Path(config.output.directory)
    jsonl_files = list(output_dir.glob("analyses_*.jsonl"))
    if not jsonl_files:
        print("No analysis JSONL files found in", output_dir)
        return

    workflows_by_label: dict[str, object] = {}
    for jsonl_path in jsonl_files:
        label = jsonl_path.stem.replace("analyses_", "")
        analyses = AnalysisWorker.load_analyses(jsonl_path)
        if not analyses:
            print(f"[{label}] No analyses in {jsonl_path}")
            continue
        print(f"[{label}] Loaded {len(analyses)} analyses")

        builder = WorkflowBuilder(config)
        workflow = builder.build(
            session_id=session_id,
            start_time=start_time,
            frame_analyses=analyses,
            captured_frames=[],
        )

        model_output_dir = output_dir / label
        sub_config = config.output.model_copy(
            update={"directory": str(model_output_dir)}
        )
        writer = WorkflowWriter(sub_config)
        writer.write(workflow)
        print(f"[{label}] Written to {model_output_dir} ({len(workflow.steps)} steps)")
        workflows_by_label[label] = workflow

    if len(workflows_by_label) >= 2:
        labels = list(workflows_by_label.keys())
        report_path = generate_comparison(
            label_a=labels[0], workflow_a=workflows_by_label[labels[0]],
            label_b=labels[1], workflow_b=workflows_by_label[labels[1]],
            output_dir=output_dir, session_id=session_id,
        )
        print(f"Comparison report: {report_path}")
