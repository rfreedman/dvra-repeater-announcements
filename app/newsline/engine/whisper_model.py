"""Process-wide Whisper model cache (shared by handoff inspect + slice QA).

Parallel QA uses distinct ``slot`` indices so each worker owns a model
instance (faster-whisper / CTranslate2 is not safe for concurrent
``transcribe`` on one model).
"""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from faster_whisper import WhisperModel

_whisper_model_cache: dict[tuple[str, int], WhisperModel] = {}
_cache_lock = threading.Lock()

# Cap RAM: each small.en instance is substantial. Override with env/CLI.
_DEFAULT_QA_WORKERS_CAP = 4


def qa_worker_count(requested: int | None = None) -> int:
    """
    How many parallel slice-QA Whisper workers to use.

    Priority: explicit ``requested`` → ``ARNEWSLINE_QA_WORKERS`` →
    ``min(4, cpu_count)`` (at least 1).
    """
    if requested is not None:
        return max(1, requested)
    env = os.environ.get("ARNEWSLINE_QA_WORKERS")
    if env is not None and env.strip() != "":
        return max(1, int(env))
    cpus = os.cpu_count() or 2
    return max(1, min(_DEFAULT_QA_WORKERS_CAP, cpus))


def get_whisper_model(model_name: str, *, slot: int = 0) -> WhisperModel:
    """Return a cached faster-whisper ``WhisperModel`` for ``model_name`` + slot."""
    key = (model_name, max(0, slot))
    with _cache_lock:
        if key not in _whisper_model_cache:
            from faster_whisper import WhisperModel as _WhisperModel

            _whisper_model_cache[key] = _WhisperModel(
                model_name, device="auto", compute_type="auto"
            )
        return _whisper_model_cache[key]


def warm_whisper_slots(model_name: str, workers: int) -> None:
    """Load one model per QA worker slot up front (avoids first-hit stampedes)."""
    for slot in range(max(1, workers)):
        get_whisper_model(model_name, slot=slot)
