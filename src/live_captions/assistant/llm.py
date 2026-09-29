"""Cliente minimo del protocolo de chat de OpenAI (Groq, Cerebras, NVIDIA, llama-server...)."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

import httpx

Message = dict[str, Any]  # el contenido puede ser texto o una lista de bloques (imagenes)

# Groq, nivel gratuito: el modelo de vision admite 1000 tokens de salida por minuto, y una
# peticion con un tope mayor se rechaza entera. Una lectura detallada ocupa unos 400-600.
IMAGE_MAX_TOKENS = 900
MAX_RATE_WAIT_S = 20.0  # si el proveedor pide esperar mas, se avisa en vez de esperar


class LLMError(Exception):
    """Error legible para la UI; `status` es el codigo HTTP cuando lo hubo."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class ModelInfo:
    id: str
    vision: bool | None  # None: el proveedor no dice que entradas admite


def _error(response: httpx.Response) -> LLMError:
    return LLMError(_explain(response), response.status_code)


def _retry_after(response: httpx.Response) -> float | None:
    """Segundos que pide esperar un 429 (cabecera Retry-After o "try again in 3.48s")."""
    header = response.headers.get("retry-after")
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    match = re.search(r"try again in ([\d.]+)s", response.text)
    return float(match.group(1)) if match else None


def _explain(response: httpx.Response) -> str:
    try:
        detail = response.json()["error"]["message"]
    except Exception:
        detail = response.text[:200]
    if response.status_code == 401:
        return f"API key rechazada ({detail})"
    if response.status_code == 429:
        return f"Limite de peticiones alcanzado ({detail})"
    return f"HTTP {response.status_code}: {detail}"


def _model_info(item: dict[str, Any]) -> ModelInfo:
    inputs = item.get("input_modalities")
    if inputs is None:  # OpenRouter lo anida en "architecture"
        inputs = (item.get("architecture") or {}).get("input_modalities")
    return ModelInfo(str(item["id"]), None if inputs is None else "image" in inputs)


def pick_vision_model(models: list[ModelInfo], previous: str = "") -> str:
    """Modelo que acepta imagenes para sustituir a `previous`; "" si no hay ninguno.

    Primero uno de la misma familia (qwen/qwen3.6 -> qwen/qwen3.8), que suele ser el relevo
    cuando un proveedor retira un modelo.
    """
    vision = [m.id for m in models if m.vision]
    family = previous.split("/")[0] if "/" in previous else ""
    same = [m for m in vision if family and m.startswith(family + "/")]
    return (same or vision or [""])[0]


def parse_sse(lines: Iterable[str]) -> Iterator[str]:
    """Extrae los trozos de texto de un flujo `data: {...}` de chat completions."""
    for line in lines:
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue
        for choice in event.get("choices", []):
            text = choice.get("delta", {}).get("content")
            if text:
                yield text


class ChatClient:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 60.0) -> None:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._model = model
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=timeout,
            # Reintenta la conexion si el proveedor cerro una del pool entre peticiones.
            transport=httpx.HTTPTransport(retries=2),
        )

    def close(self) -> None:
        self._http.close()

    def _body(self, messages: list[Message], effort: str | None, **extra: Any) -> dict[str, Any]:
        body: dict[str, Any] = {"model": self._model, "messages": messages, **extra}
        # Los modelos gpt-oss razonan antes de responder; en bajo tardan poco y siguen siendo
        # buenos. Otros modelos rechazarian el parametro, asi que solo se manda a estos.
        if effort and "gpt-oss" in self._model:
            body["reasoning_effort"] = effort
        return body

    def list_models(self) -> list[str]:
        """IDs de modelos que ofrece el proveedor (GET /models), ordenados."""
        return [model.id for model in self.catalog()]

    def catalog(self) -> list[ModelInfo]:
        """Modelos del proveedor y si aceptan imagenes (Groq y OpenRouter lo indican)."""
        try:
            response = self._http.get("/models")
        except httpx.HTTPError as exc:
            raise LLMError(f"Sin conexion con el proveedor: {exc}") from exc
        if response.status_code >= 400:
            raise _error(response)
        try:
            return sorted(
                (_model_info(item) for item in response.json()["data"]), key=lambda m: m.id
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise LLMError("Respuesta inesperada al listar modelos") from exc

    def complete(
        self,
        messages: list[Message],
        *,
        max_tokens: int = 1500,
        temperature: float = 0.4,
        json_mode: bool = False,
        effort: str | None = "low",
    ) -> str:
        body = self._body(messages, effort, max_tokens=max_tokens, temperature=temperature)
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            response = self._http.post("/chat/completions", json=body)
        except httpx.HTTPError as exc:
            raise LLMError(f"Sin conexion con el proveedor: {exc}") from exc
        if response.status_code >= 400:
            raise _error(response)
        try:
            return response.json()["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError("Respuesta inesperada del proveedor") from exc

    def stream(
        self,
        messages: list[Message],
        *,
        max_tokens: int = 3000,
        temperature: float = 0.5,
        effort: str | None = "medium",
    ) -> Iterator[str]:
        body = self._body(
            messages, effort, max_tokens=max_tokens, temperature=temperature, stream=True
        )
        try:
            with self._http.stream("POST", "/chat/completions", json=body) as response:
                if response.status_code >= 400:
                    response.read()
                    raise _error(response)
                yield from parse_sse(response.iter_lines())
        except httpx.HTTPError as exc:
            raise LLMError(f"Sin conexion con el proveedor: {exc}") from exc

    def describe_image(self, data_url: str, prompt: str, *, model: str) -> str:
        """Una unica llamada a un modelo con vision; devuelve su lectura de la imagen.

        Un 429 que pide esperar poco se reintenta una vez: en el nivel gratuito basta con que
        otra lectura acabe de gastar el cupo del minuto.
        """
        body = {
            "model": model,
            "max_tokens": IMAGE_MAX_TOKENS,
            "temperature": 0.2,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
        }
        for attempt in range(2):
            try:
                response = self._http.post("/chat/completions", json=body, timeout=90)
            except httpx.HTTPError as exc:
                raise LLMError(f"Sin conexion con el proveedor: {exc}") from exc
            wait = _retry_after(response) if response.status_code == 429 else None
            if attempt == 0 and wait is not None and wait <= MAX_RATE_WAIT_S:
                time.sleep(wait + 0.5)
                continue
            break
        if response.status_code >= 400:
            raise _error(response)
        try:
            return (response.json()["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError("Respuesta inesperada del proveedor") from exc

    def ping(self) -> str:
        """Peticion minima para comprobar URL, key y modelo. Devuelve el texto recibido.

        Margen de tokens holgado: los modelos que razonan (gpt-oss, qwen3) gastan los primeros
        pensando, y con un tope de 5 devolvian una respuesta vacia aunque todo funcionara.
        """
        return self.complete(
            [{"role": "user", "content": "Reply with the single word: ok"}], max_tokens=200
        ).strip()
