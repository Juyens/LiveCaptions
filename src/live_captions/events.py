"""Senales minimas para que el nucleo avise sin depender de ningun toolkit grafico.

`emit` llama a los suscriptores en el hilo que emite, en orden. Quien escuche desde un hilo
de trabajo (Whisper, el asistente) tiene que ser rapido y no bloquear: la interfaz se limita a
encolar el evento (ver `bridge.Bridge`).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)


class Signal:
    def __init__(self) -> None:
        self._slots: list[Callable[..., Any]] = []

    def connect(self, slot: Callable[..., Any]) -> None:
        self._slots.append(slot)

    def emit(self, *args: Any) -> None:
        for slot in list(self._slots):
            try:
                slot(*args)
            except Exception:
                # Un suscriptor roto no debe tumbar el hilo que emite (p. ej. el de Whisper).
                log.exception("Fallo en un suscriptor de senal")
