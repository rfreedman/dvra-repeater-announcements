"""PTT/key failures must defer like RF busy, not drop when COS is clear."""
from __future__ import annotations

from typing import override

from app.fire import handle_fire
from app.radio import ChannelBusyError, StubRadio

from tests.fire_helpers import make_announcement, make_ctx, make_deps


class PttFailsWhenClear(StubRadio):
    @override
    def set_ptt(self, keyed: bool) -> None:
        if keyed:
            raise ChannelBusyError("PTT HID unavailable")
        super().set_ptt(keyed)


def test_ptt_failure_defers_when_channel_clear() -> None:
    radio = PttFailsWhenClear()
    radio.busy = False
    announcement = make_announcement()
    deps, extras = make_deps(announcement, radio)
    deps.dra_clear_seconds = 0
    assert handle_fire(make_ctx(), deps) == "deferred"
    assert extras["deferred"]
    assert not any(event == ("ptt", True) for event in radio.events)
