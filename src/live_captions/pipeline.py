"""Orquesta captura -> troceado -> Whisper -> traduccion -> disco, y avisa a la UI por senales.

Hilos:
- PortAudio entrega audio (altavoces y microfono) a una cola, etiquetado por fuente.
- `_work` consume la cola, corta frases por fuente y es el unico que toca Whisper (evita
  pelear por la GPU). Las frases cerradas van primero; entre medias, cada ~0.3 s, un borrador
  rapido de la frase en curso que `Agreement` estabiliza palabra a palabra.
- `_translate` traduce las frases finales de los demas y, si no hay ninguna esperando, el
  ultimo texto confirmado del borrador. Lo que dice el propio usuario no se traduce.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, Signal

from live_captions import vad
from live_captions.agreement import Agreement
from live_captions.audio import CHUNK_SECONDS, AudioCallback, Capture, Source
from live_captions.cuda import enable_cuda_dlls
from live_captions.segmenter import RATE, Segmenter
from live_captions.storage import Session, Speaker
from live_captions.transcriber import Transcriber
from live_captions.translator import Translator

log = logging.getLogger(__name__)

DRAFT_EVERY_S = 0.3  # cadencia maxima de borradores
DRAFT_MIN_S = 0.5  # voz minima antes del primer borrador
# Si hay mas audio que esto esperando en la cola, se salta el borrador para no ir por detras.
MAX_BACKLOG_S = 0.3
CONTEXT_CHARS = 300  # lo dicho justo antes, que se le da a Whisper como contexto

# Elemento de la cola de traduccion: frase final (indice, segundos, ingles), el ultimo texto
# confirmado de un borrador ("draft", indice de su frase, ingles) o None para terminar.
_Final = tuple[int, float, str]
_Draft = tuple[str, int, str]


def detect_backend() -> tuple[str, str]:
    """(device, compute_type) para CTranslate2; GPU si hay una utilizable."""
    enable_cuda_dlls()
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16"
    except Exception:
        log.warning("No se detecto GPU utilizable, se usara la CPU", exc_info=True)
    return "cpu", "int8"


class Pipeline(QObject):
    status = Signal(str)
    ready = Signal(str)  # backend, p. ej. "cuda/float16"
    level = Signal(float)  # nivel RMS 0..1 del audio de los altavoces
    # Frase en curso de los demas: (confirmado, provisional); ("", "") la borra.
    partial = Signal(str, str)
    final = Signal(int, float, str, str)  # indice, segundos, hablante ("them"/"me"), ingles
    translated = Signal(int, float, str)  # indice, segundos, espanol
    # Traduccion del borrador: indice que tendra su frase final, espanol.
    partial_translated = Signal(int, str)
    started = Signal(str, str)  # nombre del dispositivo de salida, ruta del archivo .en.md
    stopped = Signal()
    failed = Signal(str)
    warning = Signal(str)  # problemas no fatales (p. ej. sin microfono)

    def __init__(
        self,
        transcripts_dir: Path,
        parent: QObject | None = None,
        capture: Callable[[Source, AudioCallback], Capture] = Capture,
    ) -> None:
        super().__init__(parent)
        self._transcripts_dir = transcripts_dir
        self._audio: queue.Queue[tuple[Speaker, np.ndarray]] = queue.Queue()
        self._pending: queue.Queue[_Final | _Draft | None] = queue.Queue()
        self._loopback = capture("loopback", lambda chunk: self._audio.put(("them", chunk)))
        self._mic = capture("mic", lambda chunk: self._audio.put(("me", chunk)))
        self._mic_enabled = True
        self._mic_open = False
        self._hotwords = ""
        self._transcriber: Transcriber | None = None
        self._translator: Translator | None = None
        self._session: Session | None = None
        self._running = threading.Event()
        self._worker: threading.Thread | None = None
        self._translating: threading.Thread | None = None
        self._t0 = 0.0
        self._index = 0

    # -- ciclo de vida -----------------------------------------------------------------

    @property
    def is_ready(self) -> bool:
        return self._transcriber is not None and self._translator is not None

    @property
    def is_running(self) -> bool:
        return self._running.is_set()

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._t0 if self.is_running else 0.0

    def load_models(self) -> None:
        """Descarga y carga los modelos en segundo plano; emite `ready` o `failed`."""
        threading.Thread(target=self._load, name="load-models", daemon=True).start()

    def _load(self) -> None:
        try:
            device, compute_type = detect_backend()
            self.status.emit("Cargando Whisper large-v3-turbo (1.6 GB la primera vez)...")
            transcriber = Transcriber(device, compute_type)
            transcriber.set_hotwords(self._hotwords)
            self._transcriber = transcriber
            self.status.emit("Cargando traductor...")
            self._translator = Translator(device)
            self.status.emit("Calentando modelos...")
            vad.warm_up()
            self._transcriber.warm_up()
            self._translator.warm_up()
        except Exception as exc:
            log.exception("No se pudieron cargar los modelos")
            self.failed.emit(f"No se pudieron cargar los modelos: {exc}")
            return
        self.ready.emit(f"{device}/{compute_type}")

    def set_hotwords(self, hotwords: str) -> None:
        """Nombres y terminos que Whisper debe esperar; vale antes o despues de cargar."""
        self._hotwords = hotwords
        if self._transcriber is not None:
            self._transcriber.set_hotwords(hotwords)

    def set_mic_enabled(self, enabled: bool) -> None:
        """Enciende o apaga el microfono, tambien en mitad de una sesion."""
        self._mic_enabled = enabled
        if not self.is_running:
            return
        if enabled and not self._mic_open:
            self._open_mic()
        elif not enabled and self._mic_open:
            self._mic.stop()
            self._mic_open = False

    def _open_mic(self) -> None:
        try:
            self._mic.start()
            self._mic_open = True
        except Exception as exc:
            log.warning("Sin microfono: %s", exc)
            self.warning.emit(f"Microfono no disponible: {exc}")

    def start(self) -> None:
        if not self.is_ready or self.is_running or self._worker is not None:
            return
        try:
            self._session = Session(self._transcripts_dir)
            device = self._loopback.start()
        except Exception as exc:
            log.exception("No se pudo iniciar la captura")
            self.failed.emit(f"No se pudo capturar el audio: {exc}")
            return
        self._t0 = time.monotonic()
        self._index = 0
        self._running.set()
        if self._mic_enabled:
            self._open_mic()
        self._worker = threading.Thread(target=self._work, name="transcribe", daemon=True)
        self._translating = threading.Thread(target=self._translate, name="translate", daemon=True)
        self._worker.start()
        self._translating.start()
        self.started.emit(device.name, str(self._session.english_path))

    def stop(self) -> None:
        if not self.is_running:
            return
        self._running.clear()
        self._loopback.stop()
        if self._mic_open:
            self._mic.stop()
            self._mic_open = False
        threading.Thread(target=self._finish, name="finish", daemon=True).start()

    def _finish(self) -> None:
        if self._worker is not None:
            self._worker.join()
        if self._translating is not None:
            self._translating.join()
        if self._session is not None:
            self._session.close()
        self._worker = self._translating = self._session = None
        self.stopped.emit()

    def log_assistant(self, text: str) -> None:
        """Deja constancia en el archivo de la sesion de lo que hizo el asistente."""
        if self._session is not None:
            self._session.append_assistant(self.elapsed, text)

    # -- hilos de trabajo --------------------------------------------------------------

    def _work(self) -> None:
        assert self._transcriber is not None
        segmenters: dict[Speaker, Segmenter] = {
            "them": Segmenter(vad.speech_regions),
            "me": Segmenter(vad.speech_regions),
        }
        context: dict[Speaker, str] = {"them": "", "me": ""}
        agreement = Agreement()
        last_draft = 0.0
        drafted_size = 0
        while self._running.is_set() or not self._audio.empty():
            try:
                first = self._audio.get(timeout=0.1)
            except queue.Empty:
                continue
            batches: dict[Speaker, list[np.ndarray]] = {"them": [], "me": []}
            batches[first[0]].append(first[1])
            while not self._audio.empty():
                speaker, chunk = self._audio.get_nowait()
                batches[speaker].append(chunk)

            for speaker, chunks in batches.items():
                if not chunks:
                    continue
                data = np.concatenate(chunks)
                if speaker == "them":
                    self.level.emit(float(np.sqrt(np.mean(np.square(data)))))
                for segment in segmenters[speaker].feed(data):
                    text = self._finalize(speaker, segment, context[speaker])
                    if text:
                        context[speaker] = (context[speaker] + " " + text)[-CONTEXT_CHARS:]
                    if speaker == "them":
                        agreement.reset()
                        drafted_size = 0
                        self.partial.emit("", "")

            # Borrador de la frase en curso, solo si no hay trabajo mas urgente.
            now = time.monotonic()
            if now - last_draft < DRAFT_EVERY_S or self._backlog() > MAX_BACKLOG_S:
                continue
            pending = segmenters["them"].pending()
            if pending is None or pending.size < DRAFT_MIN_S * RATE or pending.size == drafted_size:
                continue
            last_draft = now
            drafted_size = pending.size
            before = len(agreement.committed)
            committed, tentative = agreement.update(self._transcriber.draft(pending))
            self.partial.emit(committed, tentative)
            if len(agreement.committed) > before:
                self._pending.put(("draft", self._index + 1, committed))

        for speaker, segmenter in segmenters.items():
            tail = segmenter.flush()
            if tail is not None:
                self._finalize(speaker, tail, context[speaker])
        self.partial.emit("", "")
        self._pending.put(None)

    def _backlog(self) -> float:
        """Segundos de audio (aprox.) esperando en la cola."""
        return self._audio.qsize() * CHUNK_SECONDS

    def _finalize(self, speaker: Speaker, segment: np.ndarray, context: str) -> str:
        assert self._transcriber is not None
        text = self._transcriber.transcribe(segment, context=context)
        log.debug("Frase de %s, %.1f s: %r", speaker, segment.size / RATE, text)
        if not text:
            return ""
        seconds = max(0.0, time.monotonic() - self._t0 - segment.size / RATE)
        self._index += 1
        self.final.emit(self._index, seconds, speaker, text)
        if self._session is not None:
            self._session.append_english(seconds, text, speaker)
        if speaker == "them":
            self._pending.put((self._index, seconds, text))
        return text

    def _translate(self) -> None:
        assert self._translator is not None
        done = 0  # indice de la ultima frase final traducida
        finished = False
        while not finished:
            batch = [self._pending.get()]
            while not self._pending.empty():
                batch.append(self._pending.get_nowait())
            finished = None in batch
            draft: _Draft | None = None
            for item in batch:
                if item is None:
                    continue
                if item[0] == "draft":
                    draft = item  # type: ignore[assignment]
                    continue
                index, seconds, text = item  # type: ignore[misc]
                done = max(done, index)
                try:
                    spanish = self._translator.translate(text)
                except Exception:
                    log.exception("Fallo traduciendo %r", text)
                    continue
                self.translated.emit(index, seconds, spanish)
                if self._session is not None:
                    self._session.append_spanish(seconds, spanish)
            # De los borradores solo interesa el ultimo, y solo si su frase aun no ha cerrado.
            if draft is not None and not finished and draft[1] > done:
                try:
                    self.partial_translated.emit(draft[1], self._translator.translate(draft[2]))
                except Exception:
                    log.exception("Fallo traduciendo el borrador %r", draft[2])
