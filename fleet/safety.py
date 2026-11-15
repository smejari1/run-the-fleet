"""Independent assignment validation; this module never selects a job."""

import math
from dataclasses import dataclass, field

from fleet.models import Assignment, JobStatus, SimState, TractorStatus

EPS = 1e-7


@dataclass
class SafetyResult:
    ok: bool
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class UnsafeAssignmentError(ValueError):
    """An invalid proposal was rejected without applying it."""


def _finite(value) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def check(assignment: Assignment, state: SimState) -> SafetyResult:
    """Recompute route, fuel, timing and field overlap independently of dispatch."""
    violations, warnings = [], []
    tractor = state.tractors.get(assignment.tractor_id) if isinstance(assignment.tractor_id, str) else None
    if tractor is None:
        return SafetyResult(False, ["Tractor does not exist."])
    if tractor.state == TractorStatus.BROKEN:
        violations.append("Tractor is broken.")
    if tractor.assignment is not None or tractor.state != TractorStatus.IDLE:
        violations.append("Tractor is already assigned or not idle.")
    if not _finite(tractor.fuel) or not 0 <= tractor.fuel <= tractor.fuel_capacity:
        violations.append("Tractor fuel must be finite and within tank capacity.")
    if (assignment.route_index != 0 or assignment.edge_progress != 0 or assignment.refuel_remaining != 0
            or assignment.reroute or assignment.route_pending):
        violations.append("A new assignment must not contain existing travel or action progress.")
    job = None
    if assignment.action == "JOB":
        job = state.jobs.get(assignment.job_id) if isinstance(assignment.job_id, str) else None
        if job is None:
            violations.append("Job does not exist.")
        else:
            if job.state == JobStatus.DONE:
                violations.append("Job is already done and is not available.")
            elif job.state != JobStatus.PENDING or job.submitted_at > state.now:
                violations.append("Job is not available for assignment.")
            if any(t.assignment and t.assignment.job_id == job.id for t in state.tractors.values()):
                violations.append("Job is held by another assignment.")
            if job.requires_implement not in tractor.implements:
                violations.append(f"Missing implement: {job.requires_implement}.")
            missing = set(job.requires_capabilities) - set(tractor.capabilities)
            if missing:
                violations.append("Missing capabilities: " + ", ".join(sorted(missing)) + ".")
    elif assignment.action != "REFUEL" or assignment.job_id is not None:
        violations.append("Invalid assignment action or refuel job id.")
    route = assignment.planned_route
    route_valid = isinstance(route, list) and bool(route) and all(isinstance(n, str) and n in state.fields for n in route)
    distance = 0.0
    if not route_valid:
        violations.append("Route is empty or contains an unknown node.")
    else:
        if route[0] != tractor.location:
            violations.append("Route does not start at the tractor's location.")
        target = job.field if job else "DEPOT"
        if route[-1] != target:
            violations.append("Route ends at the wrong destination.")
        for a, b in zip(route, route[1:]):
            length = state.navigation.adjacency.get(a, {}).get(b)
            if length is None:
                violations.append(f"Route uses a nonexistent road: {a}–{b}.")
                route_valid = False
            elif tuple(sorted((a, b))) in state.navigation.blocked:
                violations.append(f"Route uses a blocked road: {a}–{b}.")
                route_valid = False
            else:
                distance += length
    times_valid = _finite(assignment.est_start) and _finite(assignment.est_finish)
    if not times_valid:
        violations.append("Estimated start and finish must be finite numbers.")
    if route_valid:
        travel = distance / tractor.speed
        work = job.remaining / tractor.work_rate if job else state.refuel_minutes
        actual_start = state.now + travel
        actual_finish = actual_start + work
        if times_valid and (not math.isclose(assignment.est_start, actual_start, abs_tol=EPS, rel_tol=0)
                            or not math.isclose(assignment.est_finish, actual_finish, abs_tol=EPS, rel_tol=0)):
            violations.append("Estimated times do not match route travel and remaining work.")
        fuel_needed = travel * tractor.burn_travel
        if job:
            return_route, return_length = state.navigation.route(job.field, "DEPOT")
            if not return_route:
                violations.append("No open return route to the depot.")
            else:
                fuel_needed += work * tractor.burn_work + return_length / tractor.speed * tractor.burn_travel
        if tractor.fuel - fuel_needed < tractor.reserve - EPS:
            violations.append("Insufficient fuel for travel, work, depot return and reserve.")
        if job:
            if actual_finish > job.deadline + EPS:
                if job.at_risk:
                    warnings.append("At-risk job is planned to finish after its deadline.")
                else:
                    violations.append("Estimated finish exceeds the deadline without an at-risk flag.")
            for other in state.tractors.values():
                active = other.assignment
                if other.id == tractor.id or not active or active.job_id is None:
                    continue
                other_job = state.jobs.get(active.job_id)
                if not other_job or other_job.field != job.field:
                    continue
                start = max(state.now, active.est_start)
                finish = math.inf if active.route_pending else active.est_finish
                if max(actual_start, start) < min(actual_finish, finish) - EPS:
                    violations.append(f"Field work overlaps {other.id} on {job.field}.")
        if actual_finish > state.horizon + EPS:
            warnings.append("Estimated finish exceeds the shift horizon.")
    return SafetyResult(not violations, violations, warnings)
