"""El troceado se prueba con un detector falso: voz = muestras distintas de cero."""

from __future__ import annotations

import numpy as np

from live_captions.segmenter import RATE, Segmenter


def fake_regions(audio: np.ndarray) -> list[tuple[int, int]]:
    """Tramos contiguos con |x| > 0, como haria un VAD perfecto."""
    voiced = np.abs(audio) > 0
    if not voiced.any():
        return []
    edges = np.flatnonzero(np.diff(np.concatenate([[0], voiced.astype(int), [0]])))
    return [(int(edges[i]), int(edges[i + 1])) for i in range(0, len(edges), 2)]


def speech(seconds: float) -> np.ndarray:
    return np.full(int(seconds * RATE), 0.5, dtype=np.float32)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * RATE), dtype=np.float32)


def feed_all(segmenter: Segmenter, audio: np.ndarray, chunk_s: float = 0.1) -> list[np.ndarray]:
    out: list[np.ndarray] = []
    step = int(chunk_s * RATE)
    for start in range(0, audio.size, step):
        out.extend(segmenter.feed(audio[start : start + step]))
    return out


def test_closes_a_sentence_after_a_pause() -> None:
    seg = Segmenter(fake_regions, silence_s=0.6)
    out = feed_all(seg, np.concatenate([silence(0.3), speech(2.0), silence(1.0)]))
    assert len(out) == 1
    # 2 s de voz mas el margen a cada lado (0.15 s).
    assert abs(out[0].size / RATE - 2.3) < 0.11


def test_keeps_waiting_while_the_pause_is_short() -> None:
    seg = Segmenter(fake_regions, silence_s=0.6)
    out = feed_all(seg, np.concatenate([speech(1.0), silence(0.3)]))
    assert out == []
    assert seg.pending() is not None


def test_discards_clicks_shorter_than_min_speech() -> None:
    seg = Segmenter(fake_regions, min_speech_s=0.3, pad_s=0.0)
    out = feed_all(seg, np.concatenate([speech(0.1), silence(1.0)]))
    assert out == []


def test_cuts_long_speech_at_the_last_internal_pause() -> None:
    seg = Segmenter(fake_regions, max_segment_s=5.0, silence_s=2.0, pad_s=0.0)
    audio = np.concatenate([speech(3.0), silence(0.2), speech(3.0)])
    out = feed_all(seg, audio)
    assert len(out) == 1
    assert abs(out[0].size / RATE - 3.0) < 0.11
    # Lo que sigue a la pausa se queda esperando como frase en curso.
    assert seg.pending() is not None


def test_cuts_long_speech_without_pauses_hard() -> None:
    seg = Segmenter(fake_regions, max_segment_s=5.0, silence_s=2.0, pad_s=0.0)
    out = feed_all(seg, speech(7.0))
    assert len(out) == 1
    assert out[0].size / RATE >= 5.0


def test_silence_does_not_grow_the_buffer() -> None:
    seg = Segmenter(fake_regions, silence_s=0.6)
    feed_all(seg, silence(30.0))
    assert seg._buffer.size <= 0.6 * RATE + 0.5 * RATE


def test_flush_returns_the_open_sentence_and_resets() -> None:
    seg = Segmenter(fake_regions, pad_s=0.0)
    feed_all(seg, speech(1.0))
    tail = seg.flush()
    assert tail is not None
    assert abs(tail.size / RATE - 1.0) < 0.01
    assert seg.flush() is None
    assert seg.pending() is None


def test_no_ghost_segment_from_the_tail_of_the_previous_sentence() -> None:
    # Antes el buffer se quedaba con los ultimos 0.15 s de voz de la frase emitida; el VAD los
    # volvia a ver como voz y salia un segmento fantasma que Whisper convertia en "you".
    seg = Segmenter(fake_regions, silence_s=0.6)
    audio = np.concatenate([speech(2.0), silence(1.5), speech(2.0), silence(1.5)])
    out = feed_all(seg, audio)
    assert len(out) == 2
    assert all(s.size / RATE > 2.0 for s in out)


def test_min_speech_counts_voice_not_padding() -> None:
    # 0.2 s de voz mas 0.15 s de margen a cada lado superan 0.3 s, pero la voz no.
    seg = Segmenter(fake_regions, min_speech_s=0.3, pad_s=0.15)
    out = feed_all(seg, np.concatenate([silence(0.5), speech(0.2), silence(1.0)]))
    assert out == []


def test_pending_covers_the_open_sentence() -> None:
    seg = Segmenter(fake_regions, pad_s=0.0)
    feed_all(seg, np.concatenate([silence(0.4), speech(1.0)]))
    pending = seg.pending()
    assert pending is not None
    assert abs(pending.size / RATE - 1.0) < 0.21


def test_a_late_burst_of_audio_still_closes_the_sentence_inside_it() -> None:
    # Si el hilo de trabajo se atasca, el audio acumulado llega de golpe. La pausa que hay
    # dentro del bloque tiene que cerrar la frase igual que si hubiera llegado poco a poco.
    seg = Segmenter(fake_regions, silence_s=0.6, pad_s=0.0)
    out = seg.feed(np.concatenate([speech(2.0), silence(1.5), speech(1.0)]))
    assert len(out) == 1
    assert abs(out[0].size / RATE - 2.0) < 0.01
    assert seg.pending() is not None
