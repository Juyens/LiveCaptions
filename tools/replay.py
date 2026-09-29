"""Reproduce un WAV por el pipeline en tiempo real y mide latencias y errores.

    uv run python tools/replay.py reunion.wav [--reference frases.txt]

El WAV (16 kHz mono, 16 bits) sustituye a la captura de los altavoces, al mismo ritmo que si
sonara. Se mide, frase a frase:
- primer borrador: desde que empieza la voz hasta que aparece texto provisional en ingles;
- final: desde que acaba la voz hasta la frase definitiva en ingles;
- espanol: desde que acaba la voz hasta la traduccion definitiva.
Con `--reference` (una frase por linea) calcula ademas el WER de las frases finales.
"""

from __future__ import annotations

import argparse
import logging
import re
import statistics
import sys
import tempfile
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from live_captions import vad
from live_captions.audio import CHUNK_SECONDS, AudioCallback, Device, Source
from live_captions.pipeline import Pipeline
from live_captions.segmenter import RATE

SENTENCE_GAP_S = 1.0  # silencio que separa frases del WAV a efectos de medir


def load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as file:
        if file.getframerate() != RATE or file.getnchannels() != 1 or file.getsampwidth() != 2:
            sys.exit("El WAV debe ser 16 kHz, mono, 16 bits")
        pcm = np.frombuffer(file.readframes(file.getnframes()), dtype=np.int16)
    return pcm.astype(np.float32) / 32768.0


def sentences(audio: np.ndarray) -> list[tuple[float, float]]:
    """(inicio, fin) en segundos de cada frase: tramos de voz unidos si la pausa es corta."""
    merged: list[list[int]] = []
    for start, end in vad.speech_regions(audio):
        if merged and start - merged[-1][1] < SENTENCE_GAP_S * RATE:
            merged[-1][1] = end
        else:
            merged.append([start, end])
    return [(start / RATE, end / RATE) for start, end in merged]


class WavCapture:
    """Hace de `Capture`: entrega el WAV en bloques de 0.1 s al ritmo del reloj."""

    audio: np.ndarray = np.zeros(0, dtype=np.float32)
    t0 = 0.0

    def __init__(self, source: Source, on_audio: AudioCallback) -> None:
        self._source = source
        self._on_audio = on_audio
        self._stop = threading.Event()

    def start(self) -> Device:
        if self._source != "loopback":
            raise RuntimeError("sin microfono en la reproduccion")
        threading.Thread(target=self._play, daemon=True).start()
        return Device(-1, "WAV", RATE, 1)

    def stop(self) -> None:
        self._stop.set()

    def _play(self) -> None:
        step = int(CHUNK_SECONDS * RATE)
        WavCapture.t0 = time.monotonic()
        for i, start in enumerate(range(0, self.audio.size, step)):
            if self._stop.is_set():
                return
            delay = WavCapture.t0 + (i + 1) * CHUNK_SECONDS - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self._on_audio(self.audio[start : start + step])


@dataclass
class Event:
    at: float  # segundos desde el inicio del WAV
    kind: str
    text: str


class Recorder:
    """Anota cada senal con el momento del WAV en que llega (desde el hilo que la emite)."""

    def __init__(self) -> None:
        self.events: list[Event] = []
        self._lock = threading.Lock()

    def _add(self, kind: str, text: str) -> None:
        with self._lock:
            self.events.append(Event(time.monotonic() - WavCapture.t0, kind, text))

    def partial(self, committed: str, tentative: str) -> None:
        self._add("partial", f"{committed} {tentative}".strip())

    def final(self, _index: int, _seconds: float, _speaker: str, text: str) -> None:
        self._add("final", text)

    def translated(self, _index: int, _seconds: float, text: str) -> None:
        self._add("translated", text)

    def partial_translated(self, _index: int, text: str) -> None:
        self._add("partial_es", text)


def _words(text: str) -> list[str]:
    return re.sub(r"[^\w%' ]+", " ", text.lower()).split()


