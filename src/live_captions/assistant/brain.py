"""Cerebro del asistente: contexto + transcripcion reciente -> sugerencias y chat.

Un unico hilo habla con el proveedor. Las peticiones de sugerencia se coalescen: si mientras
se procesa una llegan dos preguntas mas, solo se atiende la ultima (las anteriores ya
pasaron). Las del chat nunca se descartan.
"""

from __future__ import annotations

import json
import logging
import queue
import re
import threading
from collections import deque
from dataclasses import dataclass, field

from live_captions.assistant.config import LLMConfig
from live_captions.assistant.detector import looks_like_question
from live_captions.assistant.llm import ChatClient, LLMError, Message
from live_captions.events import Signal
from live_captions.storage import format_clock

log = logging.getLogger(__name__)

TRANSCRIPT_LINES = 40  # ventana de transcripcion que ve el modelo
HISTORY_TURNS = 12  # turnos de chat que se conservan (pares usuario/asistente)

SYSTEM = """\
You are a real-time meeting copilot for {name}, a Spanish speaker attending a meeting held in \
English. You read a live transcript captured on their computer:
- Lines marked "Them" are the other participants (captured from the speakers).
- Lines marked "Me" are {name} speaking (captured from the microphone).
Whisper transcribes the audio, so expect occasional misheard words; infer the intended meaning.

Context provided by {name} about this meeting (may be empty):
{context}
{images}"""

IMAGES = """
Reference material {name} shared as images, already read by a vision model. Treat it as \
reliable context and use it when it is relevant to what is being discussed:
{items}
"""

DESCRIBE = """\
You are helping a Spanish speaker follow a meeting held in English. Read this image carefully \
and write, in Spanish, everything a text-only assistant would need to use it as context: \
transcribe any text, numbers, table cells, code and labels exactly; describe charts with their \
trends and values; describe diagrams, screenshots and UI by their structure. Be exhaustive and \
literal, do not summarize away details, do not add opinions.
{note}"""

SUGGEST = """\
Recent transcript (oldest first, most recent last):
{transcript}

The most recent "Them" line may be a question or request. Decide whether it is directed at Me \
(explicitly, or because Me is the natural person to answer given the context) and needs a \
spoken reply now.

Respond with JSON only, no prose:
- If it is not directed at Me, or needs no reply: {{"directed": false}}
- Otherwise: {{"directed": true, "question_es": "<the question paraphrased in Spanish>", \
"answers": [{{"en": "<natural spoken reply, 1-2 sentences, first person>", "es": "<Spanish \
gloss>"}}, ...]}} with 2 or 3 answers that differ in stance (e.g. direct answer, cautious \
answer, ask for clarification). Use the context to fill in real facts; if the facts are not in \
the context, keep replies honest and non-committal rather than inventing specifics.
"""

CHAT = """\
Recent transcript (oldest first, most recent last):
{transcript}

{name} asks you: {question}

Answer in Spanish unless asked otherwise. Be concise and concrete. If they ask what to say, \
give the English wording ready to speak, with a short Spanish gloss.
"""

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


@dataclass
class Suggestion:
    seconds: float
    question: str
    question_es: str
    answers: list[dict[str, str]] = field(default_factory=list)


@dataclass
class ImageNote:
    id: int
    note: str  # lo que dijo el usuario que era
    description: str = ""  # lo que leyo el modelo de vision


