"""First-come, first-served baseline with compatibility and fuel constraints."""

from fleet.models import Assignment, Job, JobStatus, SimState, Tractor, TractorStatus

EPS = 1e-9


def compatible(tractor: Tractor, job: Job) -> bool:
    """Check required implements and capability tags."""
    return (job.requires_implement in tractor.implements
            and set(job.requires_capabilities).issubset(tractor.capabilities))


def estimate_job(state: SimState, tractor: Tractor, job: Job, now: int) -> Assignment | None:
    """Estimate a reachable job, retaining fuel for work, return, and reserve."""
    if (tractor.id, job.id) in state.excluded_pairs or not compatible(tractor, job):
        return None
    # Reserve fields for active jobs so later detours cannot create conflicts.
    if any(t.id != tractor.id and t.assignment and t.assignment.job_id
           and state.jobs[t.assignment.job_id].field == job.field for t in state.tractors.values()):
        return None
    route, distance = state.navigation.route(tractor.location, job.field)
    return_route, return_distance = state.navigation.route(job.field, "DEPOT")
    if not route or not return_route:
        return None
    travel = distance / tractor.speed
    work = job.remaining / tractor.work_rate
    fuel_needed = (travel + return_distance / tractor.speed) * tractor.burn_travel + work * tractor.burn_work
    if tractor.fuel - fuel_needed < tractor.reserve - EPS:
        return None
    return Assignment(tractor.id, job.id, route, now + travel, now + travel + work)


def estimate_refuel(state: SimState, tractor: Tractor, pending: list[Job], now: int) -> Assignment | None:
    """Refuel only when it enables pending work and the depot is reachable."""
    if (tractor.id, None) in state.excluded_pairs or tractor.fuel >= tractor.fuel_capacity - EPS:
        return None
    route, distance = state.navigation.route(tractor.location, "DEPOT")
    if not route or tractor.fuel - distance / tractor.speed * tractor.burn_travel < tractor.reserve - EPS:
        return None
    for job in pending:
        if not compatible(tractor, job):
            continue
        outbound, job_distance = state.navigation.route("DEPOT", job.field)
        inbound, return_distance = state.navigation.route(job.field, "DEPOT")
        needed = ((job_distance + return_distance) / tractor.speed * tractor.burn_travel
                  + job.remaining / tractor.work_rate * tractor.burn_work + tractor.reserve)
        if outbound and inbound and needed <= tractor.fuel_capacity + EPS:
            travel = distance / tractor.speed
            return Assignment(tractor.id, None, route, now + travel,
                              now + travel + state.refuel_minutes, action="REFUEL")
    return None


class FCFSDispatcher:
    """Choose jobs by submission time then id; tractors by id, without lookahead."""

    name = "fcfs"

    def __init__(self):
        self.annotations = []

    def decide(self, state: SimState, now: int) -> list[Assignment]:
        """Return at most one feasible job or refuel action per idle tractor."""
        self.annotations = []
        pending = sorted((job for job in state.jobs.values()
                          if job.state == JobStatus.PENDING and job.submitted_at <= now),
                         key=lambda job: (job.submitted_at, job.id))
        assignments = []
        reserved = set()
        reserved_fields = set()
        for tractor in sorted(state.tractors.values(), key=lambda item: item.id):
            if tractor.state != TractorStatus.IDLE or tractor.assignment is not None:
                continue
            available = [job for job in pending if job.id not in reserved and job.field not in reserved_fields]
            for job in available:
                assignment = estimate_job(state, tractor, job, now)
                if assignment is not None:
                    assignments.append(assignment)
                    reserved.add(job.id)
                    reserved_fields.add(job.field)
                    self.annotations.append({"type": "AT_RISK" if assignment.est_finish > job.deadline + EPS else "RISK_CLEARED",
                                             "job": job.id, "reason": "FCFS preserves submission order; planned lateness is permitted and reported."})
                    break
            else:
                assignment = estimate_refuel(state, tractor, available, now)
                if assignment is not None:
                    assignments.append(assignment)
        return assignments
