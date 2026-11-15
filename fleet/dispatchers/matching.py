"""Previous coordinator (kept for reference): exact one-step joint matching.

Replaced as the live coordinator by the rolling-horizon planner in
coordinator.py. Not used by the dashboard or comparison."""

import math
from dataclasses import replace
from itertools import product

from fleet.dispatchers.fcfs import estimate_job, estimate_refuel
from fleet.models import Assignment, Job, JobStatus, SimState, Tractor, TractorStatus

DEFAULT_WEIGHTS = {"late": 10.0, "priority": 8.0, "urgency": 30000.0}
EPS = 1e-9


def projected_available(state: SimState, tractor: Tractor, now: int) -> tuple[Tractor, int] | None:
    """Project a busy tractor's next decision boundary, location, and fuel."""
    if tractor.state == TractorStatus.BROKEN:
        return None
    assignment = tractor.assignment
    if assignment is not None and assignment.route_pending:
        return None
    if assignment is None:
        return replace(tractor), now
    remaining_travel = 0.0
    if tractor.state == TractorStatus.TRAVELING:
        route = assignment.planned_route[assignment.route_index:]
        remaining_travel = (sum(state.navigation.adjacency[a][b] for a, b in zip(route, route[1:]))
                            - assignment.edge_progress) / tractor.speed
    if assignment.action == "REFUEL":
        remaining = (assignment.refuel_remaining if tractor.state == TractorStatus.REFUELING
                     else state.refuel_minutes)
        return (replace(tractor, location="DEPOT", fuel=tractor.fuel_capacity,
                        assignment=None, state=TractorStatus.IDLE),
                math.ceil(now + remaining_travel + remaining - EPS))
    job = state.jobs[assignment.job_id]
    work = job.remaining / tractor.work_rate
    return (replace(tractor, location=job.field,
                    fuel=tractor.fuel - remaining_travel * tractor.burn_travel - work * tractor.burn_work,
                    assignment=None, state=TractorStatus.IDLE),
            math.ceil(now + remaining_travel + work - EPS))


class MatchingCoordinator:
    """Maximize on-time assignments, then assignments, then minimize total cost."""

    name = "matching"

    def __init__(self, weights: dict | None = None, hold_minutes: int = 20, hold_saving: float = 10):
        self.weights = dict(DEFAULT_WEIGHTS if weights is None else weights)
        self.hold_minutes = hold_minutes
        self.hold_saving = hold_saving
        self.annotations: list[dict] = []

    def score(self, tractor: Tractor, job: Job, assignment: Assignment, available: int) -> float:
        """Use actual travel, lateness, priority, and remaining deadline slack."""
        travel = assignment.est_start - available
        work = job.remaining / tractor.work_rate
        urgency = 1 / max(1, job.deadline - available - work)
        return (travel + self.weights["late"] * max(0, assignment.est_finish - job.deadline)
                - self.weights["priority"] * job.priority - self.weights["urgency"] * urgency)

    def decide(self, state: SimState, now: int) -> list[Assignment]:
        """Return joint assignments without mutating the supplied simulation state."""
        self.annotations = []
        pending = sorted((job for job in state.jobs.values()
                          if job.state == JobStatus.PENDING and job.submitted_at <= now),
                         key=lambda job: job.id)
        idle = sorted((t for t in state.tractors.values()
                       if t.state == TractorStatus.IDLE and t.assignment is None), key=lambda t: t.id)
        projected = {t.id: projected_available(state, t, now) for t in state.tractors.values()}
        options: dict[str, list[Assignment | None]] = {t.id: [None] for t in idle}
        costs = {}
        for job in pending:
            candidates = []
            on_time_after_refuel = False
            for tractor_id, projection in sorted(projected.items()):
                if projection is None:
                    continue
                tractor, free_at = projection
                assignment = estimate_job(state, tractor, job, free_at)
                if assignment:
                    candidates.append((tractor, free_at, assignment, self.score(tractor, job, assignment, free_at)))
                else:
                    refuel = estimate_refuel(state, tractor, [job], free_at)
                    if refuel:
                        full = replace(tractor, location="DEPOT", fuel=tractor.fuel_capacity)
                        after = estimate_job(state, full, job, math.ceil(refuel.est_finish - EPS))
                        on_time_after_refuel |= after is not None and after.est_finish <= job.deadline + EPS
            on_time = on_time_after_refuel or any(a.est_finish <= job.deadline + EPS for _, _, a, _ in candidates)
            at_risk = not on_time
            self.annotations.append({"type": "AT_RISK" if at_risk else "RISK_CLEARED", "job": job.id,
                                     "reason": "No compatible, reachable, fuel-feasible tractor can currently finish on time."
                                     if at_risk else "An on-time tractor is available or already finishing other work."})
            idle_candidates = [c for c in candidates if c[1] == now and c[0].id in options]
            busy_candidates = [c for c in candidates if c[1] > now and c[2].est_finish <= job.deadline + EPS
                               and c[1] - now <= self.hold_minutes]
            # Hold only when a soon-free tractor is materially cheaper and on time.
            best_busy = min(busy_candidates, key=lambda c: (c[3], c[0].id), default=None)
            best_idle = min(idle_candidates, key=lambda c: (c[3], c[0].id), default=None)
            if best_busy and best_idle and best_busy[3] + self.hold_saving < best_idle[3]:
                self.annotations.append({"type": "HOLD", "job": job.id, "tractor": best_busy[0].id,
                                         "reason": f"Waiting {best_busy[1] - now} min for a closer tractor; still on time."})
                continue
            for tractor, free_at, assignment, cost in idle_candidates:
                if on_time and assignment.est_finish > job.deadline + EPS:
                    continue
                assignment.explanation = {"score": round(cost, 6), "priority": job.priority,
                                          "travel_minutes": assignment.est_start - free_at,
                                          "deadline_slack": job.deadline - assignment.est_finish,
                                          "at_risk": at_risk}
                options[tractor.id].append(assignment)
                costs[(tractor.id, job.id)] = cost
        best_key = None
        best = []
        # At most three tractors. Include idle choices so positive costs cannot
        # cause empty plans to win, and every distinct job pairing is considered.
        for matching in product(*(options[t.id] for t in idle)):
            chosen = [a for a in matching if a is not None]
            if len({a.job_id for a in chosen}) != len(chosen):
                continue
            on_time_count = sum(a.est_finish <= state.jobs[a.job_id].deadline + EPS for a in chosen)
            key = (-on_time_count, -len(chosen), sum(costs[(a.tractor_id, a.job_id)] for a in chosen),
                   tuple((a.tractor_id, a.job_id) for a in chosen))
            if best_key is None or key < best_key:
                best_key, best = key, chosen
        assigned_jobs = {a.job_id for a in best}
        assigned_tractors = {a.tractor_id for a in best}
        remaining = [job for job in pending if job.id not in assigned_jobs]
        for tractor in idle:
            if tractor.id in assigned_tractors:
                continue
            if remaining and not any(estimate_job(state, tractor, job, now) for job in remaining):
                refuel = estimate_refuel(state, tractor, remaining, now)
                if refuel:
                    best.append(refuel)
        return best
