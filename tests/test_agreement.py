"""LocalAgreement: lo confirmado solo crece y solo con dos pasadas que coinciden."""

from __future__ import annotations

from live_captions.agreement import Agreement


def test_first_pass_confirms_nothing() -> None:
    agreement = Agreement()
    assert agreement.update("Good morning everyone") == ("", "Good morning everyone")


def test_confirms_the_common_prefix_except_the_edge_word() -> None:
    agreement = Agreement()
    agreement.update("First item is the good")
    committed, tentative = agreement.update("First item is the Q3 number")
    assert committed == "First item is the"
    assert tentative == "Q3 number"


def test_comparison_ignores_case_and_punctuation() -> None:
    agreement = Agreement()
    agreement.update("Let's get started with the week.")
    committed, _ = agreement.update("let's get started, with the weekly product")
    assert committed == "let's get started, with the"


def test_committed_text_never_shrinks() -> None:
    agreement = Agreement()
    agreement.update("Can you walk us through")
    agreement.update("Can you walk us through the back end")
    committed, tentative = agreement.update("Can you work as true or the backend")
    assert committed == "Can you walk us through"
    # Lo que venga despues se muestra a partir de lo confirmado, aunque discrepe.
    assert tentative == "or the backend"


def test_edge_word_is_confirmed_once_speech_moves_past_it() -> None:
    agreement = Agreement()
    agreement.update("mostly driven by the enterprise")
    agreement.update("mostly driven by the enterprise")
    committed, _ = agreement.update("mostly driven by the enterprise segment")
    assert committed == "mostly driven by the enterprise"


def test_reset_starts_a_new_sentence() -> None:
    agreement = Agreement()
    agreement.update("one two three")
    agreement.update("one two three four")
    agreement.reset()
    assert agreement.update("five six") == ("", "five six")
