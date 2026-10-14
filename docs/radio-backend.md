# Radio backend protocol

Scheduled and manual fires talk to a **radio backend** selected by `TTS_RADIO_BACKEND`:

- **`stub`** (default) — in-memory PTT/COS for dev and tests ([`StubRadio`](../app/radio.py)).
- **`dra`** — CM119 HID + DRA USB audio on the NUC ([`DraRadio`](../app/dra_radio.py)).

The typed [`Radio`](../app/radio.py) protocol only requires `channel_busy()` and `set_ptt()`. The scheduler, fire path, COS background poll, and UI status also call **optional** methods via `getattr`. A production DRA backend should implement the full extension set below; stub implements enough for tests and Mac UI work.

Helpers such as [`dra_radio_from()`](../app/dra_radio.py) unwrap [`_RunningOnKeyRadio`](../app/fire.py) (manual-trigger wrapper) before DRA-only setup.

## Required (Protocol)

| Method | Semantics |
| --- | --- |
| `channel_busy() -> bool` | True when the repeater input is active (COS / carrier detect). Used for defer, clear hold, and last-chance gating. |
| `set_ptt(keyed: bool) -> None` | Key or unkey PTT. `transmit()` always unkeys in `finally`. |

## Optional extensions

| Method / attribute | Typical backend | Used for |
| --- | --- | --- |
| `continuous_idle_seconds() -> float` | DRA, stub | Seconds of continuous COS idle already accrued (background poll). Lets `channel_blocks_transmit()` skip a HID read once the clear hold is met. |
| `wait_for_transmit_clear(*, clear_seconds, poll_seconds, deadline, now, sleep_fn, fail_fast_on_busy=False) -> bool` | DRA | Block until the channel has been idle for `clear_seconds`, or return False past `deadline`. Manual trigger retries and scheduled fires use this when `TTS_DRA_CLEAR_SECONDS` > 0. |
| `cos_defer_snapshot() -> tuple[bool \| None, float]` | DRA, stub | Last COS busy state and idle credit **without** opening HID (UI / SSE manual-trigger status). |
| `play_transmit_audio(chunks, *, lead_seconds: float) -> None` | DRA | Stream announcement PCM on USB audio (with optional lead silence on USB). When present, [`transmit()`](../app/radio.py) uses this instead of host `play_fn`. |
| `refresh_cos() -> bool` | DRA | One COS sample; updates idle tracking. Called from the background COS poll. |
| `warm_open_hid() -> None` | DRA | Keep CM119 HID open between samples (avoids macOS open/close races; cheap no-op when USB absent). |
| `ptt_keyed` (property) | DRA | True while software believes PTT is keyed. COS poll skips sampling while keyed. |
| `sync_audio_output_indices_if_changed(*, after_usb_event: bool = False) -> bool` | DRA | Refresh PortAudio output indices after USB replug. Called from startup and replug recovery. |

## Errors

| Exception | Meaning |
| --- | --- |
| [`ChannelBusyError`](../app/radio.py) | COS active immediately before PTT; do not key. Fire may defer or drop. |
| [`TransmitAbortError`](../app/radio.py) | Playback stopped early (e.g. DRA USB unplugged mid-transmit). Fire returns dropped / `dropped_usb` for manual triggers. |

## Implementing a new backend

1. Implement at least the Protocol methods.
2. For on-air use with clear hold and Trigger now UX, match **DraRadio**’s optional surface (or document deliberate gaps).
3. Register via [`create_radio_from_config()`](../app/radio.py) / startup in [`app/startup.py`](../app/startup.py).
4. Run the full test suite with `TTS_RADIO_BACKEND=stub`; add targeted tests with fakes where hardware is required.

See also [final hardware](final-hardware/README.md), [NUC notes](NUC-notes.md), and [code review findings — DRA](code-review-findings.md).
