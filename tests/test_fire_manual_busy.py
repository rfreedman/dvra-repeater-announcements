from app.fire import handle_fire
from app.radio import StubRadio

from tests.test_fire import _announcement, _ctx, _deps


def test_manual_trigger_defers_when_busy_like_scheduled():
    radio = StubRadio()
    radio.busy = True
    announcement = _announcement()
    deps, extras = _deps(announcement, radio)
    deps.update_schedule = False
    deferred: list = []
    deps.schedule_defer = lambda when, ctx: deferred.append((when, ctx))
    assert handle_fire(_ctx(), deps) == "deferred"
    assert extras["played"] == []
    assert deferred
    assert not any(event == ("ptt", True) for event in radio.events)
