"""Traduccion ingles -> espanol en local con opus-mt sobre CTranslate2.

Se tokeniza con SentencePiece directamente, sin `transformers`: el tokenizador Marian solo
hace `encode` con el modelo de origen y anade `</s>` al final.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import ctranslate2
import sentencepiece as spm
from huggingface_hub import snapshot_download

log = logging.getLogger(__name__)

MODEL = "michaelfeil/ct2fast-opus-mt-en-es"

# Corta en fin de frase seguido de espacio y mayuscula/numero; opus-mt rinde mejor frase a frase.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


def download() -> Path:
    return Path(snapshot_download(MODEL, allow_patterns=["*.bin", "*.json", "*.spm", "*.txt"]))


class Translator:
    """Modelo cargado una vez; `translate` es seguro de llamar desde un unico hilo."""

    def __init__(self, device: str) -> None:
        path = download()
        log.info("Cargando %s en %s", MODEL, device)
        self._source = spm.SentencePieceProcessor(model_file=str(path / "source.spm"))
        self._target = spm.SentencePieceProcessor(model_file=str(path / "target.spm"))
        self._model = ctranslate2.Translator(
            str(path),
            device=device,
            compute_type="int8_float16" if device == "cuda" else "int8",
        )

    def translate(self, text: str) -> str:
        sentences = [s for s in _SENTENCE_END.split(text.strip()) if s]
        if not sentences:
            return ""
        batch = [[*self._source.encode(s, out_type=str), "</s>"] for s in sentences]
        results = self._model.translate_batch(
            batch, beam_size=4, max_decoding_length=256, repetition_penalty=1.1
        )
        return " ".join(self._target.decode(r.hypotheses[0]) for r in results).strip()

    def warm_up(self) -> None:
        self.translate("Hello.")
