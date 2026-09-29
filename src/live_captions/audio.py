"""Captura de audio por WASAPI, entregada a 16 kHz mono.

Dos fuentes:
- "loopback": lo que suena por la salida por defecto (la reunion). Windows expone cada
  dispositivo de salida tambien como entrada loopback, sin cables virtuales.
- "mic": el microfono por defecto (lo que dice el propio usuario).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pyaudiowpatch as pa
import soxr

TARGET_RATE = 16_000
CHUNK_SECONDS = 0.1

Source = Literal["loopback", "mic"]
AudioCallback = Callable[[np.ndarray], None]


@dataclass(frozen=True)
class Device:
    """Dispositivo elegido, con su formato nativo."""

    index: int
    name: str
    rate: int
    channels: int


def _as_device(info: dict) -> Device:
    return Device(
        index=int(info["index"]),
        name=str(info["name"]).removesuffix(" [Loopback]"),
        rate=int(info["defaultSampleRate"]),
        channels=max(1, int(info["maxInputChannels"])),
    )


def default_loopback(audio: pa.PyAudio) -> Device:
    """Devuelve el loopback del dispositivo de salida por defecto."""
    wasapi = audio.get_host_api_info_by_type(pa.paWASAPI)
    output = audio.get_device_info_by_index(wasapi["defaultOutputDevice"])
    if not output.get("isLoopbackDevice", False):
        for candidate in audio.get_loopback_device_info_generator():
            if str(output["name"]) in str(candidate["name"]):
                output = candidate
                break
        else:
            raise RuntimeError(f"No hay loopback para la salida {output['name']!r}")
    return _as_device(output)


def default_microphone(audio: pa.PyAudio) -> Device:
    """Devuelve el microfono por defecto de WASAPI."""
    wasapi = audio.get_host_api_info_by_type(pa.paWASAPI)
    index = int(wasapi["defaultInputDevice"])
    if index < 0:
        raise RuntimeError("No hay microfono por defecto")
    return _as_device(audio.get_device_info_by_index(index))


def microphones(audio: pa.PyAudio) -> list[Device]:
    """Entradas WASAPI reales (sin los loopback de las salidas), en el orden de Windows."""
    wasapi = audio.get_host_api_info_by_type(pa.paWASAPI)
    found = []
    for index in range(audio.get_device_count()):
        info = audio.get_device_info_by_index(index)
        if (
            info["hostApi"] == wasapi["index"]
            and int(info["maxInputChannels"]) > 0
            and not info.get("isLoopbackDevice", False)
        ):
            found.append(_as_device(info))
    return found


def list_microphones() -> tuple[list[str], str]:
    """(nombres de los microfonos, nombre del predeterminado) para ofrecerlos al usuario."""
    audio = pa.PyAudio()
    try:
        names = [device.name for device in microphones(audio)]
        try:
            default = default_microphone(audio).name
        except RuntimeError:
            default = ""
        return names, default
    finally:
        audio.terminate()


def pick_microphone(audio: pa.PyAudio, name: str) -> Device:
    """El microfono con ese nombre; el predeterminado si `name` esta vacio o ya no existe.

    Se elige por nombre y no por indice: Windows renumera los dispositivos cada vez que se
    conecta o desconecta uno (unos auriculares, una webcam).
    """
    if name:
        for device in microphones(audio):
            if device.name == name:
                return device
    return default_microphone(audio)


class Capture:
    """Abre una fuente y entrega bloques float32 mono a 16 kHz al callback.

    El callback corre en el hilo de PortAudio: debe ser rapido y no bloquear (encolar y salir).
    """

    def __init__(self, source: Source, on_audio: AudioCallback) -> None:
        self._source: Source = source
        self._on_audio = on_audio
        self._audio: pa.PyAudio | None = None
        self._stream: pa.Stream | None = None
        self._resampler: soxr.ResampleStream | None = None
        self._channels = 2
        self._lock = threading.Lock()
        self.device_name = ""  # solo microfono: "" = el predeterminado de Windows

    def start(self) -> Device:
        with self._lock:
            if self._stream is not None:
                raise RuntimeError("La captura ya esta en marcha")
            self._audio = pa.PyAudio()
            try:
                if self._source == "loopback":
                    device = default_loopback(self._audio)
                else:
                    device = pick_microphone(self._audio, self.device_name)
                self._channels = device.channels
                self._resampler = soxr.ResampleStream(
                    device.rate, TARGET_RATE, num_channels=1, dtype="float32"
                )
                self._stream = self._audio.open(
                    format=pa.paInt16,
                    channels=device.channels,
                    rate=device.rate,
                    input=True,
                    input_device_index=device.index,
                    frames_per_buffer=int(device.rate * CHUNK_SECONDS),
                    stream_callback=self._callback,
                )
            except Exception:
                self._audio.terminate()
                self._audio = None
                raise
            return device

    def stop(self) -> None:
        with self._lock:
            if self._stream is not None:
                self._stream.stop_stream()
                self._stream.close()
                self._stream = None
            if self._audio is not None:
                self._audio.terminate()
                self._audio = None
            self._resampler = None

    def _callback(
        self, data: bytes | None, _frames: int, _time: object, _status: int
    ) -> tuple[None, int]:
        if data and self._resampler is not None:
            pcm = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
            mono = pcm.reshape(-1, self._channels).mean(axis=1) if self._channels > 1 else pcm
            resampled = self._resampler.resample_chunk(np.ascontiguousarray(mono))
            if resampled.size:
                self._on_audio(resampled)
        return None, pa.paContinue
