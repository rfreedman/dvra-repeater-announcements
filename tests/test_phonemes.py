"""Phoneme dictionary replacement, storage, and IPA lookup."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.audio import PauseSegment, PcmChunk, parse_script
from app.models import TtsAnnouncement
from app.pcm_cache import fingerprint
from app.phoneme_dict import PhonemeDictionary, get_phoneme_dictionary
from app.phoneme_text import PhonemeTextError, compile_entries, prepare_speech_text
from app.store import AnnouncementStore
from app.tts.ipa import ipa_for_text

_IPA = "dˈɛlɐwˌɛɹ"


def _entries(*rows: tuple[str, str]):
    return compile_entries(rows)


def test_dictionary_replaces_whole_words_longest_phrase_first() -> None:
    entries = _entries(("our", "aʊɚ"), ("hour", "aʊɚ"), ("New Castle", "njuːkæsəl"), ("Castle", "kæsəl"))
    text = prepare_speech_text("Say our hour in New Castle", entries, use_dictionary=True)
    assert text == "Say [[aʊɚ]] [[aʊɚ]] in [[njuːkæsəl]]"
    assert "hour" not in text.split("[[")[0]


def test_our_does_not_match_inside_hour() -> None:
    entries = _entries(("our", "aʊɚ"))
    assert prepare_speech_text("hour", entries, use_dictionary=True) == "hour"


def test_possessive_does_not_use_the_base_word() -> None:
    entries = _entries(("Delaware", _IPA))
    text = prepare_speech_text("Delaware's club", entries, use_dictionary=True)
    assert text == "Delaware's club"
    owned = _entries(("Delaware", _IPA), ("Delaware's", _IPA))
    replaced = prepare_speech_text("Delaware's club", owned, use_dictionary=True)
    assert replaced == f"[[{_IPA}]] club"


def test_case_and_hyphen_are_whole_tokens() -> None:
    entries = _entries(("delaware", _IPA), ("Hertz", "hˈɜːts"))
    text = prepare_speech_text("DELAWARE mega-hertz", entries, use_dictionary=True)
    assert text == f"[[{_IPA}]] mega-hertz"


def test_manual_phonemes_braces_and_pauses_are_left_intact() -> None:
    entries = _entries(("Delaware", _IPA))
    text = prepare_speech_text(
        "[[keep]] {{Delaware}} Delaware [pause:1.5s] tail",
        entries,
        use_dictionary=True,
    )
    assert text == f"[[keep]] Delaware [[{_IPA}]] [pause:1.5s] tail"
    pauses = [segment for segment in parse_script(text) if isinstance(segment, PauseSegment)]
    assert pauses[0].seconds == 1.5


def test_switch_off_unwraps_braces_without_replacing() -> None:
    entries = _entries(("Delaware", _IPA))
    text = prepare_speech_text("{{Delaware}} Delaware", entries, use_dictionary=False)
    assert text == "Delaware Delaware"


def test_piper_text_drops_tie_marks_and_saved_ipa_keeps_them() -> None:
    ipa = "dˈɛlɐwˌɛ\u200dɹ"
    entries = _entries(("Delaware", ipa))
    assert entries[0].ipa == ipa
    spoken = prepare_speech_text("Delaware", entries, use_dictionary=True)
    assert spoken == "[[dˈɛlɐwˌɛɹ]]"
    assert "\u200d" not in spoken
    manual = prepare_speech_text(f"Say [[{ipa}]] please", entries, use_dictionary=False)
    assert manual == "Say [[dˈɛlɐwˌɛɹ]] please"


def test_unbalanced_markup_stays_typed() -> None:
    entries = _entries(("Delaware", _IPA))
    assert prepare_speech_text("{{Delaware", entries, use_dictionary=True) == "{{Delaware"


def test_alternate_text_replaces_the_spelling_as_plain_words() -> None:
    entries = compile_entries([("Association", "", "uh so see AY shun")])
    text = prepare_speech_text("The Association meets", entries, use_dictionary=True)
    assert text == "The uh so see AY shun meets"
    assert "[[" not in text


def test_an_entry_needs_ipa_or_alternate_text_but_not_both() -> None:
    with pytest.raises(PhonemeTextError, match="not both"):
        _ = compile_entries([("Association", _IPA, "uh so")])
    with pytest.raises(PhonemeTextError, match="Enter IPA or alternate text"):
        _ = compile_entries([("Association", "  ", " ")])
    with pytest.raises(PhonemeTextError, match="Alternate text"):
        _ = compile_entries([("Association", "", "[[uh so]]")])


def test_ipa_only_file_without_alternate_keeps_its_hash(tmp_path: Path) -> None:
    path = tmp_path / "phonemes.json"
    _ = path.write_text(
        json.dumps({"version": 1, "entries": [{"spelling": "Delaware", "ipa": _IPA}]}),
        encoding="utf-8",
    )
    dictionary = PhonemeDictionary(path)
    loaded = dictionary.compiled()
    assert loaded[0].ipa == _IPA
    assert loaded[0].alternate == ""
    before = dictionary.content_hash()
    _ = dictionary.replace([("Delaware", _IPA)])
    assert json.loads(path.read_text(encoding="utf-8"))["entries"] == [
        {"spelling": "Delaware", "ipa": _IPA}
    ]
    assert dictionary.content_hash() == before


def test_punctuation_spelling_and_duplicates_are_rejected() -> None:
    with pytest.raises(PhonemeTextError):
        _ = compile_entries([("Delaware,", _IPA)])
    with pytest.raises(PhonemeTextError):
        _ = compile_entries([("Delaware", _IPA), ("delaware", "x")])


def test_load_writes_dictionary_flag_onto_existing_announcements(tmp_path: Path) -> None:
    path = tmp_path / "announcements.json"
    _ = path.write_text(
        json.dumps(
            {
                "version": 2,
                "announcements": [
                    {"id": "abc123abc123", "name": "ID", "kind": "tts", "text": "Hello Delaware"}
                ],
                "schedules": [],
            }
        ),
        encoding="utf-8",
    )
    item = AnnouncementStore(path).get("abc123abc123")
    assert isinstance(item, TtsAnnouncement)
    assert item.use_phoneme_dictionary is True
    assert '"use_phoneme_dictionary": true' in path.read_text(encoding="utf-8")


def test_fingerprint_includes_dictionary_only_when_enabled() -> None:
    on_a = fingerprint("Hello", "v", 1.0, 0.25, None, use_phoneme_dictionary=True, phoneme_dictionary_hash="aaa")
    on_b = fingerprint("Hello", "v", 1.0, 0.25, None, use_phoneme_dictionary=True, phoneme_dictionary_hash="bbb")
    off_a = fingerprint("Hello", "v", 1.0, 0.25, None, use_phoneme_dictionary=False, phoneme_dictionary_hash="aaa")
    off_b = fingerprint("Hello", "v", 1.0, 0.25, None, use_phoneme_dictionary=False, phoneme_dictionary_hash="bbb")
    assert on_a != on_b
    assert off_a == off_b
    assert on_a != off_a


def test_dictionary_api_is_admin_only_for_writes(
    client: TestClient, readonly_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.routes.phonemes.schedule_dictionary_refresh", lambda: None)
    denied = readonly_client.put(
        "/api/phonemes",
        json={"entries": [{"spelling": "Delaware", "ipa": _IPA}]},
    )
    assert denied.status_code == 403
    saved = client.put(
        "/api/phonemes",
        json={"entries": [{"spelling": "  Delaware  ", "ipa": f"  {_IPA}  "}]},
    )
    assert saved.status_code == 200
    assert saved.json()["entries"] == [{"spelling": "Delaware", "ipa": _IPA, "alternate": ""}]
    listed = client.get("/api/phonemes")
    assert listed.json()["entries"] == [{"spelling": "Delaware", "ipa": _IPA, "alternate": ""}]
    duplicate = client.put(
        "/api/phonemes",
        json={
            "entries": [
                {"spelling": "Delaware", "ipa": _IPA},
                {"spelling": "delaware", "ipa": "x"},
            ]
        },
    )
    assert duplicate.status_code == 400
    both = client.put(
        "/api/phonemes",
        json={"entries": [{"spelling": "Association", "ipa": _IPA, "alternate": "uh so"}]},
    )
    assert both.status_code == 400
    neither = client.put(
        "/api/phonemes",
        json={"entries": [{"spelling": "Association"}]},
    )
    assert neither.status_code == 400
    alternate = client.put(
        "/api/phonemes",
        json={"entries": [{"spelling": "Association", "alternate": "  uh so see AY shun  "}]},
    )
    assert alternate.status_code == 200
    assert alternate.json()["entries"] == [
        {"spelling": "Association", "ipa": "", "alternate": "uh so see AY shun"}
    ]
    saved_text = get_phoneme_dictionary().path.read_text(encoding="utf-8")
    assert '"alternate": "uh so see AY shun"' in saved_text
    assert '"ipa": ""' in saved_text


def test_speak_applies_dictionary(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _ = get_phoneme_dictionary().replace([("Delaware", _IPA)])
    seen: list[str] = []

    def fake_synthesize(
        text: str,
        voice: str | None = None,
        speed: float = 1.0,
        sentence_pause: float = 0.25,
    ) -> tuple[str, int, list[PcmChunk]]:
        _ = voice
        _ = speed
        _ = sentence_pause
        seen.append(text)
        return "en_US-lessac-medium", 22050, [PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)]

    monkeypatch.setattr("app.routes.speak.registry.synthesize", fake_synthesize)
    on = client.post("/api/speak", json={"text": "Hello Delaware", "use_phoneme_dictionary": True})
    assert on.status_code == 200
    off = client.post("/api/speak", json={"text": "Hello Delaware", "use_phoneme_dictionary": False})
    assert off.status_code == 200
    assert seen == [f"Hello [[{_IPA}]]", "Hello Delaware"]


def test_speak_applies_alternate_text(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _ = get_phoneme_dictionary().replace([("Association", "", "uh so see AY shun")])
    seen: list[str] = []

    def fake_synthesize(
        text: str,
        voice: str | None = None,
        speed: float = 1.0,
        sentence_pause: float = 0.25,
    ) -> tuple[str, int, list[PcmChunk]]:
        _ = voice
        _ = speed
        _ = sentence_pause
        seen.append(text)
        return "en_US-lessac-medium", 22050, [PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)]

    monkeypatch.setattr("app.routes.speak.registry.synthesize", fake_synthesize)
    res = client.post("/api/speak", json={"text": "The Association meets", "use_phoneme_dictionary": True})
    assert res.status_code == 200
    assert seen == ["The uh so see AY shun meets"]


def test_matches_skip_protected_spans(client: TestClient) -> None:
    _ = get_phoneme_dictionary().replace([("Delaware", _IPA)])
    res = client.post(
        "/api/phonemes/matches",
        json={"text": "{{Delaware}} [[Delaware]] Delaware"},
    )
    assert res.status_code == 200
    assert res.json()["entries"] == [{"spelling": "Delaware", "ipa": _IPA, "alternate": ""}]


def test_ipa_for_delaware_includes_a_tie_mark() -> None:
    ipa = ipa_for_text("Delaware")
    assert "ɛ" in ipa
    assert "ɹ" in ipa
    assert "\u200d" in ipa


def test_ipa_for_our_uses_primary_stress() -> None:
    """A lone function word is stressed, matching espeak-ng --ipa=3."""
    assert ipa_for_text("our") == "ˈa\u200dʊɚ"
    assert ipa_for_text("the") == "ðˈə"
    assert ipa_for_text("to") == "tˈuː"


@pytest.mark.skipif(shutil.which("espeak-ng") is None, reason="espeak-ng is not installed")
def test_ipa_matches_espeak_ng_cli() -> None:
    for word in ("our", "the", "to", "hour", "Delaware", "Hello. World."):
        completed = subprocess.run(
            ["espeak-ng", "-v", "en-us", "--ipa=3", "-q", word],
            check=True,
            capture_output=True,
            text=True,
        )
        assert ipa_for_text(word) == completed.stdout.strip()
