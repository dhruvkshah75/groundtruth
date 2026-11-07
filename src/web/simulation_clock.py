"""Wall-clock accounting for a manually pumped simulation runtime.

This is only the clock primitive: callers own lifecycle, synchronization and ticks.
"""

from __future__ import annotations

from collections.abc import Callable
from math import isfinite
from time import monotonic


class SimulationClock:
    """Accumulate simulation milliseconds without losing unprocessed ticks."""

    def __init__(self, *, step_ms: int, rate: float, now: Callable[[], float] = monotonic):
        if step_ms <= 0 or isinstance(rate, bool) or rate not in (0.25, 0.5, 1.0):
            raise ValueError("invalid simulation step or playback rate")
        self.step_ms = step_ms
        self.rate = rate
        self._now = now
        self._anchor_ns = round(now() * 1_000_000_000)
        self._debt_ms = 0.0
        self.running = False

    def _account(self) -> None:
        current_ns = round(self._now() * 1_000_000_000)
        elapsed_ns = max(0, current_ns - self._anchor_ns)
        self._anchor_ns = max(self._anchor_ns, current_ns)
        if self.running:
            self._debt_ms += elapsed_ns * self.rate / 1_000_000

    def play(self) -> None:
        self._account()
        self.running = True

    def pause(self) -> None:
        self._account()
        self.running = False

    def set_rate(self, rate: float) -> None:
        if isinstance(rate, bool) or rate not in (0.25, 0.5, 1.0):
            raise ValueError("unsupported playback rate")
        self._account()
        self.rate = rate

    def pump(self, tick: Callable[[], bool], *, max_ticks: int) -> int:
        """Run bounded ticks; tick returns False when the run has completed."""
        if max_ticks <= 0:
            raise ValueError("max_ticks must be positive")
        self._account()
        count = 0
        while self.running and self._debt_ms >= self.step_ms and count < max_ticks:
            keep_running = tick()
            self._debt_ms -= self.step_ms
            count += 1
            if not keep_running:
                self.running = False
                self._debt_ms = 0.0
        return count

    def restore_backlog(self, value: float) -> None:
        if type(value) not in (float, int) or not isfinite(value) or value < 0:
            raise ValueError("Invalid simulation backlog")
        self._debt_ms = value

    @property
    def backlog_ms(self) -> float:
        return self._debt_ms
