from app.fire import defer_retry_delay
from tests.test_fire import _announcement


class IdleRadio:
    def __init__(self, idle: float) -> None:
        self._idle = idle

    def continuous_idle_seconds(self) -> float:
        return self._idle


def test_short_retry_when_clear_hold_met():
    ann = _announcement()
    ann = ann.model_copy(update={"busy_retry_seconds": 30.0})
    delay = defer_retry_delay(
        IdleRadio(12.0),
        ann,
        kind="ptt",
        clear_seconds=10.0,
    )
    assert delay == 1.0


def test_full_retry_when_clear_hold_disabled():
    ann = _announcement().model_copy(update={"busy_retry_seconds": 30.0})
    assert defer_retry_delay(IdleRadio(0.0), ann, kind="busy", clear_seconds=0) == 30.0


def test_full_retry_when_still_waiting_for_clear():
    ann = _announcement()
    ann = ann.model_copy(update={"busy_retry_seconds": 30.0})
    delay = defer_retry_delay(
        IdleRadio(2.0),
        ann,
        kind="busy",
        clear_seconds=10.0,
    )
    assert delay == 30.0