def wer(reference: str, hypothesis: str) -> float:
    ref, hyp = _words(reference), _words(hypothesis)
    row = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        previous, row[0] = row[0], i
        for j, h in enumerate(hyp, 1):
            previous, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, previous + (r != h))
    return row[-1] / max(1, len(ref))


def first_after(events: list[Event], kind: str, t: float, until: float) -> float | None:
    for event in events:
        if event.kind == kind and t <= event.at < until and event.text:
            return event.at - t
    return None


def report(events: list[Event], spans: list[tuple[float, float]], reference: str | None) -> None:
    rows: dict[str, list[float]] = {"borrador": [], "final": [], "espanol": [], "es borr.": []}
    print(f"\n{'frase':>5} {'voz':>13} {'borrador':>9} {'final':>7} {'espanol':>8} {'es borr.':>9}")
    for n, (start, end) in enumerate(spans, 1):
        limit = spans[n][0] if n < len(spans) else float("inf")
        cells = {
            "borrador": first_after(events, "partial", start, end + 0.5),
            "final": first_after(events, "final", end, limit + 5),
            "espanol": first_after(events, "translated", end, limit + 5),
            "es borr.": first_after(events, "partial_es", start, end + 0.5),
        }
        # El borrador se mide desde que empieza la voz; lo demas, desde que acaba.
        line = f"{n:>5} {start:6.1f}-{end:5.1f}s"
        for key, value in cells.items():
            if value is not None:
                rows[key].append(value)
            width = len(key) + 1 if len(key) > 7 else 8
            line += f" {value:{width}.2f}s" if value is not None else f" {'-':>{width + 1}}"
        print(line)
    print()
    for key, values in rows.items():
        if values:
            print(f"{key:>9}: mediana {statistics.median(values):.2f} s, max {max(values):.2f} s")
    finals = " ".join(e.text for e in events if e.kind == "final")
    print(
        f"\n{sum(e.kind == 'partial' for e in events)} borradores, "
        f"{sum(e.kind == 'final' for e in events)} frases finales"
    )
    print("Transcripcion final:\n  " + "\n  ".join(e.text for e in events if e.kind == "final"))
    if reference is not None:
        print(f"\nWER: {wer(reference, finals):.1%}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("wav", type=Path)
    parser.add_argument("--reference", type=Path, help="texto real, una frase por linea")
    parser.add_argument("--hotwords", default="", help="vocabulario, como en los ajustes")
    parser.add_argument("--verbose", action="store_true", help="cada frase segun se corta")
    args = parser.parse_args()
    if args.verbose:
        logging.basicConfig(format="%(relativeCreated)7.0f ms  %(message)s")
        logging.getLogger("live_captions").setLevel(logging.DEBUG)

    WavCapture.audio = load_wav(args.wav)
    spans = sentences(WavCapture.audio)
    recorder = Recorder()
    pipeline = Pipeline(Path(tempfile.mkdtemp(prefix="replay-")), capture=WavCapture)
    pipeline.set_mic_enabled(False)
    pipeline.set_hotwords(args.hotwords)
    pipeline.partial.connect(recorder.partial)
    pipeline.final.connect(recorder.final)
    pipeline.translated.connect(recorder.translated)
    pipeline.partial_translated.connect(recorder.partial_translated)
    pipeline.status.connect(lambda message: print(message, flush=True))
    ready, stopped = threading.Event(), threading.Event()
    failed: list[str] = []
    pipeline.failed.connect(lambda message: (failed.append(message), ready.set()))
    pipeline.ready.connect(lambda _backend: ready.set())
    pipeline.stopped.connect(stopped.set)

    pipeline.load_models()
    ready.wait()
    if failed:
        sys.exit(failed[0])
    duration = WavCapture.audio.size / RATE
    print(f"Modelos listos; reproduciendo {duration:.0f} s...", flush=True)
    pipeline.start()
    time.sleep(duration + 3)
    pipeline.stop()
    stopped.wait()

    reference = args.reference.read_text(encoding="utf-8") if args.reference else None
    report(recorder.events, spans, reference)


if __name__ == "__main__":
    main()
