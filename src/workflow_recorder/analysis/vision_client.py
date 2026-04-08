"""OpenAI GPT-4o vision API client for screenshot analysis."""

from __future__ import annotations

import base64
import time
from pathlib import Path

import structlog
from openai import OpenAI, RateLimitError, APITimeoutError, APIConnectionError

from workflow_recorder.analysis.frame_analysis import FrameAnalysis, UIElement
from workflow_recorder.analysis.prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from workflow_recorder.capture.window_info import WindowContext
from workflow_recorder.config import AnalysisConfig
from workflow_recorder.utils.retry import retry

log = structlog.get_logger()


class VisionClient:
    """Sends screenshots to GPT-4o vision for analysis."""

    def __init__(self, config: AnalysisConfig):
        self.config = config
        self.client = OpenAI(api_key=config.openai_api_key)
        self._last_call_time = 0.0
        self._min_interval = 60.0 / config.rate_limit_rpm if config.rate_limit_rpm > 0 else 0

    def analyze_frame(
        self,
        image_path: Path,
        window_context: WindowContext | None,
        frame_index: int = 0,
        timestamp: float | None = None,
    ) -> FrameAnalysis | None:
        """Analyze a screenshot and return structured frame analysis."""
        self._rate_limit()
        return self._call_api(image_path, window_context, frame_index, timestamp)

    def _rate_limit(self) -> None:
        """Enforce rate limiting between API calls."""
        if self._min_interval <= 0:
            return
        elapsed = time.time() - self._last_call_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

    @retry(
        max_attempts=3,
        backoff_base=2.0,
        retryable_exceptions=(RateLimitError, APITimeoutError, APIConnectionError),
    )
    def _call_api(
        self,
        image_path: Path,
        window_context: WindowContext | None,
        frame_index: int,
        timestamp: float | None,
    ) -> FrameAnalysis | None:
        # Encode image
        with open(image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")

        # Determine mime type
        suffix = image_path.suffix.lower()
        mime = "image/png" if suffix == ".png" else "image/jpeg"

        # Build user prompt
        from PIL import Image
        with Image.open(image_path) as img:
            width, height = img.size

        if window_context:
            user_text = USER_PROMPT_TEMPLATE.format(
                process_name=window_context.process_name,
                window_title=window_context.window_title,
                window_rect=window_context.window_rect,
                is_maximized=window_context.is_maximized,
                width=width,
                height=height,
            )
        else:
            user_text = f"Analyze this screenshot. Screen resolution: {width}x{height}. What is the user doing?"

        self._last_call_time = time.time()
        response = self.client.chat.completions.create(
            model=self.config.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime};base64,{image_data}",
                                "detail": self.config.detail,
                            },
                        },
                    ],
                },
            ],
            response_format={"type": "json_object"},
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
        )

        content = response.choices[0].message.content
        if not content:
            log.warning("empty_response", frame_index=frame_index)
            return None

        import json
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            log.warning("invalid_json_response", frame_index=frame_index,
                        content=content[:200])
            return None

        # Parse into FrameAnalysis
        ui_elements = []
        for elem in data.get("ui_elements_visible", []):
            if isinstance(elem, dict):
                ui_elements.append(UIElement(
                    name=elem.get("name", ""),
                    element_type=elem.get("element_type", ""),
                    coordinates=elem.get("coordinates", []),
                ))

        return FrameAnalysis(
            frame_index=frame_index,
            timestamp=timestamp or time.time(),
            application=data.get("application", ""),
            window_title=data.get("window_title", ""),
            user_action=data.get("user_action", ""),
            ui_elements_visible=ui_elements,
            text_content=data.get("text_content", ""),
            mouse_position_estimate=data.get("mouse_position_estimate", []),
            confidence=float(data.get("confidence", 0.0)),
        )
