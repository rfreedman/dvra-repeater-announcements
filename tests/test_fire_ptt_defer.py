"""PTT/key failures must defer like RF busy, not drop when COS is clear."""
from __future__ import annotations

from app.fire import handle_fire
from app.radio import ChannelBusyError, StubRadio

from tests.test_fire import _announcement, _ctx, _deps


class PttFailsWhenClear(StubRadio):
    def set_ptt(self, keyed: bool) -> None:
        if keyed:
            raise ChannelBusyError("PTT HID unavailable")
        super().set_ptt(keyed)


def test_ptt_failure_defers_when_channel_clear():
    radio = PttFailsWhenClear()
    radio.busy = False
    announcement = _announcement()
    deps, extras = _deps(announcement, radio)
    deps.dra_clear_seconds = 0
    assert handle_fire(_ctx(), deps) == "deferred"
    assert extras["deferred"]
    assert not any(event == ("ptt", True) for event in radio.events)
