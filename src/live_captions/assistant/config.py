"""Configuracion del asistente: proveedor, modelo y API key.

Todos los proveedores hablan el protocolo de chat de OpenAI, incluido un `llama-server`
local, asi que cambiar de uno a otro es cambiar URL y modelo. La API key no va a QSettings
(registro en claro): se guarda en el Administrador de credenciales de Windows via keyring.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass

import keyring
from PySide6.QtCore import QSettings

SERVICE = "LiveCaptions"


@dataclass(frozen=True)
class Preset:
    label: str
    base_url: str
    model: str
    needs_key: bool = True
    vision_model: str = ""  # modelo que entiende imagenes, si el proveedor tiene uno


PRESETS: dict[str, Preset] = {
    "groq": Preset(
        "Groq",
        "https://api.groq.com/openai/v1",
        "openai/gpt-oss-120b",
        vision_model="qwen/qwen3.6-27b",
    ),
    "cerebras": Preset("Cerebras", "https://api.cerebras.ai/v1", "gpt-oss-120b"),
    "nvidia": Preset(
        "NVIDIA NIM", "https://integrate.api.nvidia.com/v1", "meta/llama-3.3-70b-instruct"
    ),
    "openrouter": Preset(
        "OpenRouter", "https://openrouter.ai/api/v1", "meta-llama/llama-3.3-70b-instruct:free"
    ),
    "local": Preset("Local (llama-server)", "http://127.0.0.1:8080/v1", "local", needs_key=False),
}

# Modelos que los proveedores han retirado; se sustituyen al cargar la configuracion guardada
# para que una instalacion antigua no se quede pidiendo un modelo inexistente.
RETIRED: dict[str, str] = {
    "llama-3.3-70b-versatile": "openai/gpt-oss-120b",  # Groq, 2026-08-16
    "llama-3.1-8b-instant": "openai/gpt-oss-20b",  # Groq, 2026-08-16
    "llama-3.3-70b": "gpt-oss-120b",  # Cerebras
}


@dataclass
class LLMConfig:
    provider: str = "groq"
    base_url: str = PRESETS["groq"].base_url
    model: str = PRESETS["groq"].model
    user_name: str = ""
    vision_model: str = PRESETS["groq"].vision_model
    vocabulary: str = ""  # nombres y terminos de las reuniones, pista para Whisper

    @property
    def hotwords(self) -> str:
        """Pista para Whisper: el nombre del usuario y su vocabulario."""
        return ", ".join(part for part in (self.user_name, self.vocabulary) if part)

    @property
    def needs_key(self) -> bool:
        preset = PRESETS.get(self.provider)
        return preset.needs_key if preset else True

    @property
    def api_key(self) -> str:
        try:
            return keyring.get_password(SERVICE, self.provider) or ""
        except keyring.errors.KeyringError:
            return ""

    def set_api_key(self, value: str) -> None:
        if value:
            keyring.set_password(SERVICE, self.provider, value)
            return
        with contextlib.suppress(keyring.errors.PasswordDeleteError):
            keyring.delete_password(SERVICE, self.provider)

    @property
    def has_vision(self) -> bool:
        return self.is_usable and bool(self.vision_model)

    @property
    def is_usable(self) -> bool:
        return bool(self.base_url and self.model) and (not self.needs_key or bool(self.api_key))


def load(settings: QSettings) -> LLMConfig:
    default = LLMConfig()
    return LLMConfig(
        provider=str(settings.value("llm/provider", default.provider)),
        base_url=str(settings.value("llm/base_url", default.base_url)),
        model=RETIRED.get(model := str(settings.value("llm/model", default.model)), model),
        user_name=str(settings.value("llm/user_name", "")),
        vision_model=str(settings.value("llm/vision_model", default.vision_model)),
        vocabulary=str(settings.value("transcription/vocabulary", "")),
    )


def save(settings: QSettings, config: LLMConfig) -> None:
    settings.setValue("llm/provider", config.provider)
    settings.setValue("llm/base_url", config.base_url)
    settings.setValue("llm/model", config.model)
    settings.setValue("llm/user_name", config.user_name)
    settings.setValue("llm/vision_model", config.vision_model)
    settings.setValue("transcription/vocabulary", config.vocabulary)
