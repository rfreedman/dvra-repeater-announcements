"""IPA strings equivalent to ``espeak-ng -v en-us --ipa=3``.

Uses the espeak-ng library linked into Piper's ``espeakbridge`` extension, so
production does not need the ``espeak-ng`` executable. ``--ipa=3`` is IPA plus
the tie bit, with U+200D (zero-width joiner) in the high byte of the mode word.

The command prints phonemes from the synthesis trace (``espeak_SetPhonemeTrace``
then ``espeak_Synth``). ``espeak_TextToPhonemes`` is a different path: a lone
function word such as "our" stays unstressed there (``ˌ``) and is stressed in
the command's output (``ˈ``).
"""

from __future__ import annotations

import ctypes
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from app.tts.base import VoiceError

# espeakPHONEMES_IPA | espeakPHONEMES_TIE | (U+200D << 8)
_IPA3_MODE = 0x02 | 0x80 | (0x200D << 8)
_ESPEAK_CHARS_UTF8 = 1
_POS_CHARACTER = 1


class _SetPhonemeTrace(Protocol):
    argtypes: list[object]
    restype: object

    def __call__(self, phonememode: int, stream: object) -> None: ...


class _Synth(Protocol):
    argtypes: list[object]
    restype: object

    def __call__(
        self,
        text: bytes,
        size: int,
        position: int,
        position_type: int,
        end_position: int,
        flags: int,
        unique_identifier: object,
        user_data: object,
    ) -> int: ...


class _Synchronize(Protocol):
    restype: object

    def __call__(self) -> int: ...


class _SetSynthCallback(Protocol):
    argtypes: list[object]
    restype: object

    def __call__(self, callback: object) -> None: ...


class _FileOp(Protocol):
    argtypes: list[object]
    restype: object

    def __call__(self, stream: object) -> int: ...


class _Fopen(Protocol):
    argtypes: list[object]
    restype: object

    def __call__(self, path: bytes, mode: bytes) -> object: ...


@dataclass
class _Espeak:
    set_trace: _SetPhonemeTrace
    synth: _Synth
    synchronize: _Synchronize
    set_callback: _SetSynthCallback


@dataclass
class _Libc:
    fopen: _Fopen
    fflush: _FileOp
    fclose: _FileOp


_espeak: _Espeak | None = None
_libc: _Libc | None = None
_ready = False


def _discard_audio(_wav: object, _numsamples: int, _events: object) -> int:
    return 0


_AUDIO_CALLBACK = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p)
_discard_callback = _AUDIO_CALLBACK(_discard_audio)


def ipa_for_text(text: str) -> str:
    """Return the ``en-us --ipa=3`` pronunciation of ``text``, including tie marks."""
    cleaned = text.strip()
    if not cleaned:
        raise VoiceError("Text is required")
    from piper import espeakbridge
    from piper.phonemize_espeak import ESPEAK_DATA_DIR
    from piper.voice import _ESPEAK_PHONEMIZER_LOCK  # pyright: ignore[reportPrivateUsage]

    with _ESPEAK_PHONEMIZER_LOCK:
        global _ready
        if not _ready:
            espeakbridge.initialize(str(ESPEAK_DATA_DIR))
            _ready = True
        espeakbridge.set_voice("en-us")
        phonemes = _trace_ipa(cleaned)
    if not phonemes:
        raise VoiceError("No pronunciation for that text")
    return phonemes


def _espeak_api() -> _Espeak:
    global _espeak
    if _espeak is None:
        import piper.espeakbridge as bridge

        loaded = ctypes.CDLL(bridge.__file__)
        set_trace = cast(_SetPhonemeTrace, getattr(loaded, "espeak_SetPhonemeTrace"))
        set_trace.argtypes = [ctypes.c_int, ctypes.c_void_p]
        set_trace.restype = None
        synth = cast(_Synth, getattr(loaded, "espeak_Synth"))
        synth.argtypes = [
            ctypes.c_char_p,
            ctypes.c_size_t,
            ctypes.c_uint,
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        synth.restype = ctypes.c_int
        synchronize = cast(_Synchronize, getattr(loaded, "espeak_Synchronize"))
        synchronize.restype = ctypes.c_int
        set_callback = cast(_SetSynthCallback, getattr(loaded, "espeak_SetSynthCallback"))
        set_callback.argtypes = [ctypes.c_void_p]
        set_callback.restype = None
        _espeak = _Espeak(set_trace, synth, synchronize, set_callback)
    return _espeak


def _libc_api() -> _Libc:
    global _libc
    if _libc is None:
        # The process C library. find_library("c") fails after DYLD_FALLBACK_LIBRARY_PATH
        # is set to Homebrew's lib directory, which this app does for hidapi.
        loaded = ctypes.CDLL(None)
        fopen = cast(_Fopen, getattr(loaded, "fopen"))
        fopen.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        fopen.restype = ctypes.c_void_p
        fflush = cast(_FileOp, getattr(loaded, "fflush"))
        fflush.argtypes = [ctypes.c_void_p]
        fflush.restype = ctypes.c_int
        fclose = cast(_FileOp, getattr(loaded, "fclose"))
        fclose.argtypes = [ctypes.c_void_p]
        fclose.restype = ctypes.c_int
        _libc = _Libc(fopen, fflush, fclose)
    return _libc


def _trace_ipa(text: str) -> str:
    """Phonemes written by the same trace the espeak-ng command prints."""
    espeak = _espeak_api()
    libc = _libc_api()
    descriptor, name = tempfile.mkstemp(prefix="dvra-ipa-")
    os.close(descriptor)
    path = Path(name)
    stream = libc.fopen(str(path).encode(), b"wb")
    if not stream:
        path.unlink(missing_ok=True)
        raise VoiceError("No pronunciation for that text")
    status = -1
    try:
        espeak.set_callback(ctypes.cast(_discard_callback, ctypes.c_void_p))
        espeak.set_trace(_IPA3_MODE, stream)
        raw = text.encode("utf-8")
        status = espeak.synth(raw, len(raw) + 1, 0, _POS_CHARACTER, 0, _ESPEAK_CHARS_UTF8, None, None)
        _ = espeak.synchronize()
    finally:
        espeak.set_trace(0, None)
        espeak.set_callback(None)
        _ = libc.fflush(stream)
        _ = libc.fclose(stream)
    try:
        written = path.read_bytes()
    finally:
        path.unlink(missing_ok=True)
    if status != 0:
        raise VoiceError("No pronunciation for that text")
    return written.decode("utf-8").strip()
