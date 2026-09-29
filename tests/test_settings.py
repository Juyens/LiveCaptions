"""Ajustes en JSON: persisten, sobreviven a un archivo roto e importan la version Qt una vez."""

from __future__ import annotations

from live_captions.settings import Settings


def test_values_persist_across_instances(tmp_path) -> None:
    path = tmp_path / "settings.json"
    Settings(path, legacy={}).update({"mic": False, "context": "Soy Joseph"})
    again = Settings(path, legacy={})
    assert again.get("mic") is False
    assert again.get("context") == "Soy Joseph"
    assert again.get("missing", 7) == 7


def test_legacy_values_are_imported_only_when_there_is_no_file(tmp_path) -> None:
    path = tmp_path / "settings.json"
    first = Settings(path, legacy={"llm/provider": "cerebras", "spanish": False})
    assert first.get("llm/provider") == "cerebras"
    assert path.exists()
    first.set("llm/provider", "groq")
    # Con el archivo ya creado, el registro no vuelve a pisar lo que el usuario cambio.
    assert Settings(path, legacy={"llm/provider": "cerebras"}).get("llm/provider") == "groq"


def test_a_corrupt_file_starts_empty(tmp_path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{not json", encoding="utf-8")
    settings = Settings(path, legacy={"mic": True})
    assert settings.get("mic") is None
    settings.set("mic", True)
    assert Settings(path).get("mic") is True
