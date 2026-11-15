"""Plain-language explanations built only from real decisions in the event log.

Nothing here invents a reason: every sentence is filled in from the numbers
the coordinator used (travel, deadline slack, its planned queue, the tractors
it rejected and why) or from simulator records (breakdowns, preserved acres,
road closures). Used by the dashboard's "Why?" button and plan-updates feed.
"""

from fleet.models import SimState


def clock(minutes: float) -> str:
    """Shift clock, matching the dashboard (00:00 = start of shift)."""
    minutes = max(0.0, float(minutes))
    return f"{int(minutes // 60):02d}:{int(minutes % 60):02d}"


def _job_label(state: SimState, job_id: str) -> str:
    job = state.jobs.get(job_id)
    if job is None:
        return job_id
    field = state.fields.get(job.field)
    place = f"{job.field} ({field.name})" if field and field.name != job.field else job.field
    return f"{job.type} {place} ({job_id})"


def _list(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def assignment_text(state: SimState, record: dict) -> str:
    """One assignment, e.g. 'agxt3 → spraying F5 (J07). It is 24 min away...'."""
    details = record.get("details", {})
    tractor = record.get("tractor")
    if details.get("action") == "REFUEL":
        explanation = details.get("explanation") or {}
        nxt = explanation.get("next_job")
        follow = f" so it can safely do {_job_label(state, nxt)} next" if nxt else ""
        return f"{tractor} heads to the depot to refuel{follow}."
    job_id = record.get("job")
    job = state.jobs.get(job_id)
    explanation = details.get("explanation") or {}
    travel = explanation.get("travel_minutes", details.get("est_start", record["t"]) - record["t"])
    finish = details.get("est_finish")
    parts = [f"{tractor} → {_job_label(state, job_id)}."]
    if travel is not None:
        parts.append("It is already at the field." if travel <= 1e-7 else f"It is {travel:.1f} min away.")
    tractor_obj = state.tractors.get(tractor)
    if tractor_obj is not None and job is not None:
        parts.append(f"It has the {job.requires_implement.replace('_', ' ')}"
                     + (f" and {', '.join(job.requires_capabilities)}" if job.requires_capabilities else "") + ".")
    if explanation.get("fuel_at_assignment") is not None:
        parts.append(f"At dispatch it had {explanation['fuel_at_assignment']:.1f}% fuel; "
                     f"this job needed {explanation['fuel_required']:.1f}% including work, depot return and reserve.")
    if finish is not None and job is not None:
        slack = job.deadline - finish
        if slack >= 0:
            parts.append(f"Estimated finish {clock(finish)}, {slack:.0f} min before the {clock(job.deadline)} deadline.")
        else:
            parts.append(f"Estimated finish {clock(finish)}, {-slack:.0f} min after the {clock(job.deadline)} deadline: "
                         "this plan finishes the job late; lateness is recorded in the comparison.")
    alternatives = explanation.get("alternatives") or []
    if alternatives and finish is not None:
        best = min(alternatives, key=lambda a: a["est_finish"])
        if best["free_in"] > 0:
            parts.append(f"{best['tractor']} is busy for {best['free_in']} more min and would finish "
                         f"at {clock(best['est_finish'])}.")
        else:
            parts.append(f"{best['tractor']} could finish at {clock(best['est_finish'])} if given this job alone. "
                         "The planner chose this assignment while balancing the whole fleet's queues.")
    rejected = explanation.get("rejected") or []
    if rejected:
        parts.append(_list([f"{r['tractor']} can't ({r['reason']})" for r in rejected]) + ".")
    queue = explanation.get("queue") or []
    if len(queue) > 1:
        parts.append(f"After this, {tractor}'s plan is {', '.join(queue[1:])}.")
    return " ".join(parts)


def record_text(state: SimState, record: dict) -> str | None:
    """Turn one log record into a sentence for the plan-updates feed (or None to skip)."""
    kind = record["type"]
    details = record.get("details", {})
    tractor, job = record.get("tractor"), record.get("job")
    if kind == "ASSIGNED":
        return assignment_text(state, record)
    if kind == "BREAKDOWN":
        return details.get("message", f"{tractor} broke down.")
    if kind in {"RECOVERY_ASSIGNED", "RECOVERY_WAITING"}:
        return details.get("message")
    if kind == "REPLAN":
        return details.get("reason")
    if kind == "HOLD":
        return f"{_job_label(state, job)}: {details.get('reason', '')}"
    if kind == "AT_RISK":
        return f"⚠ {_job_label(state, job)} is at risk. {details.get('reason', '')}"
    if kind == "JOB_DONE":
        return f"{tractor} finished {_job_label(state, job)}" + (" (late)." if details.get("late") else " on time.")
    if kind == "REPAIRED":
        return f"{tractor} was repaired and is available again."
    if kind == "BLOCK_ROUTE":
        return f"Road {details.get('a')}-{details.get('b')} closed. Routes were reassessed; safe alternatives are used when available."
    if kind == "UNBLOCK_ROUTE":
        return f"Road {details.get('a')}-{details.get('b')} reopened."
    if kind == "REROUTED":
        route = " → ".join(details.get("route", []))
        finish = details.get("est_finish")
        return (f"{tractor}'s route" + (f" for {job}" if job else "") + f" was updated: {route}."
                + (f" Estimated finish {clock(finish)}." if finish is not None else ""))
    if kind == "ROUTE_UNAVAILABLE":
        return (f"{tractor} has no safe route" + (f" for {job}; it returns to the queue" if job else "")
                + ". " + details.get("reason", ""))
    if kind in {"REROUTE_PENDING", "FIELD_WAIT", "SAFETY_REJECTED", "SAFETY_WARNING"}:
        return details.get("message")
    if kind == "PRIORITY_CHANGED":
        return f"{job} priority set to {details.get('priority')}. The plan was recalculated."
    if kind == "JOB_SUBMITTED" and record["t"] > 0:
        return f"New job: {_job_label(state, job)}."
    return None


def plan_feed(state: SimState, records: list[dict], limit: int = 25) -> list[dict]:
    """Most recent plain-language plan updates, newest first."""
    feed = []
    for record in reversed(records):
        text = record_text(state, record)
        if text:
            feed.append({"t": record["t"], "clock": clock(record["t"]), "type": record["type"], "text": text,
                         "highlight": record["type"] in {"BREAKDOWN", "REPLAN", "RECOVERY_ASSIGNED",
                                                         "RECOVERY_WAITING", "AT_RISK", "BLOCK_ROUTE"}})
            if len(feed) >= limit:
                break
    return feed


def explain_job(state: SimState, records: list[dict], job_id: str) -> dict:
    """Answer 'Why?' for one job: current status plus the decisions behind it."""
    job = state.jobs.get(job_id)
    if job is None:
        raise KeyError(job_id)
    mine = [r for r in records if r.get("job") == job_id]
    mode = next((r.get("details", {}).get("dispatcher") for r in records if r["type"] == "RUN_STARTED"), None)
    policies = {"fcfs": "FCFS uses the earliest-submitted feasible job, with job id breaking ties.",
                "nearest": "Nearest feasible chooses the job with the least open-road travel time, subject to equipment, fuel and field access.",
                "coordinator": "The coordinator plans job sequences across the fleet, balancing deadlines, priority, travel, refuelling and idle time."}
    lines = [policies[mode]] if mode in policies else []
    last_assigned = next((r for r in reversed(mine) if r["type"] == "ASSIGNED"), None)
    if job.state == "DONE":
        status = (f"Done at {clock(job.finished_at)}, "
                  + (f"{job.finished_at - job.deadline:.0f} min late." if job.late else "on time."))
    elif job.state in {"ASSIGNED", "IN_PROGRESS"} and last_assigned:
        status = f"{'Working' if job.state == 'IN_PROGRESS' else 'On the way'}: {last_assigned['tractor']}."
    elif job.submitted_at > state.now:
        status = f"Scheduled for {clock(job.submitted_at)}; it has not entered dispatch yet."
    else:
        status = "Waiting in the queue."
    if last_assigned:
        lines.append(f"[{clock(last_assigned['t'])}] " + assignment_text(state, last_assigned))
    for record in mine:
        if record["type"] in {"BREAKDOWN", "RECOVERY_ASSIGNED", "RECOVERY_WAITING", "REPLAN", "HOLD",
                              "REROUTED", "ROUTE_UNAVAILABLE", "REROUTE_PENDING", "FIELD_WAIT", "PRIORITY_CHANGED"}:
            text = record_text(state, record)
            if text:
                lines.append(f"[{clock(record['t'])}] {text}")
    risk = next((r for r in reversed(mine) if r["type"] in {"AT_RISK", "RISK_CLEARED"}), None)
    if job.state != "DONE" and risk is not None and risk["type"] == "AT_RISK":
        lines.append("⚠ At risk: " + risk["details"].get("reason", ""))
    elif job.state != "DONE" and risk is not None:
        lines.append("Plan: " + risk["details"].get("reason", ""))
    if job.acres_done and job.state != "DONE":
        lines.append(f"{job.acres_done:.1f} of {job.acres_total:g} acres are already done and will not be repeated.")
    if job.submitted_at > state.now:
        lines.append(f"{job_id} will be considered at shift {clock(job.submitted_at)}; "
                     f"its deadline is {clock(job.deadline)}.")
    return {"job": job_id, "title": _job_label(state, job_id), "status": status,
            "lines": lines or ["No assignment has been made yet. Jobs wait for an idle, compatible, "
                                "fuel-feasible tractor and exclusive field access."],
            "decision": (last_assigned["details"].get("explanation") or {}) if last_assigned else None}
