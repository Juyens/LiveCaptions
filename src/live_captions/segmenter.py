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
    min_speech_s: float = 0.3  # menos voz que esto se descarta (clicks, respiraciones)
    pad_s: float = 0.15  # margen alrededor de la voz para no comer consonantes
    hop_s: float = 0.2  # cada cuanto audio nuevo se vuelve a evaluar
    _buffer: np.ndarray = field(default_factory=_empty)
    _since_eval: int = 0
    # Tramos de la ultima evaluacion; `pending` los reutiliza en vez de volver a pasar el VAD.
    _regions: list[tuple[int, int]] = field(default_factory=list)

    def feed(self, chunk: np.ndarray) -> list[np.ndarray]:
        """Anade audio y devuelve las frases que hayan quedado cerradas.

        Un bloque largo (audio acumulado mientras el hilo estaba ocupado) se evalua a trozos de
        `hop_s`, como si hubiera llegado a tiempo: evaluado de una vez, una pausa en mitad del
        bloque quedaria tapada por la voz que viene detras y la frase no se cerraria.
        """
        chunk = chunk.astype(np.float32, copy=False)
        hop = int(self.hop_s * RATE)
        closed: list[np.ndarray] = []
        while chunk.size:
            take = min(chunk.size, hop - self._since_eval)
            self._buffer = np.concatenate([self._buffer, chunk[:take]])
            self._since_eval += take
            chunk = chunk[take:]
            if self._since_eval >= hop:
                self._since_eval = 0
                closed.extend(self._evaluate())
        return closed

    def pending(self) -> np.ndarray | None:
        """Audio con voz aun sin cerrar (hasta la ultima evaluacion), para los borradores."""
        if not self._regions:
            return None
        return self._slice(self._regions[0][0], self._regions[-1][1])

    def flush(self) -> np.ndarray | None:
        """Cierra lo que quede (al parar la captura)."""
        regions = self.speech_regions(self._buffer)
        segment = None
        if regions and self._voiced(regions) >= self.min_speech_s * RATE:
            segment = self._slice(regions[0][0], regions[-1][1])
        self._buffer = _empty()
        self._since_eval = 0
        self._regions = []
        return segment

    @property
    def _pad(self) -> int:
        return int(self.pad_s * RATE)

    @staticmethod
    def _voiced(regions: list[tuple[int, int]]) -> int:
        return sum(end - start for start, end in regions)

    def _slice(self, start: int, end: int) -> np.ndarray:
        return self._buffer[max(0, start - self._pad) : min(self._buffer.size, end + self._pad)]

    def _evaluate(self) -> list[np.ndarray]:
        regions = self._regions = self.speech_regions(self._buffer)
        if not regions:
            # Solo silencio: conservar una cola corta por si la voz empieza justo al final.
            keep = int(self.silence_s * RATE)
            if self._buffer.size > keep:
                self._buffer = self._buffer[-keep:]
            return []

        first_start, last_end = regions[0][0], regions[-1][1]
        trailing_silence = self._buffer.size - last_end
        if trailing_silence >= self.silence_s * RATE:
            return self._emit(regions)

        if self._buffer.size - first_start >= self.max_segment_s * RATE:
            # Demasiado largo: cortar en la ultima pausa interna si la hay, si no, en seco.
            return self._emit(regions[:-1] if len(regions) > 1 else regions)
        return []

    def _emit(self, regions: list[tuple[int, int]]) -> list[np.ndarray]:
        end = regions[-1][1]
        segment = self._slice(regions[0][0], end)
        # Se corta justo donde acaba la voz, sin margen: si el buffer siguiente empezara con la
        # cola de esta frase, el VAD la veria como voz nueva y Whisper inventaria una "frase"
        # de 0.3 s ("you", "money"...). El margen de la siguiente sale del silencio posterior.
        self._buffer = self._buffer[end:]
        self._regions = []
        return [segment] if self._voiced(regions) >= self.min_speech_s * RATE else []
