"""Archivos de sesion: cada frase se escribe en disco en cuanto existe.

Una sesion produce tres Markdown con el mismo prefijo de fecha:
- `.en.md`: transcripcion en ingles, con `Ellos:` / `Tu:` segun quien hablo.
- `.es.md`: traduccion de lo que dijeron los demas.
- `.assistant.md`: sugerencias y chat del asistente.

Se escribe con flush inmediato para que un cierre brusco no pierda nada de lo ya transcrito.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal, TextIO

Speaker = Literal["them", "me"]
LABELS: dict[str, str] = {"them": "Ellos", "me": "Tú"}


def format_clock(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


class Session:
    def __init__(self, root: Path, started: datetime | None = None) -> None:
        started = started or datetime.now()
        root.mkdir(parents=True, exist_ok=True)
        stem = started.strftime("%Y-%m-%d_%H-%M-%S")
        self.english_path = root / f"{stem}.en.md"
        self.spanish_path = root / f"{stem}.es.md"
        self.assistant_path = root / f"{stem}.assistant.md"
        title = started.strftime("%Y-%m-%d %H:%M")
        self._english = self._open(self.english_path, f"# Transcript {title}\n\n")
        self._spanish = self._open(self.spanish_path, f"# Transcripcion {title}\n\n")
        self._assistant: TextIO | None = None
        self._assistant_header = f"# Asistente {title}\n\n"

    @staticmethod
    def _open(path: Path, header: str) -> TextIO:
        handle = path.open("a", encoding="utf-8")
        handle.write(header)
        handle.flush()
        return handle

    def append_english(self, seconds: float, text: str, speaker: Speaker = "them") -> None:
        self._append(self._english, seconds, f"{LABELS[speaker]}: {text}")

    def append_spanish(self, seconds: float, text: str) -> None:
        self._append(self._spanish, seconds, text)

    def append_assistant(self, seconds: float, text: str) -> None:
        """El archivo del asistente solo se crea si llega a usarse."""
        if self._assistant is None:
            if self._english.closed:
                return
            self._assistant = self._open(self.assistant_path, self._assistant_header)
        self._append(self._assistant, seconds, text)

    @staticmethod
    def _append(handle: TextIO, seconds: float, text: str) -> None:
        if handle.closed:
            return
        handle.write(f"[{format_clock(seconds)}] {text}\n")
        handle.flush()

    def close(self) -> None:
        self._english.close()
        self._spanish.close()
        if self._assistant is not None:
            self._assistant.close()
