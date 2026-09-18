"""Filtro barato: decide si una frase de los demas merece consultar al modelo.

No intenta acertar si la pregunta es para el usuario (eso lo decide el modelo con contexto);
solo descarta lo que claramente no es una pregunta ni una peticion, para no gastar
peticiones ni distraer con sugerencias durante un monologo.
"""

from __future__ import annotations

import re
import unicodedata

_QUESTION_START = re.compile(
    r"^(?:so,?\s+|and,?\s+|but,?\s+|ok(?:ay)?,?\s+|well,?\s+)*"
    r"(?:what|why|how|when|where|which|who|whose|"
    r"can|could|would|will|shall|should|may|might|"
    r"do|does|did|are|is|was|were|have|has|had|any)\b",
    re.IGNORECASE,
)

_REQUEST = re.compile(
    r"\b(?:what do you think|your (?:thoughts|opinion|take|view|update|status|input|side)|"
    r"tell (?:us|me)|walk (?:us|me) through|(?:over|back) to you|go ahead|"
    r"any (?:questions|updates|thoughts|concerns|blockers|objections)|thoughts\?|"
    r"can you|could you|would you|do you|did you|are you|have you|"
    r"let(?:'s| us) hear|what about you|how about you|you(?:'re| are) up|"
    r"up to you|your turn|please share|please explain|please walk)\b",
    re.IGNORECASE,
)


MIN_NAME_LEN = 3  # particulas como "de" o "la" no cuentan como nombre


def _fold(text: str) -> str:
    """Minusculas y sin tildes: Whisper escribe "Jose" o "José" segun le suene."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def name_tokens(user_name: str) -> list[str]:
    """Palabras del nombre completo y apodos ("Ana Maria Ruiz, Anita") que valen como aviso."""
    parts = re.split(r"[\s,;/]+", _fold(user_name))
    return sorted({p for p in parts if len(p) >= MIN_NAME_LEN})


def mentions_user(text: str, user_name: str) -> bool:
    tokens = name_tokens(user_name)
    if not tokens:
        return False
    pattern = r"\b(?:" + "|".join(re.escape(t) for t in tokens) + r")\b"
    return re.search(pattern, _fold(text)) is not None


def looks_like_question(text: str, user_name: str = "") -> bool:
    """Verdadero si la frase termina en interrogacion, empieza como pregunta o pide algo."""
    stripped = text.strip()
    if not stripped:
        return False
    if "?" in stripped:
        return True
    if mentions_user(stripped, user_name):
        return True
    return bool(_QUESTION_START.match(stripped) or _REQUEST.search(stripped))
