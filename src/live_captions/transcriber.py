"""Whisper large-v3-turbo sobre CTranslate2, con filtro de alucinaciones."""

from __future__ import annotations

import logging
import re

import numpy as np
from faster_whisper import WhisperModel

log = logging.getLogger(__name__)

MODEL = "deepdml/faster-whisper-large-v3-turbo-ct2"

# Frases que Whisper inventa sobre silencio o ruido de fondo (creditos de YouTube, etc.).
_HALLUCINATIONS = re.compile(
    r"^(thank(s| you)( (very|so) much)?( (for|so much for) (watching|listening))?|"
    r"(please )?(like|subscribe)[^.]*|see you( in the next (video|one))?|"
    r"bye(-bye)?|you|the end|\.+)[.!\s]*$",
    re.IGNORECASE,
)


class Transcriber:
    """Mantiene el modelo cargado y transcribe bloques de audio a 16 kHz."""

    def __init__(self, device: str, compute_type: str) -> None:
        log.info("Cargando %s en %s/%s", MODEL, device, compute_type)
        self._model = WhisperModel(MODEL, device=device, compute_type=compute_type)

    def transcribe(self, audio: np.ndarray, *, draft: bool = False) -> str:
        """Texto en ingles del bloque. `draft` sacrifica precision por velocidad."""
        segments, _ = self._model.transcribe(
            audio,
            language="en",
            beam_size=1 if draft else 5,
            condition_on_previous_text=False,
            vad_filter=False,
            without_timestamps=True,
        )
        parts: list[str] = []
        for segment in segments:
            text = segment.text.strip()
            if not text:
                continue
            if segment.no_speech_prob > 0.6 and segment.avg_logprob < -0.8:
                log.debug("Descartado por no_speech: %r", text)
                continue
            if _HALLUCINATIONS.match(text):
                log.debug("Descartado por alucinacion: %r", text)
                continue
            parts.append(text)
        return " ".join(parts)

    def warm_up(self) -> None:
        """Primera pasada sobre silencio para compilar kernels y reservar VRAM."""
        self.transcribe(np.zeros(16_000, dtype=np.float32), draft=True)
