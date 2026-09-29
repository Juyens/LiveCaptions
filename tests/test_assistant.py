from __future__ import annotations

import pytest

from live_captions.assistant.brain import _parse_json
from live_captions.assistant.detector import looks_like_question, name_tokens
from live_captions.assistant.llm import parse_sse


@pytest.mark.parametrize(
    "text",
    [
        "What do you think about the timeline?",
        "Joseph, can you walk us through the backend changes",
        "So, are we on track for Friday",
        "Any blockers on your side",
        "Over to you.",
        "I'd love to hear your thoughts",
    ],
)
def test_detects_questions_and_requests(text: str) -> None:
    assert looks_like_question(text)


@pytest.mark.parametrize(
    "text",
    [
        "We shipped the new release yesterday.",
        "The numbers look good this quarter.",
        "Let me share my screen.",
        "",
    ],
)
def test_ignores_statements(text: str) -> None:
    assert not looks_like_question(text)


def test_mentioning_the_user_counts() -> None:
    assert looks_like_question("Joseph will handle the migration.", user_name="Joseph")
    assert not looks_like_question("Josephine will handle the migration.", user_name="Joseph")


def test_any_part_of_the_full_name_or_nickname_counts() -> None:
    name = "Joseph Julius Castillo Barrios, Joe, Pepe"
    assert name_tokens(name) == ["barrios", "castillo", "joe", "joseph", "julius", "pepe"]
    assert looks_like_question("Let's hear from Castillo on this.", user_name=name)
    assert looks_like_question("joe will take that one", user_name=name)
    assert looks_like_question("Pepe already knows the numbers.", user_name=name)
    assert not looks_like_question("The migration will land next week.", user_name=name)


def test_name_matching_ignores_accents_and_short_particles() -> None:
    name = "José de la Cruz"
    assert name_tokens(name) == ["cruz", "jose"]
    assert looks_like_question("Jose, your thoughts", user_name=name)
    assert looks_like_question("Thanks JOSÉ.", user_name=name)
    assert not looks_like_question("Send it to the team.", user_name=name)  # "de"/"la" no cuentan


def test_parse_sse_extracts_deltas_and_skips_noise() -> None:
    lines = [
        ": keep-alive",
        'data: {"choices":[{"delta":{"role":"assistant"}}]}',
        'data: {"choices":[{"delta":{"content":"Hola"}}]}',
        "",
        'data: {"choices":[{"delta":{"content":" mundo"}}]}',
        "data: not-json",
        "data: [DONE]",
    ]
    assert list(parse_sse(lines)) == ["Hola", " mundo"]


def test_parse_json_tolerates_fences_and_prose() -> None:
    assert _parse_json('```json\n{"directed": false}\n```') == {"directed": False}
    assert _parse_json('Sure! {"directed": true, "answers": []} hope it helps') == {
        "directed": True,
        "answers": [],
    }
    assert _parse_json("no json here") is None
    assert _parse_json("[1, 2]") is None


def test_retired_models_are_replaced_on_load(tmp_path) -> None:
    from live_captions.assistant import config
    from live_captions.settings import Settings

    settings = Settings(tmp_path / "s.json", legacy={})
    settings.update({"llm/provider": "groq", "llm/model": "llama-3.3-70b-versatile"})
    assert config.load(settings).model == "openai/gpt-oss-120b"
    settings.set("llm/model", "qwen/qwen3.6-27b")
    assert config.load(settings).model == "qwen/qwen3.6-27b"
