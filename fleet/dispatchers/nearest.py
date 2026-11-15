"""Greedy nearest-feasible baseline, independent of priority and deadlines."""

from fleet.dispatchers.fcfs import estimate_job, estimate_refuel
from fleet.models import Assignment, JobStatus, SimState, TractorStatus


class NearestDispatcher:
    """Visit idle tractors by id and select their closest feasible pending job."""

    name = "nearest"

    def __init__(self):
        self.annotations = []

    def decide(self, state: SimState, now: int) -> list[Assignment]:
        """Use shared compatibility, route and return-reserve fuel checks."""
        self.annotations = []
        pending = [job for job in state.jobs.values()
                   if job.state == JobStatus.PENDING and job.submitted_at <= now]
        assignments = []
        reserved = set()
        reserved_fields = set()
        for tractor in sorted(state.tractors.values(), key=lambda t: t.id):
            if tractor.state != TractorStatus.IDLE or tractor.assignment is not None:
                continue
            available = [job for job in pending if job.id not in reserved and job.field not in reserved_fields]
            choices = []
            for job in available:
                assignment = estimate_job(state, tractor, job, now)
                if assignment is not None:
                    choices.append(((assignment.est_start - now, job.submitted_at, job.id), assignment))
            if choices:
                _, assignment = min(choices, key=lambda item: item[0])
                assignments.append(assignment)
                reserved.add(assignment.job_id)
                job = state.jobs[assignment.job_id]
                reserved_fields.add(job.field)
                self.annotations.append({"type": "AT_RISK" if assignment.est_finish > job.deadline + 1e-9 else "RISK_CLEARED",
                                         "job": job.id, "reason": "Nearest preserves travel ordering; planned lateness is permitted and reported."})
            else:
                refuel = estimate_refuel(state, tractor, available, now)
                if refuel:
                    assignments.append(refuel)
        return assignments
