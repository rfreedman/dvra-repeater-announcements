"""Missing-phoneme logs name the character and the plaintext word."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from piper.phoneme_ids import phonemes_to_ids

from app.phoneme_dict import get_phoneme_dictionary
from app.tts.phoneme_warnings import describe_phoneme, missing_phoneme_context
from app.tts.piper import PiperEngine

_IPA = "dˈɛlɐwˌɛ\u200dɹ"
_TIE = "\u200d"


def test_invisible_phoneme_is_described_by_code_point() -> None:
    described = describe_phoneme(_TIE)
    assert "U+200D" in described
    assert "ZERO WIDTH JOINER" in described
    assert _TIE not in described


def test_visible_phoneme_keeps_the_character() -> None:
    assert describe_phoneme("q") == "q (U+0071 LATIN SMALL LETTER Q)"


def test_piper_warning_names_tie_mark_and_dictionary_word(caplog: pytest.LogCaptureFixture) -> None:
    _ = get_phoneme_dictionary().replace([("Delaware", _IPA)])
    with caplog.at_level(logging.WARNING, logger="piper.phoneme_ids"):
        with missing_phoneme_context(f"[[{_IPA}]]", phonemize_word=None):
            _ = phonemes_to_ids([_TIE, _TIE])
    messages = [record.getMessage() for record in caplog.records]
    assert messages == [
        'Missing phoneme from id map: U+200D ZERO WIDTH JOINER; plaintext "Delaware"'
    ]


def test_unsaved_ipa_is_logged_when_no_dictionary_word_matches(
    caplog: pytest.LogCaptureFixture,
) -> None:
    called: list[str] = []

    def phonemize_word(word: str) -> list[str]:
        called.append(word)
        return []

    with caplog.at_level(logging.WARNING, logger="piper.phoneme_ids"):
        with missing_phoneme_context(f"[[{_IPA}]]", phonemize_word):
            _ = phonemes_to_ids([_TIE])
    assert called == []
    message = caplog.records[-1].getMessage()
    assert "U+200D ZERO WIDTH JOINER" in message
    assert "unknown for phonemes" in message
    assert r"\u200d" in message


def test_plain_word_is_named_when_espeak_emits_the_phoneme(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def phonemize_word(word: str) -> list[str]:
        return [_TIE] if word == "Delaware" else ["d"]

    with caplog.at_level(logging.WARNING, logger="piper.phoneme_ids"):
        with missing_phoneme_context("Say Delaware today", phonemize_word):
            _ = phonemes_to_ids([_TIE])
    message = caplog.records[-1].getMessage()
    assert message == 'Missing phoneme from id map: U+200D ZERO WIDTH JOINER; plaintext "Delaware"'


def test_synthesize_uses_the_dictionary_word(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _ = get_phoneme_dictionary().replace([("Delaware", _IPA)])

    class _Voice:
        config: SimpleNamespace = SimpleNamespace(sample_rate=22050)

        def synthesize(self, text: str, syn_config: object = None) -> Iterator[SimpleNamespace]:
            _ = text, syn_config
            _ = phonemes_to_ids([_TIE])
            yield SimpleNamespace(
                audio_int16_bytes=b"\x00\x00",
                sample_rate=22050,
                sample_width=2,
                sample_channels=1,
            )

        def phonemize(self, word: str) -> list[list[str]]:
            raise AssertionError(word)

    engine = PiperEngine(voices_dir=tmp_path)
    voice = _Voice()

    def load_voice(voice_id: str) -> _Voice:
        _ = voice_id
        return voice

    monkeypatch.setattr(engine, "_load", load_voice)
    with caplog.at_level(logging.WARNING, logger="piper.phoneme_ids"):
        _ = list(engine.synthesize(f"[[{_IPA}]]", voice="en_US-test-medium"))
    assert caplog.records[-1].getMessage() == (
        'Missing phoneme from id map: U+200D ZERO WIDTH JOINER; plaintext "Delaware"'
    )
