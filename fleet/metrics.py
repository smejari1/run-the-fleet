"""Single source of metric definitions for CLI and later comparison/dashboard."""

import math
from collections import Counter

from fleet.models import JobStatus, SimState, TractorStatus


def consistency_errors(state: SimState) -> list[str]:
    """Find duplicate ownership, lost progress, or inconsistent completion data."""
    errors = []
    holders = Counter(t.assignment.job_id for t in state.tractors.values()
                      if t.assignment and t.assignment.job_id is not None)
    completed = Counter(state.completed)
    for tractor in state.tractors.values():
        assignment = tractor.assignment
        if tractor.state in {TractorStatus.BROKEN, TractorStatus.IDLE} and assignment:
            errors.append(f"Unavailable or idle tractor {tractor.id} has an assignment")
        if tractor.state not in {TractorStatus.BROKEN, TractorStatus.IDLE} and assignment is None:
            errors.append(f"Active tractor {tractor.id} has no assignment")
        if not math.isfinite(tractor.fuel) or not -1e-9 <= tractor.fuel <= tractor.fuel_capacity + 1e-9:
            errors.append(f"Invalid fuel: {tractor.id}")
        if tractor.location not in state.fields:
            errors.append(f"Unknown tractor location: {tractor.id}")
        if assignment:
            if assignment.tractor_id != tractor.id:
                errors.append(f"Assignment tractor mismatch: {tractor.id}")
            if assignment.action == "JOB":
                job = state.jobs.get(assignment.job_id)
                if job and ((tractor.state == TractorStatus.WORKING) != (job.state == JobStatus.IN_PROGRESS)):
                    errors.append(f"Tractor/job activity mismatch: {job.id}")
            elif assignment.action != "REFUEL" or assignment.job_id is not None:
                errors.append(f"Invalid assignment action: {tractor.id}")
    for job in state.jobs.values():
        if holders[job.id] > 1:
            errors.append(f"Duplicate ownership: {job.id}")
        recorded = sum(item["acres"] for item in job.history)
        if not math.isclose(recorded, job.acres_done, abs_tol=1e-7):
            errors.append(f"Work ledger mismatch: {job.id}")
        if not -1e-9 <= job.acres_done <= job.acres_total + 1e-9:
            errors.append(f"Out-of-range work: {job.id}")
        previous_end = 0.0
        for entry in job.history:
            if (entry["tractor"] not in state.tractors
                    or not all(math.isfinite(entry[key]) for key in ("start", "end", "acres"))
                    or entry["acres"] <= 0 or entry["start"] < previous_end - 1e-9
                    or entry["end"] <= entry["start"] or entry["end"] > state.now + 1e-9):
                errors.append(f"Invalid or overlapping work history: {job.id}")
                break
            previous_end = entry["end"]
        if job.state == JobStatus.DONE:
            if completed[job.id] != 1 or holders[job.id] or job.remaining > 1e-9:
                errors.append(f"Invalid completion: {job.id}")
            if (job.finished_at is None or not math.isfinite(job.finished_at)
                    or not 0 <= job.finished_at <= state.now + 1e-9
                    or job.late != (job.finished_at > job.deadline + 1e-9)):
                errors.append(f"Invalid completion time: {job.id}")
        elif completed[job.id]:
            errors.append(f"Unfinished job in completed list: {job.id}")
        elif job.finished_at is not None:
            errors.append(f"Unfinished job has completion time: {job.id}")
        if (job.state in {JobStatus.ASSIGNED, JobStatus.IN_PROGRESS}) != bool(holders[job.id]):
            errors.append(f"Ownership mismatch: {job.id}")
    intervals = {}
    workers = Counter()
    for tractor in state.tractors.values():
        if tractor.state == TractorStatus.WORKING and tractor.assignment and tractor.assignment.job_id in state.jobs:
            workers[state.jobs[tractor.assignment.job_id].field] += 1
    for field_id, count in workers.items():
        if count > 1:
            errors.append(f"Multiple working tractors on field: {field_id}")
    for job in state.jobs.values():
        intervals.setdefault(job.field, []).extend((item["start"], item["end"]) for item in job.history)
    for field_id, work in intervals.items():
        last_end = -math.inf
        for start, end in sorted(work):
            if start < last_end - 1e-9:
                errors.append(f"Overlapping field work history: {field_id}")
                break
            last_end = max(last_end, end)
    for job_id in (holders.keys() | completed.keys()) - state.jobs.keys():
        errors.append(f"Unknown job in ledger: {job_id}")
    return errors


def calculate_metrics(state: SimState) -> dict:
    """Measure actual useful work and time; unfinished work uses the full window."""
    jobs = [job for job in state.jobs.values() if job.submitted_at <= state.now]
    done = [job for job in jobs if job.state == JobStatus.DONE]
    on_time = [job for job in done if not job.late]
    makespan = max((job.finished_at for job in done), default=None)
    # Include known future jobs when deciding whether the whole workload ended.
    all_finished = bool(state.jobs) and all(job.state == JobStatus.DONE for job in state.jobs.values())
    idle_cutoff = makespan if all_finished else state.now
    if not state.jobs:
        idle_cutoff = 0
    idle = sum(max(0, min(end, idle_cutoff) - start)
               for tractor in state.tractors.values() for start, end in tractor.idle_intervals)
    return {
        "jobs_completed": len(done),
        "jobs_on_time": len(on_time),
        "deadline_misses": len(jobs) - len(on_time),
        "priority_weighted_on_time": sum(job.priority for job in on_time),
        "acres_completed": round(sum(job.acres_done for job in jobs), 6),
        "travel_minutes": round(sum(t.travel_minutes for t in state.tractors.values()), 6),
        "idle_minutes": round(idle, 6),
        "makespan": round(makespan, 6) if makespan is not None else None,
        "safety_violations": state.safety_violations,
        "duplicate_or_lost_work": len(consistency_errors(state)),
    }
