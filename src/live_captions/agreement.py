"""Estabiliza los borradores de Whisper con LocalAgreement.

Cada pocos cientos de ms se retranscribe la frase en curso y cada pasada puede cambiar las
ultimas palabras. Una palabra se da por buena (se "confirma") cuando dos pasadas seguidas
coinciden en ella y en todo lo anterior; lo confirmado ya no cambia hasta la frase final, asi
que el texto crece por la derecha en vez de parpadear.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_NOT_WORD = re.compile(r"[^\w%$€£']+")


def _key(word: str) -> str:
    """Forma de comparar: sin mayusculas ni puntuacion ("Numbers." == "numbers")."""
    return _NOT_WORD.sub("", word.lower())


@dataclass
class Agreement:
    committed: list[str] = field(default_factory=list)
    _previous: list[str] = field(default_factory=list)

    def update(self, hypothesis: str) -> tuple[str, str]:
        """Anade una pasada y devuelve (texto confirmado, resto provisional)."""
        words = hypothesis.split()
        agreed = 0
        limit = min(len(words), len(self._previous))
        while agreed < limit and _key(words[agreed]) == _key(self._previous[agreed]):
            agreed += 1
        # La ultima palabra de la pasada esta pegada al borde del audio y puede estar cortada
        # ("migraines" por "migration..."): no se confirma aunque coincida.
        agreed = min(agreed, len(words) - 1)
        if agreed > len(self.committed):
            self.committed = words[:agreed]
        self._previous = words
        tentative = words[len(self.committed) :]
        return " ".join(self.committed), " ".join(tentative)

    def reset(self) -> None:
        self.committed = []
        self._previous = []
