"""Load JSON scenarios and reject invalid domain input before a run."""

import json
import math
from pathlib import Path

from fleet.models import Event, Field, Job, SimState, Tractor
from fleet.navigation import Navigation


def _number(value, name: str, minimum: float = 0, positive: bool = False) -> None:
    if (isinstance(value, bool) or not isinstance(value, (float, int))
            or not math.isfinite(value) or value < minimum or (positive and value == 0)):
        raise ValueError(f"{name} must be finite and {'positive' if positive else f'>= {minimum}'}")


def _integer(value, name: str, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


def _unique(items: list, label: str) -> dict:
    result = {}
    for item in items:
        if not isinstance(item.id, str) or not item.id or item.id in result:
            raise ValueError(f"Duplicate or invalid {label} id: {item.id}")
        result[item.id] = item
    return result


def _tags(tags, label: str) -> None:
    if not isinstance(tags, list) or any(not isinstance(tag, str) or not tag for tag in tags):
        raise ValueError(f"{label} must be a list of nonempty strings")


def validate_job(job: Job, state: SimState) -> None:
    """Validate both scenario jobs and jobs submitted at runtime."""
    if not isinstance(job.id, str) or not job.id:
        raise ValueError("Job id must be a nonempty string")
    if job.field not in state.fields or job.field == "DEPOT":
        raise ValueError(f"Unknown job field: {job.field}")
    _integer(job.priority, "priority", 1)
    if job.priority > 5:
        raise ValueError("priority must be between 1 and 5")
    _integer(job.submitted_at, "submitted_at")
    _integer(job.deadline, "deadline")
    if job.deadline <= job.submitted_at:
        raise ValueError("deadline must be after submitted_at")
    _number(job.acres_total, "acres_total", positive=True)
    _number(job.acres_done, "acres_done")
    if job.acres_done != 0:
        raise ValueError("New jobs must start at zero acres_done")
    _tags(job.requires_capabilities, "requires_capabilities")
    if not any(job.requires_implement in t.implements for t in state.tractors.values()):
        raise ValueError(f"No tractor has implement: {job.requires_implement}")


def load_scenario(path: str | Path) -> tuple[SimState, list[Event]]:
    """Load an independent initial state plus its ordered event timeline."""
    with Path(path).open(encoding="utf-8-sig") as stream:
        return scenario_from_dict(json.load(stream))


def scenario_from_dict(data: dict) -> tuple[SimState, list[Event]]:
    """Validate an in-memory scenario, useful for tests and later dashboard input."""
    depot = Field(id="DEPOT", name="Depot", acres=0, **data.get("depot", {"x": 0, "y": 0}))
    field_list = [Field(**item) for item in data["fields"]]
    fields = _unique([depot, *field_list], "field")
    tractors = _unique([Tractor(**item) for item in data["tractors"]], "tractor")
    jobs = _unique([Job(**item) for item in data["jobs"]], "job")
    if not tractors:
        raise ValueError("A scenario needs at least one tractor")
    for node in fields.values():
        for coordinate in (node.x, node.y):
            if isinstance(coordinate, bool) or not isinstance(coordinate, (int, float)) or not math.isfinite(coordinate):
                raise ValueError("Field coordinates must be finite numbers")
        _number(node.acres, "field acres")
    for tractor in tractors.values():
        _tags(tractor.implements, "implements")
        _tags(tractor.capabilities, "capabilities")
        for attr in ("speed", "work_rate", "fuel_capacity"):
            _number(getattr(tractor, attr), attr, positive=True)
        for attr in ("fuel", "reserve", "burn_travel", "burn_work"):
            _number(getattr(tractor, attr), attr)
        if tractor.fuel > tractor.fuel_capacity or tractor.reserve > tractor.fuel_capacity:
            raise ValueError("Fuel and reserve must not exceed capacity")
        if tractor.location not in fields:
            raise ValueError(f"Unknown tractor location: {tractor.location}")
    navigation = Navigation(fields, data.get("edges"))
    state = SimState(fields, tractors, jobs, navigation,
                     horizon=data.get("horizon", 600), seed=data.get("seed", 0),
                     refuel_minutes=data.get("refuel_minutes", 15))
    _integer(state.horizon, "horizon", 1)
    _integer(state.seed, "seed")
    _integer(state.refuel_minutes, "refuel_minutes", 1)
    for job in jobs.values():
        validate_job(job, state)
    events = []
    known_jobs = dict(jobs)
    # Validate future submitted jobs first, including duplicate ids.
    for item in data.get("events", []):
        if item["type"] == "submit_job":
            job_data = dict(item["job"])
            job_data.setdefault("submitted_at", item["at"])
            job = Job(**job_data)
            validate_job(job, state)
            if job.id in known_jobs or job.submitted_at != item["at"]:
                raise ValueError("Submitted job id must be unique and submitted_at must equal event time")
            known_jobs[job.id] = job
    for item in data.get("events", []):
        details = {key: value for key, value in item.items() if key not in {"type", "at"}}
        event = Event(item["at"], item["type"], details)
        _integer(event.at, "event at")
        if event.type in {"breakdown", "repair"}:
            if details.get("tractor") not in tractors:
                raise ValueError("Unknown event tractor")
        elif event.type in {"block_route", "unblock_route"}:
            if tuple(sorted((details["a"], details["b"]))) not in navigation.edges:
                raise ValueError("Unknown event road")
        elif event.type == "change_priority":
            if details.get("job") not in known_jobs or known_jobs[details["job"]].submitted_at > event.at:
                raise ValueError("Unknown or not-yet-submitted event job")
            _integer(details["priority"], "event priority", 1)
            if details["priority"] > 5:
                raise ValueError("event priority must be <= 5")
        elif event.type != "submit_job":
            raise ValueError(f"Unknown event type: {event.type}")
        events.append(event)
    return state, events