class Assistant:
    def __init__(self) -> None:
        self.suggestion = Signal()  # (Suggestion)
        self.image_read = Signal()  # (ImageNote ya descrita)
        self.chat_started = Signal()  # (pregunta del usuario)
        self.chat_delta = Signal()  # (trozo de respuesta)
        self.chat_done = Signal()  # (respuesta completa)
        self.status = Signal()  # (texto)
        self.error = Signal()  # (mensaje)

        self._config = LLMConfig()
        self._client: ChatClient | None = None
        self._context = ""
        self._transcript: deque[tuple[str, float, str]] = deque(maxlen=400)
        self._history: list[Message] = []
        self._images: dict[int, ImageNote] = {}
        self._next_image = 0
        self._jobs: queue.Queue[tuple[str, object]] = queue.Queue()
        self._latest_question = 0
        self._lock = threading.Lock()
        threading.Thread(target=self._work, name="assistant", daemon=True).start()

    # -- configuracion y estado --------------------------------------------------------

    @property
    def is_configured(self) -> bool:
        return self._client is not None

    def configure(self, config: LLMConfig) -> None:
        with self._lock:
            if self._client is not None:
                self._client.close()
            self._config = config
            self._client = (
                ChatClient(config.base_url, config.api_key, config.model)
                if config.is_usable
                else None
            )

    @property
    def has_vision(self) -> bool:
        return self._client is not None and self._config.has_vision

    def set_context(self, text: str) -> None:
        self._context = text.strip()

    def attach_image(self, data_url: str, note: str) -> ImageNote | None:
        """Encola la lectura de una imagen; devuelve la nota (aun sin descripcion)."""
        if self._client is None:
            self.error.emit("Configura el proveedor del asistente (Ajustes).")
            return None
        if not self._config.has_vision:
            self.error.emit("Este proveedor no tiene modelo de vision configurado (Ajustes).")
            return None
        self._next_image += 1
        image = ImageNote(self._next_image, note.strip())
        self._images[image.id] = image
        self._jobs.put(("image", (image.id, data_url)))
        return image

    def remove_image(self, image_id: int) -> None:
        self._images.pop(image_id, None)

    def clear_images(self) -> None:
        self._images.clear()

    def reset(self) -> None:
        """Nueva sesion: olvida transcripcion y chat, conserva contexto y configuracion."""
        self._transcript.clear()
        self._history.clear()

    # -- entradas ----------------------------------------------------------------------

    def on_line(self, speaker: str, seconds: float, text: str) -> None:
        self._transcript.append((speaker, seconds, text))
        if speaker != "them" or self._client is None:
            return
        if looks_like_question(text, self._config.user_name):
            self._latest_question += 1
            self._jobs.put(("suggest", (self._latest_question, seconds, text)))

    def ask(self, question: str) -> None:
        question = question.strip()
        if not question:
            return
        if self._client is None:
            self.error.emit("Configura el proveedor del asistente (icono de ajustes).")
            return
        self._jobs.put(("chat", question))

    # -- trabajo -----------------------------------------------------------------------

    def _name(self) -> str:
        return self._config.user_name or "the user"

    def _system(self) -> Message:
        read = [i for i in self._images.values() if i.description]
        images = ""
        if read:
            items = "\n".join(
                f"[Image {i.id}] {self._name()} says: {i.note or '(no note)'}\n"
                f"Content: {i.description}"
                for i in read
            )
            images = IMAGES.format(name=self._name(), items=items)
        return {
            "role": "system",
            "content": SYSTEM.format(
                name=self._name(), context=self._context or "(none)", images=images
            ),
        }

    def _transcript_text(self) -> str:
        lines = list(self._transcript)[-TRANSCRIPT_LINES:]
        if not lines:
            return "(empty so far)"
        label = {"them": "Them", "me": "Me"}
        return "\n".join(f"[{format_clock(s)}] {label.get(sp, sp)}: {t}" for sp, s, t in lines)

    def _work(self) -> None:
        while True:
            kind, payload = self._jobs.get()
            client = self._client
            if client is None:
                continue
            try:
                if kind == "suggest":
                    self._suggest(client, payload)  # type: ignore[arg-type]
                elif kind == "image":
                    self._describe(client, payload)  # type: ignore[arg-type]
                else:
                    self._chat(client, str(payload))
            except LLMError as exc:
                self.error.emit(str(exc))
            except Exception:
                log.exception("Fallo en el asistente")
                self.error.emit("Fallo inesperado en el asistente (ver log).")

    def _suggest(self, client: ChatClient, job: tuple[int, float, str]) -> None:
        job_id, seconds, question = job
        if job_id != self._latest_question:
            return  # ya hay una pregunta mas reciente en cola
        self.status.emit("Pensando una respuesta...")
        raw = client.complete(
            [
                self._system(),
                {"role": "user", "content": SUGGEST.format(transcript=self._transcript_text())},
            ],
            json_mode=True,
            temperature=0.3,
        )
        self.status.emit("")
        data = _parse_json(raw)
        if not data or not data.get("directed"):
            return
        answers = [
            {"en": str(a.get("en", "")).strip(), "es": str(a.get("es", "")).strip()}
            for a in data.get("answers", [])
            if isinstance(a, dict) and a.get("en")
        ]
        if not answers:
            return
        self.suggestion.emit(
            Suggestion(seconds, question, str(data.get("question_es", "")).strip(), answers)
        )

    def _describe(self, client: ChatClient, job: tuple[int, str]) -> None:
        image_id, data_url = job
        image = self._images.get(image_id)
        if image is None:
            return  # el usuario la quito antes de que se leyera
        self.status.emit("Leyendo la imagen...")
        note = f"The user says the image is about: {image.note}" if image.note else ""
        try:
            image.description = client.describe_image(
                data_url, DESCRIBE.format(note=note), model=self._config.vision_model
            )
        finally:
            self.status.emit("")
        if not image.description:
            self._images.pop(image_id, None)
            self.error.emit("El modelo de vision no devolvio nada para la imagen.")
            return
        self.image_read.emit(image)

    def _chat(self, client: ChatClient, question: str) -> None:
        self.chat_started.emit(question)
        prompt = CHAT.format(
            transcript=self._transcript_text(), name=self._name(), question=question
        )
        messages = [self._system(), *self._history, {"role": "user", "content": prompt}]
        chunks: list[str] = []
        for delta in client.stream(messages):
            chunks.append(delta)
            self.chat_delta.emit(delta)
        answer = "".join(chunks).strip()
        # En el historial va la pregunta sin la transcripcion, para no arrastrar tokens.
        self._history.extend(
            [{"role": "user", "content": question}, {"role": "assistant", "content": answer}]
        )
        del self._history[: -2 * HISTORY_TURNS]
        self.chat_done.emit(answer)


def _parse_json(raw: str) -> dict | None:
    text = _FENCE.sub("", raw.strip())
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Algunos modelos rodean el JSON de prosa: quedarse con el primer objeto.
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            log.warning("Respuesta no JSON del modelo: %r", raw[:200])
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            log.warning("Respuesta no JSON del modelo: %r", raw[:200])
            return None
    return data if isinstance(data, dict) else None
