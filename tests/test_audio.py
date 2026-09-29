"""Eleccion de microfono por nombre, con un PyAudio falso (sin tocar el hardware)."""

from __future__ import annotations

from typing import Any

from live_captions.audio import microphones, pick_microphone

WASAPI = 3


class FakeAudio:
    """Lo minimo de pyaudiowpatch.PyAudio que usan `microphones` y `pick_microphone`."""

    def __init__(self, devices: list[dict[str, Any]], default_input: int) -> None:
        self._devices = [
            {"index": i, "defaultSampleRate": 48000.0, **device} for i, device in enumerate(devices)
        ]
        self._default = default_input

    def get_host_api_info_by_type(self, _kind: int) -> dict[str, int]:
        return {"index": WASAPI, "defaultInputDevice": self._default}

    def get_device_count(self) -> int:
        return len(self._devices)

    def get_device_info_by_index(self, index: int) -> dict[str, Any]:
        return self._devices[index]


def machine() -> FakeAudio:
    return FakeAudio(
        [
            {"name": "Varios micrófonos (Intel®)", "hostApi": WASAPI, "maxInputChannels": 2},
            {"name": "Altavoces", "hostApi": WASAPI, "maxInputChannels": 0},
            {
                "name": "Altavoces [Loopback]",
                "hostApi": WASAPI,
                "maxInputChannels": 2,
                "isLoopbackDevice": True,
            },
            {"name": "Auriculares (soundcore P25i)", "hostApi": WASAPI, "maxInputChannels": 1},
            {"name": "Auriculares (soundcore P25i)", "hostApi": 1, "maxInputChannels": 1},
        ],
        default_input=0,
    )


def test_only_real_wasapi_inputs_are_offered() -> None:
    names = [device.name for device in microphones(machine())]
    assert names == ["Varios micrófonos (Intel®)", "Auriculares (soundcore P25i)"]


def test_a_microphone_is_picked_by_name() -> None:
    device = pick_microphone(machine(), "Auriculares (soundcore P25i)")
    assert device.index == 3
    assert device.channels == 1


def test_empty_or_unplugged_names_fall_back_to_the_default() -> None:
    assert pick_microphone(machine(), "").index == 0
    assert pick_microphone(machine(), "Cascos que ya no estan").index == 0
