"""Domain objects shared by the simulator and dispatchers."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fleet.navigation import Navigation


class TractorStatus(StrEnum):
    IDLE = "IDLE"
    TRAVELING = "TRAVELING"
    WORKING = "WORKING"
    REFUELING = "REFUELING"
    BROKEN = "BROKEN"


class JobStatus(StrEnum):
    PENDING = "PENDING"
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"


@dataclass
class Field:
    id: str
    name: str
    x: float
    y: float
    acres: float = 0


@dataclass
class Assignment:
    tractor_id: str
    job_id: str | None
    planned_route: list[str]
    est_start: float
    est_finish: float
    action: str = "JOB"
    explanation: dict = field(default_factory=dict)
    route_index: int = 0
    edge_progress: float = 0
    refuel_remaining: float = 0
    reroute: bool = False
    route_pending: bool = False


@dataclass
class Tractor:
    id: str
    name: str
    implements: list[str]
    speed: float
    fuel: float = 100
    fuel_capacity: float = 100
    burn_travel: float = 0.1
    burn_work: float = 0.2
    work_rate: float = 0.25
    reserve: float = 10
    location: str = "DEPOT"
    capabilities: list[str] = field(default_factory=list)
    state: TractorStatus = TractorStatus.IDLE
    assignment: Assignment | None = None
    travel_minutes: float = 0
    idle_intervals: list[tuple[float, float]] = field(default_factory=list)
    waiting_reason: str | None = None


@dataclass
class Job:
    id: str
    type: str
    field: str
    requires_implement: str
    priority: int
    deadline: int
    submitted_at: int
    acres_total: float
    acres_done: float = 0
    requires_capabilities: list[str] = field(default_factory=list)
    state: JobStatus = JobStatus.PENDING
    late: bool = False
    at_risk: bool = False
    finished_at: float | None = None
    history: list[dict] = field(default_factory=list)
    recovery_history: list[dict] = field(default_factory=list)

    @property
    def remaining(self) -> float:
        """Return only the unfinished acres, including after a breakdown."""
        return max(0.0, self.acres_total - self.acres_done)


@dataclass
class Event:
    at: int
    type: str
    details: dict = field(default_factory=dict)


@dataclass
class SimState:
    fields: dict[str, Field]
    tractors: dict[str, Tractor]
    jobs: dict[str, Job]
    navigation: "Navigation"
    horizon: int = 600
    seed: int = 0
    refuel_minutes: int = 15
    now: int = 0
    completed: list[str] = field(default_factory=list)
    safety_violations: int = 0
    excluded_pairs: set[tuple[str, str | None]] = field(default_factory=set)
