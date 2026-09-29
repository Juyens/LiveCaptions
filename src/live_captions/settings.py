"""Preferencias del usuario en un JSON de `%APPDATA%\\LiveCaptions`.

Claves planas con barras ("llm/model"), las mismas que usaba QSettings cuando la interfaz era
Qt; la primera vez se importan del registro, donde las dejo aquella version, para que nadie
pierda su contexto ni su proveedor al actualizar. La API key no pasa nunca por aqui: vive en el
Administrador de credenciales de Windows (ver `assistant.config`).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Donde guardaba QSettings("Juyens", "LiveCaptions") en Windows.
LEGACY_KEY = r"Software\Juyens\LiveCaptions"
_LEGACY_TEXT = (
    "context",
    "llm/provider",
    "llm/base_url",
    "llm/model",
    "llm/user_name",
    "llm/vision_model",
    "transcription/vocabulary",
)
_LEGACY_FLAGS = ("on_top", "mic", "spanish", "assistant")


def default_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "LiveCaptions" / "settings.json"


def read_legacy() -> dict[str, Any]:
    """Lo que la version Qt dejo en el registro (vacio si no hay nada o no es Windows)."""
    try:
        import winreg
    except ImportError:
        return {}
    found: dict[str, Any] = {}
    for key in (*_LEGACY_TEXT, *_LEGACY_FLAGS):
        folder, _, name = key.rpartition("/")
        path = LEGACY_KEY + ("\\" + folder.replace("/", "\\") if folder else "")
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as handle:
                value, _ = winreg.QueryValueEx(handle, name)
        except OSError:
            continue
        if key in _LEGACY_FLAGS:
            found[key] = str(value).lower() == "true"
        elif isinstance(value, str):
            found[key] = value
    return found


class Settings:
    """Diccionario persistente; cada `set` se escribe a disco (son pocos y pequenos)."""

    def __init__(self, path: Path | None = None, *, legacy: dict[str, Any] | None = None) -> None:
        self._path = path or default_path()
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {}
        if self._path.exists():
            try:
                loaded = json.loads(self._path.read_text(encoding="utf-8"))
                self._data = loaded if isinstance(loaded, dict) else {}
            except (OSError, ValueError):
                log.warning("Ajustes ilegibles en %s; se empieza de cero", self._path)
        else:
            self._data = read_legacy() if legacy is None else dict(legacy)
            if self._data:
                log.info("Ajustes importados de la version anterior: %s", sorted(self._data))
                self._write()

    @property
    def path(self) -> Path:
        return self._path

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.update({key: value})

    def update(self, values: dict[str, Any]) -> None:
        with self._lock:
            self._data.update(values)
            self._write()

    def _write(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Escritura atomica: un cierre a medias no deja un JSON truncado.
        temporary = self._path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            temporary.replace(self._path)
        except OSError:
            log.warning("No se pudieron guardar los ajustes en %s", self._path, exc_info=True)
