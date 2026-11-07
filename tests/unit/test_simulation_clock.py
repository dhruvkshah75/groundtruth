"""Deterministic checks for runtime wall-clock accounting."""

import pytest

from src.web.simulation_clock import SimulationClock


@pytest.mark.parametrize(("rate", "expected"), [(0.25, 2), (0.5, 5), (1.0, 10)])
def test_rate_and_paused_time(rate, expected):
    now = [0.0]
    clock = SimulationClock(step_ms=100, rate=rate, now=lambda: now[0])
    now[0] = 100
    clock.play()
    now[0] += 1
    assert clock.pump(lambda: True, max_ticks=20) == expected
    clock.pause()
    now[0] += 100
    clock.play()
    assert clock.pump(lambda: True, max_ticks=20) == 0


def test_rate_change_and_bounded_debt():
    now = [0.0]
    clock = SimulationClock(step_ms=100, rate=0.5, now=lambda: now[0])
    clock.play()
    now[0] = 0.1
    clock.set_rate(1.0)
    now[0] = 0.25
    assert clock.pump(lambda: True, max_ticks=1) == 1
    assert clock.pump(lambda: True, max_ticks=1) == 1
    assert clock.backlog_ms == pytest.approx(0)


def test_completion_discards_debt_and_backwards_time_never_reverses():
    now = [5.0]
    clock = SimulationClock(step_ms=100, rate=1, now=lambda: now[0])
    clock.play()
    now[0] = 4
    assert clock.pump(lambda: True, max_ticks=10) == 0
    now[0] = 6
    assert clock.pump(lambda: False, max_ticks=10) == 1
    assert clock.backlog_ms == 0
    assert not clock.running
