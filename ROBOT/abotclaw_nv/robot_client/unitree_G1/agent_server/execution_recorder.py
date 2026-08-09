"""Records robot state during code execution.

G1 版本：只录制 state_agg 的状态快照为 JSONL（相机画面录制在 G1 上未接入，
相机侧 API 是 D455/D435i 各自独立单例，没有统一 ``camera_backend`` 抽象；
如需画面录制请另行实现一个 G1 相机聚合器再挂回这里）。
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from state import StateAggregator

logger = logging.getLogger(__name__)

_CODE_DIR = Path(__file__).resolve().parent.parent / "logs" / "code_executions"

# State capture interval (10 Hz)
_STATE_INTERVAL = 0.1


class ExecutionRecorder:
    """Records robot state during code execution.

    Spawns a daemon thread that writes ``state_agg.state`` snapshots at 10 Hz
    into ``state_log.jsonl`` under ``logs/code_executions/<execution_id>/``.
    Camera frame recording is not wired on G1 — pass ``camera_backend=None``.
    """

    def __init__(self) -> None:
        self._state_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._execution_id: Optional[str] = None
        self._state_agg: Optional[StateAggregator] = None
        self._output_dir: Optional[Path] = None
        self._state_count = 0
        self._started_at = 0.0

    # -- lifecycle -----------------------------------------------------------

    def start(
        self,
        execution_id: str,
        camera_backend: Any = None,  # kept for API compatibility; ignored on G1
        state_agg: Optional[StateAggregator] = None,
    ) -> None:
        """Start recording state for an execution.

        No-op if already recording, or if neither a state aggregator nor any
        recordable backend is provided.
        """
        if camera_backend is not None:
            logger.debug(
                "ExecutionRecorder: camera_backend provided but G1 does not "
                "record frames (ignored)"
            )

        if self._state_thread is not None and self._state_thread.is_alive():
            logger.warning("ExecutionRecorder: already recording, ignoring start()")
            return

        if state_agg is None:
            logger.debug("ExecutionRecorder: no state aggregator, skipping recording")
            return

        self._execution_id = execution_id
        self._state_agg = state_agg
        self._output_dir = _CODE_DIR / execution_id
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._state_count = 0
        self._started_at = time.time()
        self._stop_event.clear()

        self._state_thread = threading.Thread(
            target=self._state_capture_loop,
            name=f"exec-state-{execution_id}",
            daemon=True,
        )
        self._state_thread.start()

        logger.info("ExecutionRecorder: started for %s (state only)", execution_id)

    def stop(self) -> Dict[str, Any]:
        """Stop recording and write metadata.

        Returns:
            Summary dict with duration and state_samples.
            Empty dict if recorder was not running.
        """
        state_alive = self._state_thread is not None and self._state_thread.is_alive()

        if not state_alive:
            return {}

        self._stop_event.set()
        self._state_thread.join(timeout=5.0)
        self._state_thread = None

        stopped_at = time.time()
        duration = stopped_at - self._started_at
        state_samples = self._state_count

        metadata: Dict[str, Any] = {
            "execution_id": self._execution_id,
            "started_at": self._started_at,
            "stopped_at": stopped_at,
            "duration": round(duration, 2),
        }

        if state_samples > 0:
            metadata["state_log"] = "state_log.jsonl"
            metadata["state_interval"] = _STATE_INTERVAL
            metadata["state_samples"] = state_samples

        has_data = state_samples > 0

        if self._output_dir and has_data:
            meta_path = self._output_dir / "metadata.json"
            try:
                meta_path.write_text(json.dumps(metadata, indent=2))
            except Exception as e:
                logger.error("ExecutionRecorder: failed to write metadata: %s", e)
        elif self._output_dir and not has_data:
            try:
                shutil.rmtree(self._output_dir, ignore_errors=True)
            except Exception:
                pass

        logger.info(
            "ExecutionRecorder: stopped for %s (%d state samples, %.1fs)",
            self._execution_id, state_samples, duration,
        )

        self._state_agg = None
        self._execution_id = None
        self._output_dir = None

        return metadata

    # -- capture thread ------------------------------------------------------

    def _state_capture_loop(self) -> None:
        """Background thread: capture state at 10 Hz to JSONL."""
        if self._output_dir is None or self._state_agg is None:
            return
        state_path = self._output_dir / "state_log.jsonl"
        with open(state_path, "w") as f:
            while not self._stop_event.is_set():
                try:
                    state = self._state_agg.state  # thread-safe copy
                    f.write(json.dumps(state, separators=(",", ":")) + "\n")
                    f.flush()
                    self._state_count += 1
                except Exception as e:
                    logger.error("ExecutionRecorder: state capture error: %s", e)
                self._stop_event.wait(timeout=_STATE_INTERVAL)

    # -- queries -------------------------------------------------------------

    def get_recording(self, execution_id: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a past recording.

        Returns:
            Metadata dict or None if not found.
        """
        meta_path = _CODE_DIR / execution_id / "metadata.json"
        if not meta_path.exists():
            return None
        try:
            return json.loads(meta_path.read_text())
        except Exception as e:
            logger.error(
                "ExecutionRecorder: failed to read metadata for %s: %s",
                execution_id, e,
            )
            return None

    def list_recordings(self) -> List[str]:
        """List execution IDs that have recordings (newest first)."""
        if not _CODE_DIR.exists():
            return []
        dirs = [
            d for d in _CODE_DIR.iterdir()
            if d.is_dir() and (d / "metadata.json").exists()
        ]
        dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
        return [d.name for d in dirs]

    def cleanup_old_recordings(self, keep: int = 20) -> None:
        """Delete oldest recording directories beyond *keep* limit."""
        if not _CODE_DIR.exists():
            return
        dirs = [
            d for d in _CODE_DIR.iterdir()
            if d.is_dir() and (d / "metadata.json").exists()
        ]
        if len(dirs) <= keep:
            return
        dirs.sort(key=lambda d: d.stat().st_mtime)
        for old_dir in dirs[:-keep]:
            try:
                shutil.rmtree(old_dir)
                logger.info("ExecutionRecorder: cleaned up %s", old_dir.name)
            except Exception as e:
                logger.warning(
                    "ExecutionRecorder: failed to clean %s: %s",
                    old_dir.name, e,
                )
