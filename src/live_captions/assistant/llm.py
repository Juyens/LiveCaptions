"""Cliente minimo del protocolo de chat de OpenAI (Groq, Cerebras, NVIDIA, llama-server...)."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from typing import Any

import httpx

Message = dict[str, Any]  # el contenido puede ser texto o una lista de bloques (imagenes)


class LLMError(Exception):
    """Error legible para la UI."""


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
        try:
            response = self._http.get("/models")
        except httpx.HTTPError as exc:
            raise LLMError(f"Sin conexion con el proveedor: {exc}") from exc
        if response.status_code >= 400:
            raise LLMError(_explain(response))
        try:
            return sorted(str(m["id"]) for m in response.json()["data"])
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
            raise LLMError(_explain(response))
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
                    raise LLMError(_explain(response))
                yield from parse_sse(response.iter_lines())
        except httpx.HTTPError as exc:
            raise LLMError(f"Sin conexion con el proveedor: {exc}") from exc

    def describe_image(self, data_url: str, prompt: str, *, model: str) -> str:
        """Una unica llamada a un modelo con vision; devuelve su lectura de la imagen."""
        body = {
            "model": model,
            "max_tokens": 1500,
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
        try:
            response = self._http.post("/chat/completions", json=body, timeout=90)
        except httpx.HTTPError as exc:
            raise LLMError(f"Sin conexion con el proveedor: {exc}") from exc
        if response.status_code >= 400:
            raise LLMError(_explain(response))
        try:
            return (response.json()["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError("Respuesta inesperada del proveedor") from exc

    def ping(self) -> str:
        """Peticion minima para comprobar URL, key y modelo. Devuelve el texto recibido."""
        return self.complete(
            [{"role": "user", "content": "Reply with the single word: ok"}], max_tokens=5
        ).strip()
