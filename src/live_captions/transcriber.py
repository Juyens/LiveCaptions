"""Whisper large-v3-turbo sobre CTranslate2, con filtro de alucinaciones.

Dos caminos:
- `transcribe`: frases cerradas. Ventana completa de 30 s, beam 5, y como contexto la frase
  anterior y el vocabulario del usuario. Prima la precision (~0.3 s en una RTX 4060).
- `draft`: la frase en curso, varias veces por segundo. El encoder de Whisper siempre procesa
  30 s aunque la frase dure 2, y es casi todo el coste; aqui se le pasa solo la ventana que
  hace falta (10 s como minimo, por debajo empieza a repetirse en bucle), lo que baja la pasada
  de ~270 ms a ~65 ms. Algo menos preciso, pero la frase final lo corrige.
"""

from __future__ import annotations

import logging
import re
import zlib

import ctranslate2
import numpy as np
from faster_whisper import WhisperModel
from faster_whisper.tokenizer import Tokenizer

log = logging.getLogger(__name__)

MODEL = "deepdml/faster-whisper-large-v3-turbo-ct2"

RATE = 16_000
_HOP = 160  # muestras por frame del espectrograma
_DRAFT_MIN_FRAMES = 1000  # 10 s
_DRAFT_STEP_FRAMES = 200  # la ventana crece de 2 en 2 s
_FULL_FRAMES = 3000  # 30 s, la ventana de entrenamiento

# Frases que Whisper inventa sobre silencio o ruido de fondo (creditos de YouTube, etc.).
_HALLUCINATIONS = re.compile(
    r"^(thank(s| you)( (very|so) much)?( (for|so much for) (watching|listening))?|"
    r"(please )?(like|subscribe)[^.]*|see you( in the next (video|one))?|"
    r"bye(-bye)?|you|the end|\.+)[.!\s]*$",
    re.IGNORECASE,
)


def _looks_invented(text: str) -> bool:
    return bool(_HALLUCINATIONS.match(text))


def _repetitive(text: str) -> bool:
    """Bucles tipo "the. the. the...": el texto se comprime demasiado bien."""
    data = text.encode()
    return len(data) > 24 and len(data) / len(zlib.compress(data)) > 2.4


class Transcriber:
    """Mantiene el modelo cargado y transcribe bloques de audio a 16 kHz."""

    def __init__(self, device: str, compute_type: str) -> None:
        log.info("Cargando %s en %s/%s", MODEL, device, compute_type)
        self._model = WhisperModel(MODEL, device=device, compute_type=compute_type)
        self._tokenizer = Tokenizer(
            self._model.hf_tokenizer,
            self._model.model.is_multilingual,
            task="transcribe",
            language="en",
        )
        self._hotwords = ""
        self._draft_prompt = self._build_draft_prompt()

    def set_hotwords(self, hotwords: str) -> None:
        """Nombres y terminos que suelen salir (se le dan a Whisper como pista)."""
        self._hotwords = " ".join(hotwords.split())
        self._draft_prompt = self._build_draft_prompt()

    def transcribe(self, audio: np.ndarray, *, context: str = "") -> str:
        """Texto en ingles de una frase cerrada; `context` es lo dicho justo antes."""
        segments, _ = self._model.transcribe(
            audio,
            language="en",
            beam_size=5,
            condition_on_previous_text=False,
            initial_prompt=self._prompt(context) or None,
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
            if _looks_invented(text):
                log.debug("Descartado por alucinacion: %r", text)
                continue
            parts.append(text)
        return " ".join(parts)

    def draft(self, audio: np.ndarray) -> str:
        """Transcripcion rapida y aproximada de la frase en curso ("" si no es fiable)."""
        features = self._model.feature_extractor(audio)
        frames = features.shape[-1]
        window = -(-(frames + 100) // _DRAFT_STEP_FRAMES) * _DRAFT_STEP_FRAMES
        window = min(_FULL_FRAMES, max(_DRAFT_MIN_FRAMES, window))
        if frames >= window:
            features = features[:, -window:]
        else:
            features = np.pad(features, ((0, 0), (0, window - frames)))
        encoded = self._model.model.encode(
            ctranslate2.StorageView.from_array(np.ascontiguousarray(features[None])),
            to_cpu=False,
        )
        # Tope de tokens proporcional al audio: corta de raiz los bucles de repeticion.
        budget = int(audio.size / RATE * 6) + 10
        result = self._model.model.generate(
            encoded,
            [self._draft_prompt],
            beam_size=1,
            max_length=len(self._draft_prompt) + budget,
            return_no_speech_prob=True,
        )[0]
        if result.no_speech_prob > 0.6:
            return ""
        text = self._tokenizer.decode(result.sequences_ids[0]).strip()
        if _looks_invented(text) or _repetitive(text):
            log.debug("Borrador descartado: %r", text)
            return ""
        return text

    def warm_up(self) -> None:
        """Primera pasada sobre silencio para compilar kernels y reservar VRAM."""
        silence = np.zeros(RATE, dtype=np.float32)
        self.transcribe(silence)
        self.draft(silence)

    def _prompt(self, context: str) -> str:
        """Texto previo para Whisper: el vocabulario como frase y lo dicho justo antes.

        Whisper imita el estilo del texto previo; una lista suelta de palabras le quita el punto
        final a las frases, asi que el vocabulario va como una frase normal, con su punto.
        """
        glossary = f"{self._hotwords.rstrip('.')}." if self._hotwords else ""
        return f"{glossary} {context[-300:]}".strip()

    def _build_draft_prompt(self) -> list[int]:
        prompt: list[int] = []
        if self._hotwords:
            prompt.append(self._tokenizer.sot_prev)
            prompt.extend(self._tokenizer.encode(" " + self._prompt(""))[:200])
        prompt.extend(self._tokenizer.sot_sequence)
        prompt.append(self._tokenizer.no_timestamps)
        return prompt
