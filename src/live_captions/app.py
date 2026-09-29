"""La ventana: pagina web (WebView2 via pywebview) conectada al pipeline y al asistente.

Python -> pagina: cada senal del nucleo se convierte en un evento del `Bridge`.
Pagina -> Python: los metodos publicos de `Api` (`window.pywebview.api.*` en JS); pywebview
los ejecuta en hilos propios, asi que pueden bloquear un momento (probar la conexion) sin
congelar la ventana.
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

import webview

from live_captions.assistant import config as llm_config
from live_captions.assistant.brain import Assistant, ImageNote, Suggestion
from live_captions.assistant.llm import ChatClient, LLMError
from live_captions.bridge import Bridge
from live_captions.pipeline import Pipeline
from live_captions.settings import Settings

log = logging.getLogger(__name__)

WEB = Path(__file__).parent / "web"


def _config_from(values: dict[str, Any]) -> llm_config.LLMConfig:
    return llm_config.LLMConfig(
        provider=str(values.get("provider", "groq")),
        base_url=str(values.get("base_url", "")).strip(),
        model=str(values.get("model", "")).strip(),
        user_name=str(values.get("user_name", "")).strip(),
        vision_model=str(values.get("vision_model", "")).strip(),
        vocabulary=str(values.get("vocabulary", "")).strip(),
    )


def _client_from(values: dict[str, Any]) -> ChatClient:
    """Cliente con lo que hay escrito en el dialogo de ajustes, aun sin guardar."""
    config = _config_from(values)
    return ChatClient(config.base_url, str(values.get("api_key", "")), config.model, timeout=20)


class App:
    def __init__(self, transcripts_dir: Path, settings: Settings | None = None) -> None:
        self.transcripts_dir = transcripts_dir
        self.settings = settings or Settings()
        self.bridge = Bridge()
        self.window: webview.Window | None = None
        self.backend = ""
        self.status = ("Cargando modelos...", False)
        self.closing = False

        self.pipeline = Pipeline(transcripts_dir)
        self.assistant = Assistant()
        self._wire()
        self.pipeline.set_mic_enabled(bool(self.settings.get("mic", True)))
        self.assistant.set_context(str(self.settings.get("context", "")))
        self.configure(llm_config.load(self.settings))

    # -- senales del nucleo -> eventos de la pagina ------------------------------------

    def _wire(self) -> None:
        send = self.bridge.send
        pipeline, assistant = self.pipeline, self.assistant

        pipeline.status.connect(lambda text: self.set_status(text))
        pipeline.warning.connect(lambda text: self.set_status(text, error=True))
        pipeline.failed.connect(self._on_failed)
        pipeline.ready.connect(self._on_ready)
        pipeline.started.connect(self._on_started)
        pipeline.stopped.connect(self._on_stopped)
        pipeline.level.connect(lambda rms: send("level", round(rms, 4)))
        pipeline.partial.connect(lambda c, t: send("partial", {"committed": c, "tentative": t}))
        pipeline.final.connect(self._on_final)
        pipeline.translated.connect(
            lambda i, s, text: send("translated", {"index": i, "seconds": s, "text": text})
        )
        pipeline.partial_translated.connect(
            lambda i, text: send("partial_translated", {"index": i, "text": text})
        )

        assistant.suggestion.connect(self._on_suggestion)
        assistant.image_read.connect(self._on_image_read)
        assistant.chat_started.connect(self._on_chat_started)
        assistant.chat_delta.connect(lambda delta: send("chat_delta", delta))
        assistant.chat_done.connect(self._on_chat_done)
        assistant.status.connect(lambda text: send("assistant_status", {"text": text}))
        assistant.error.connect(
            lambda text: send("assistant_status", {"text": text, "error": True})
        )

    def set_status(self, text: str, *, error: bool = False) -> None:
        self.status = (text, error)
        self.bridge.send("status", {"text": text, "error": error})

    def _on_ready(self, backend: str) -> None:
        self.backend = backend
        self.bridge.send("ready", backend)
        gpu = backend.startswith("cuda")
        self.set_status("Listo." if gpu else "Listo (sin GPU: ira lento).")

    def _on_failed(self, message: str) -> None:
        self.set_status(message, error=True)
        self.bridge.send("failed", message)

    def _on_started(self, device: str, path: str) -> None:
        self.bridge.send("started", {"device": device, "path": path})
        self.set_status(f"Escuchando {device}")

    def _on_stopped(self) -> None:
        self.bridge.send("stopped")
        self.set_status("Sesion guardada.")
        if self.closing and self.window is not None:
            self.window.destroy()

    def _on_final(self, index: int, seconds: float, speaker: str, text: str) -> None:
        self.bridge.send(
            "final", {"index": index, "seconds": seconds, "speaker": speaker, "text": text}
        )
        self.assistant.on_line(speaker, seconds, text)

    def _on_suggestion(self, suggestion: Suggestion) -> None:
        self.bridge.send("suggestion", asdict(suggestion))
        answers = " | ".join(a["en"] for a in suggestion.answers)
        self.pipeline.log_assistant(f"Sugerencia para «{suggestion.question}»: {answers}")

    def _on_image_read(self, image: ImageNote) -> None:
        self.bridge.send("image_read", {"id": image.id, "description": image.description})
        self.pipeline.log_assistant(f"Imagen {image.id} leida: {image.description}")

    def _on_chat_started(self, question: str) -> None:
        self.bridge.send("chat_started", question)
        self.pipeline.log_assistant(f"Tú: {question}")

    def _on_chat_done(self, answer: str) -> None:
        self.bridge.send("chat_done", answer)
        self.pipeline.log_assistant(f"Asistente: {answer}")

    # -- configuracion -----------------------------------------------------------------

    def configure(self, config: llm_config.LLMConfig) -> None:
        self.pipeline.set_hotwords(config.hotwords)
        self.assistant.configure(config)
        self.bridge.send("assistant_config", self.assistant_badge(config))

    def assistant_badge(self, config: llm_config.LLMConfig | None = None) -> dict[str, Any]:
        config = config or llm_config.load(self.settings)
        if not self.assistant.is_configured:
            return {"configured": False, "badge": "sin configurar"}
        vision = " · imagenes" if config.has_vision else ""
        return {"configured": True, "badge": f"{config.model} · {config.provider}{vision}"}

    # -- ventana -----------------------------------------------------------------------

    def run(self) -> None:
        geometry = self.settings.get("window") or {}
        self.window = webview.create_window(
            "Live Captions",
            url=str(WEB / "index.html"),
            js_api=Api(self),
            width=int(geometry.get("width", 1440)),
            height=int(geometry.get("height", 760)),
            x=geometry.get("x"),
            y=geometry.get("y"),
            min_size=(720, 420),
            background_color="#000000",
            on_top=bool(self.settings.get("on_top", False)),
            text_select=True,
        )
        self.window.events.loaded += self._on_loaded
        self.window.events.closing += self._on_closing
        self.pipeline.load_models()
        # Servida por HTTP local y no como file://, para que el portapapeles (Copiar) funcione.
        webview.start(
            http_server=True,
            private_mode=False,
            storage_path=str(self.settings.path.parent / "web"),
        )

    def _on_loaded(self) -> None:
        assert self.window is not None
        self.bridge.attach(self.window.run_js)

    def _on_closing(self) -> bool:
        window = self.window
        # Minimizada, Windows la situa en (-32000, -32000): esa posicion no se guarda.
        if window is not None and window.x > -10000:
            self.settings.set(
                "window",
                {"width": window.width, "height": window.height, "x": window.x, "y": window.y},
            )
        if self.pipeline.is_running and not self.closing:
            # Primero se cierra la frase en curso y se guarda; `_on_stopped` cierra la ventana.
            self.closing = True
            self.set_status("Guardando la sesion...")
            self.pipeline.stop()
            return False
        return True


class Api:
    """Lo que la pagina puede pedir. Solo los metodos publicos se exponen a JS."""

    def __init__(self, app: App) -> None:
        self._app = app

    def state(self) -> dict[str, Any]:
        """Todo lo que la pagina necesita al cargar (o recargar)."""
        app, settings = self._app, self._app.settings
        return {
            "ready": app.pipeline.is_ready,
            "running": app.pipeline.is_running,
            "elapsed": app.pipeline.elapsed,
            "backend": app.backend,
            "status": {"text": app.status[0], "error": app.status[1]},
            "mic": bool(settings.get("mic", True)),
            "on_top": bool(settings.get("on_top", False)),
            "spanish": bool(settings.get("spanish", True)),
            "assistant": bool(settings.get("assistant", True)),
            "widths": settings.get("widths"),
            "context": str(settings.get("context", "")),
            "assistant_config": app.assistant_badge(),
        }

    # -- escucha -----------------------------------------------------------------------

    def toggle(self) -> None:
        pipeline = self._app.pipeline
        if pipeline.is_running:
            self._app.set_status("Cerrando la frase en curso...")
            pipeline.stop()
        else:
            self._app.assistant.reset()
            pipeline.start()

    def set_mic(self, enabled: bool) -> None:
        self._app.settings.set("mic", bool(enabled))
        self._app.pipeline.set_mic_enabled(bool(enabled))

    # -- ventana y preferencias --------------------------------------------------------

    def set_on_top(self, enabled: bool) -> None:
        self._app.settings.set("on_top", bool(enabled))
        if self._app.window is not None:
            self._app.window.on_top = bool(enabled)

    def set_layout(self, spanish: bool, assistant: bool, widths: list[float] | None) -> None:
        self._app.settings.update(
            {"spanish": bool(spanish), "assistant": bool(assistant), "widths": widths}
        )

    def open_folder(self) -> None:
        folder = self._app.transcripts_dir
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)  # type: ignore[attr-defined]  # solo existe en Windows

    # -- asistente ---------------------------------------------------------------------

    def set_context(self, text: str) -> None:
        self._app.settings.set("context", str(text))
        self._app.assistant.set_context(str(text))

    def ask(self, question: str) -> None:
        self._app.assistant.ask(str(question))

    def send_image(self, data_url: str, note: str) -> dict[str, Any]:
        image = self._app.assistant.attach_image(str(data_url), str(note))
        if image is None:
            return {"ok": False}
        self._app.pipeline.log_assistant(f"Imagen {image.id} enviada: {note or '(sin nota)'}")
        return {"ok": True, "id": image.id}

    def remove_image(self, image_id: int) -> None:
        self._app.assistant.remove_image(int(image_id))

    def clear_images(self) -> None:
        self._app.assistant.clear_images()

    # -- ajustes -----------------------------------------------------------------------

    def load_settings(self) -> dict[str, Any]:
        config = llm_config.load(self._app.settings)
        return {
            **asdict(config),
            "api_key": config.api_key,
            "presets": {key: asdict(p) for key, p in llm_config.PRESETS.items()},
        }

    def preset(self, provider: str) -> dict[str, Any]:
        preset = llm_config.PRESETS[provider]
        return {**asdict(preset), "api_key": llm_config.LLMConfig(provider=provider).api_key}

    def save_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        config = _config_from(values)
        config.set_api_key(str(values.get("api_key", "")).strip())
        llm_config.save(self._app.settings, config)
        self._app.configure(config)
        return self._app.assistant_badge(config)

    def test_connection(self, values: dict[str, Any]) -> dict[str, Any]:
        client = _client_from(values)
        try:
            reply = client.ping()
        except LLMError as exc:
            return {"ok": False, "message": str(exc)}
        except Exception as exc:
            return {"ok": False, "message": f"Error: {exc}"}
        finally:
            client.close()
        return {"ok": True, "message": f"Conectado. El modelo respondio: {reply[:40]!r}"}

    def list_models(self, values: dict[str, Any]) -> dict[str, Any]:
        client = _client_from(values)
        try:
            models = client.list_models()
        except LLMError as exc:
            return {"ok": False, "message": str(exc)}
        except Exception as exc:
            return {"ok": False, "message": f"Error: {exc}"}
        finally:
            client.close()
        return {"ok": True, "models": models, "message": f"{len(models)} modelos disponibles"}
