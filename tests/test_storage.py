from __future__ import annotations

from datetime import datetime
from pathlib import Path

from live_captions.storage import Session, format_clock


def test_format_clock() -> None:
    assert format_clock(0) == "00:00:00"
    assert format_clock(59.9) == "00:00:59"
    assert format_clock(3725) == "01:02:05"


def test_session_writes_parallel_files_immediately(tmp_path: Path) -> None:
    session = Session(tmp_path / "out", started=datetime(2026, 9, 16, 20, 30, 5))
    session.append_english(12.4, "Hello there.")
    session.append_english(15.0, "Hi, thanks.", speaker="me")
    session.append_spanish(12.4, "Hola.")

    # Sin cerrar la sesion: el contenido ya tiene que estar en disco.
    english = (tmp_path / "out" / "2026-09-16_20-30-05.en.md").read_text(encoding="utf-8")
    spanish = (tmp_path / "out" / "2026-09-16_20-30-05.es.md").read_text(encoding="utf-8")
    assert english == (
        "# Transcript 2026-09-16 20:30\n\n"
        "[00:00:12] Ellos: Hello there.\n"
        "[00:00:15] Tú: Hi, thanks.\n"
    )
    assert spanish == "# Transcripcion 2026-09-16 20:30\n\n[00:00:12] Hola.\n"

    session.close()
    session.append_english(20, "late")  # tras cerrar se ignora en vez de reventar
    assert "late" not in session.english_path.read_text(encoding="utf-8")


def test_assistant_file_is_created_only_when_used(tmp_path: Path) -> None:
    session = Session(tmp_path, started=datetime(2026, 9, 16, 20, 30, 5))
    assert not session.assistant_path.exists()
    session.append_assistant(3, "Sugerencia: ...")
    session.close()
    content = session.assistant_path.read_text(encoding="utf-8")
    assert content.endswith("[00:00:03] Sugerencia: ...\n")
