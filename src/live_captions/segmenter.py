"""Corta el flujo de audio en frases usando un detector de voz.

Whisper trabaja sobre trozos completos, no sobre un flujo continuo. Este modulo acumula audio y
emite un segmento cuando la persona lleva un rato callada (fin de frase) o cuando el trozo se
hace demasiado largo. El detector se inyecta para que la logica sea probable sin modelos.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

RATE = 16_000

# Devuelve los tramos [inicio, fin) con voz, en muestras, dentro del audio recibido.
SpeechRegions = Callable[[np.ndarray], list[tuple[int, int]]]


def _empty() -> np.ndarray:
    return np.zeros(0, dtype=np.float32)


@dataclass
class Segmenter:
    """Maquina de estados sobre un unico buffer de audio a 16 kHz."""

    speech_regions: SpeechRegions
    silence_s: float = 0.6  # pausa que cierra una frase
    max_segment_s: float = 15.0  # tope antes de cortar aunque sigan hablando
    min_speech_s: float = 0.3  # trozos mas cortos se descartan (clicks, respiraciones)
    pad_s: float = 0.15  # margen alrededor de la voz para no comer consonantes
    hop_s: float = 0.5  # cada cuanto audio nuevo se vuelve a evaluar
    _buffer: np.ndarray = field(default_factory=_empty)
    _since_eval: int = 0

    def feed(self, chunk: np.ndarray) -> list[np.ndarray]:
        """Anade audio y devuelve las frases que hayan quedado cerradas."""
        self._buffer = np.concatenate([self._buffer, chunk.astype(np.float32, copy=False)])
        self._since_eval += chunk.size
        if self._since_eval < self.hop_s * RATE:
            return []
        self._since_eval = 0
        return self._evaluate()

    def pending(self) -> np.ndarray | None:
        """Audio con voz aun sin cerrar, para transcripciones provisionales."""
        regions = self.speech_regions(self._buffer)
        if not regions:
            return None
        return self._buffer[max(0, regions[0][0] - self._pad) :]

    def flush(self) -> np.ndarray | None:
        """Cierra lo que quede (al parar la captura)."""
        regions = self.speech_regions(self._buffer)
        segment = self._slice(regions[0][0], regions[-1][1]) if regions else None
        self._buffer = _empty()
        self._since_eval = 0
        return segment if segment is not None and self._long_enough(segment) else None

    @property
    def _pad(self) -> int:
        return int(self.pad_s * RATE)

    def _long_enough(self, segment: np.ndarray) -> bool:
        return segment.size >= self.min_speech_s * RATE

    def _slice(self, start: int, end: int) -> np.ndarray:
        return self._buffer[max(0, start - self._pad) : min(self._buffer.size, end + self._pad)]

    def _evaluate(self) -> list[np.ndarray]:
        regions = self.speech_regions(self._buffer)
        if not regions:
            # Solo silencio: conservar una cola corta por si la voz empieza justo al final.
            keep = int(self.silence_s * RATE)
            if self._buffer.size > keep:
                self._buffer = self._buffer[-keep:]
            return []

        first_start, last_end = regions[0][0], regions[-1][1]
        trailing_silence = self._buffer.size - last_end
        if trailing_silence >= self.silence_s * RATE:
            return self._emit(first_start, last_end)

        if self._buffer.size - first_start >= self.max_segment_s * RATE:
            # Demasiado largo: cortar en la ultima pausa interna si la hay, si no, en seco.
            cut = regions[-2][1] if len(regions) > 1 else last_end
            return self._emit(first_start, cut)
        return []

    def _emit(self, start: int, end: int) -> list[np.ndarray]:
        segment = self._slice(start, end)
        self._buffer = self._buffer[max(0, end - self._pad) :]
        return [segment] if self._long_enough(segment) else []
