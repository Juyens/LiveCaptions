"""Canal de eventos Python -> pagina web, sin frenar nunca a quien emite.

Los hilos de audio, Whisper y el asistente solo encolan (`send`). Un hilo propio vacia la cola
cada `FLUSH_S` y entrega el lote a la pagina de una vez con `run_js`, que no espera respuesta.
El nivel de audio llega diez veces por segundo y solo importa el ultimo, asi que dentro de un
lote se queda uno.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

FLUSH_S = 0.03
_LATEST_ONLY = {"level"}

Event = tuple[str, Any]


def pack(batch: list[Event]) -> list[list[Any]]:
    """Lote listo para la pagina: en orden, con un solo `level` (el ultimo)."""
    last = {kind: i for i, (kind, _) in enumerate(batch) if kind in _LATEST_ONLY}
    return [
        [kind, payload]
        for i, (kind, payload) in enumerate(batch)
        if kind not in _LATEST_ONLY or last[kind] == i
    ]


class Bridge:
    def __init__(self) -> None:
        self._events: queue.Queue[Event] = queue.Queue()
        self._run_js: Callable[[str], Any] | None = None
        self._thread: threading.Thread | None = None

    def send(self, kind: str, payload: Any = None) -> None:
        self._events.put((kind, payload))

    def attach(self, run_js: Callable[[str], Any]) -> None:
        """Empieza a entregar; lo encolado antes de que la pagina cargara sale en el primer lote."""
        self._run_js = run_js
        if self._thread is None:
            self._thread = threading.Thread(target=self._pump, name="bridge", daemon=True)
            self._thread.start()

    def _pump(self) -> None:
        while True:
            batch = [self._events.get()]
            time.sleep(FLUSH_S)  # deja que se junten los que vienen detras
            while not self._events.empty():
                batch.append(self._events.get_nowait())
            script = f"window.lc && window.lc.receive({json.dumps(pack(batch))})"
            try:
                assert self._run_js is not None
                self._run_js(script)
            except Exception:
                # La ventana puede estar cerrandose; no hay a quien avisar.
                log.debug("No se pudo entregar un lote a la pagina", exc_info=True)
