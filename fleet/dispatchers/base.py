"""Dispatcher contract and a no-op dispatcher for the simulation foundation."""

from typing import Protocol

from fleet.models import Assignment, SimState


class Dispatcher(Protocol):
    name: str

    def decide(self, state: SimState, now: int) -> list[Assignment]:
        """Return assignments at a decision point without mutating state."""
        ...


class NoopDispatcher:
    name = "none"

    def decide(self, state: SimState, now: int) -> list[Assignment]:
        """Leave all tractors idle to exercise event processing in isolation."""
        return []
