"""El puente entrega en orden y del nivel de audio solo el ultimo de cada lote."""

from __future__ import annotations

import threading

from live_captions.bridge import Bridge, pack
from live_captions.events import Signal


def test_pack_keeps_order_and_only_the_last_level() -> None:
    batch = [("level", 0.1), ("partial", "a"), ("level", 0.2), ("final", "b"), ("level", 0.3)]
    assert pack(batch) == [["partial", "a"], ["final", "b"], ["level", 0.3]]


def test_events_sent_before_the_page_loads_are_delivered_after() -> None:
    bridge = Bridge()
    bridge.send("status", {"text": "Cargando"})
    delivered: list[str] = []
    done = threading.Event()

    def run_js(script: str) -> None:
        delivered.append(script)
        done.set()

    bridge.attach(run_js)
    assert done.wait(2)
    assert '["status", {"text": "Cargando"}]' in delivered[0]


def test_a_failing_subscriber_does_not_stop_the_others() -> None:
    signal = Signal()
    seen: list[int] = []
    signal.connect(lambda _: 1 / 0)
    signal.connect(seen.append)
    signal.emit(5)
    assert seen == [5]
