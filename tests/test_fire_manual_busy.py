from datetime import datetime

from app.fire import handle_fire
from app.fire_types import FireContext
from app.radio import StubRadio

from tests.fire_helpers import make_announcement, make_ctx, make_deps


def test_manual_trigger_defers_when_busy_like_scheduled() -> None:
    radio = StubRadio()
    radio.busy = True
    announcement = make_announcement()
    deps, extras = make_deps(announcement, radio)
    deps.update_schedule = False
    deferred: list[tuple[datetime, FireContext]] = []
    deps.schedule_defer = lambda when, ctx: deferred.append((when, ctx))
    assert handle_fire(make_ctx(), deps) == "deferred"
    assert extras["played"] == []
    assert deferred
    assert not any(event == ("ptt", True) for event in radio.events)
