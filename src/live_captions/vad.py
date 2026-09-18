"""Detector de voz Silero, el mismo que trae faster-whisper."""

from __future__ import annotations

import numpy as np
from faster_whisper.vad import VadOptions, get_speech_timestamps

_OPTIONS = VadOptions(
    threshold=0.5,
    min_speech_duration_ms=100,
    # Pausas mas cortas que esto se consideran parte de la misma frase.
    min_silence_duration_ms=300,
    speech_pad_ms=0,
)


def speech_regions(audio: np.ndarray) -> list[tuple[int, int]]:
    """Tramos con voz, en muestras a 16 kHz."""
    if audio.size < 512:
        return []
    return [(int(t["start"]), int(t["end"])) for t in get_speech_timestamps(audio, _OPTIONS)]


def warm_up() -> None:
    """Carga el modelo ONNX antes de la primera frase real."""
    get_speech_timestamps(np.zeros(16_000, dtype=np.float32), _OPTIONS)
