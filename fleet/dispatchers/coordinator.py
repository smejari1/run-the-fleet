"""Rolling-horizon planner: plan every tractor's whole job sequence, dispatch the first step.

At each decision point the coordinator builds a complete plan for all pending
work: an ordered queue of jobs for every working tractor (busy tractors join
the plan from the time and place they will become free). The plan is built by
cheapest insertion from several job orderings and improved by local search
(move a job, swap two jobs). Only the first job in an idle tractor's queue is
dispatched; everything is re-planned at the next decision point, so breakdowns,
blocked roads, new jobs and priority changes are absorbed automatically.

Plan cost (lower is better), per job:
    missed horizon  -> W["undone"]                     (work not completed today)
    late            -> W["late_fixed"] + W["late_prio"] * priority
                       + W["late_min"] * priority * minutes_late
    always          -> W["finish"] * finish_time         (earlier is better)
plus W["travel"] * travel minutes for the whole fleet, plus
W["idle"] * planned idle: for every working tractor, the minutes between
finishing its own queue and the moment the last tractor finishes. This term
balances work so no tractor sits idle while another still has a long queue.
"""

import math
from dataclasses import replace

from fleet.dispatchers.fcfs import compatible, estimate_job, estimate_refuel
from fleet.explain import clock
from fleet.models import Assignment, Job, JobStatus, SimState, Tractor, TractorStatus

EPS = 1e-9

PLAN_WEIGHTS = {
    "undone": 5000.0,
    "late_fixed": 1500.0,
    "late_prio": 200.0,
    "late_min": 2.0,
    "finish": 0.05,
    "travel": 1.0,
    "idle": 2.0,
}


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


# Kept for code that reads the coordinator's weights (comparison output).
DEFAULT_WEIGHTS = PLAN_WEIGHTS


class _Planner:
    """Evaluate and improve per-tractor job sequences for one decision point."""

    def __init__(self, state: SimState, now: int, weights: dict):
        self.state = state
        self.now = now
        self.w = weights
        self._dist: dict[tuple[str, str], float | None] = {}

    def dist(self, a: str, b: str) -> float | None:
        key = (a, b)
        if key not in self._dist:
            route, length = self.state.navigation.route(a, b)
            self._dist[key] = length if route else None
        return self._dist[key]

    def simulate(self, tractor: Tractor, free_at: int, jobs: list[Job]):
        """Walk a sequence; return (cost, steps, end) or None if infeasible.

        steps: list of dicts {job, start, finish, travel, refuel_first}.
        Next jobs start on the following whole minute, as in the simulator.
        """
        loc, fuel, t = tractor.location, tractor.fuel, float(free_at)
        state, w = self.state, self.w
        end = float(free_at)
        cost, steps = 0.0, []
        for index, job in enumerate(jobs):
            if (tractor.id, job.id) in state.excluded_pairs:
                return None
            if index:
                t = math.ceil(t - EPS)
            if not compatible(tractor, job):
                return None
            d_back = self.dist(job.field, "DEPOT")
            d_to = self.dist(loc, job.field)
            if d_back is None or d_to is None:
                return None
            work = job.remaining / tractor.work_rate
            need = (d_to + d_back) / tractor.speed * tractor.burn_travel + work * tractor.burn_work
            refuel_first = False
            travel = 0.0
            if fuel - need < tractor.reserve - EPS:
                d_depot = self.dist(loc, "DEPOT")
                if d_depot is None or fuel - d_depot / tractor.speed * tractor.burn_travel < tractor.reserve - EPS:
                    return None
                travel += d_depot / tractor.speed
                t = math.ceil(t + d_depot / tractor.speed + state.refuel_minutes - EPS)
                fuel, loc, refuel_first = tractor.fuel_capacity, "DEPOT", True
                d_to = self.dist("DEPOT", job.field)
                need = (d_to + d_back) / tractor.speed * tractor.burn_travel + work * tractor.burn_work
                if fuel - need < tractor.reserve - EPS:
                    return None
            leg = d_to / tractor.speed
            travel += leg
            start = t + leg
            finish = start + work
            fuel -= leg * tractor.burn_travel + work * tractor.burn_work
            loc, t, end = job.field, finish, finish
            cost += w["travel"] * travel + self.job_cost(job, finish)
            steps.append({"job": job, "start": start, "finish": finish, "travel": travel,
                          "refuel_first": refuel_first})
        return cost, steps, end

    def job_cost(self, job: Job, finish: float) -> float:
        w = self.w
        cost = w["finish"] * finish
        if finish > self.state.horizon + EPS:
            cost += w["undone"]
        late = finish - job.deadline
        if late > EPS:
            cost += w["late_fixed"] + w["late_prio"] * job.priority + w["late_min"] * job.priority * late
        return cost


class CoordinatorDispatcher:
    """Fleet Agent: plan all pending work across the fleet, dispatch first steps."""

    name = "coordinator"

    def __init__(self, weights: dict | None = None, max_passes: int = 6):
        self.weights = dict(PLAN_WEIGHTS if weights is None else weights)
        self.max_passes = max_passes
        self.annotations: list[dict] = []
        self.last_plan: dict[str, list[dict]] = {}
        self._previous: dict | None = None

    # ---------------------------------------------------------------- planning
    def _total(self, costs: dict, ends: dict) -> float:
        """Whole-plan cost: per-tractor costs plus planned idle before the last finish."""
        if not ends:
            return 0.0
        last = max(ends.values())
        return sum(costs.values()) + self.weights["idle"] * sum(last - e for e in ends.values())

    def _plan(self, planner: _Planner, fleet: dict, jobs: list[Job]):
        """Return (best queues {tractor_id: [Job]}, unplaceable jobs)."""
        orderings = [
            sorted(jobs, key=lambda j: (j.deadline, -j.priority, j.id)),
            sorted(jobs, key=lambda j: (-j.priority, j.deadline, j.id)),
            sorted(jobs, key=lambda j: (j.deadline - j.remaining, j.id)),
            sorted(jobs, key=lambda j: (j.submitted_at, j.id)),
        ]
        best = None
        for order in orderings:
            queues = {tid: [] for tid in fleet}
            costs = {tid: 0.0 for tid in fleet}
            ends = {tid: float(fleet[tid][1]) for tid in fleet}
            unplaced = []
            for job in order:
                choice = None
                for tid, (tractor, free_at) in fleet.items():
                    queue = queues[tid]
                    for pos in range(len(queue) + 1):
                        trial = queue[:pos] + [job] + queue[pos:]
                        result = planner.simulate(tractor, free_at, trial)
                        if result is None:
                            continue
                        total = self._total({**costs, tid: result[0]}, {**ends, tid: result[2]})
                        if choice is None or total < choice[0] - EPS:
                            choice = (total, tid, trial, result[0], result[2])
                if choice is None:
                    unplaced.append(job)
                    continue
                _, tid, trial, new_cost, new_end = choice
                queues[tid], costs[tid], ends[tid] = trial, new_cost, new_end
            queues, costs, ends = self._improve(planner, fleet, queues, costs, ends)
            total = self._total(costs, ends) + len(unplaced) * self.weights["undone"] * 2
            key = (total, tuple((tid, tuple(j.id for j in q)) for tid, q in sorted(queues.items())))
            if best is None or key < best[0]:
                best = (key, queues, unplaced)
        return best[1], best[2]

    def _improve(self, planner, fleet, queues, costs, ends):
        """Local search: relocate one job, or swap two jobs, while the plan improves."""
        def evaluate(tid, queue):
            result = planner.simulate(fleet[tid][0], fleet[tid][1], queue)
            return None if result is None else (result[0], result[2])

        current = self._total(costs, ends)
        for _ in range(self.max_passes):
            best_move = None
            tids = sorted(queues)
            # Relocate a job to its best position anywhere in the fleet.
            for src in tids:
                for i in range(len(queues[src])):
                    job = queues[src][i]
                    reduced = queues[src][:i] + queues[src][i + 1:]
                    reduced_eval = evaluate(src, reduced)
                    if reduced_eval is None:
                        continue
                    for dst in tids:
                        base = reduced if dst == src else queues[dst]
                        for pos in range(len(base) + 1):
                            if dst == src and pos == i:
                                continue
                            trial = base[:pos] + [job] + base[pos:]
                            trial_eval = evaluate(dst, trial)
                            if trial_eval is None:
                                continue
                            new_costs, new_ends = dict(costs), dict(ends)
                            changes = {dst: (trial, trial_eval)}
                            if dst != src:
                                changes[src] = (reduced, reduced_eval)
                            for tid, (_, (c, e)) in changes.items():
                                new_costs[tid], new_ends[tid] = c, e
                            total = self._total(new_costs, new_ends)
                            if total < current - 1e-6 and (best_move is None or total < best_move[0]):
                                best_move = (total, changes)
            # Swap two jobs between tractors.
            for a_index, a in enumerate(tids):
                for b in tids[a_index + 1:]:
                    for i in range(len(queues[a])):
                        for j in range(len(queues[b])):
                            qa = queues[a][:i] + [queues[b][j]] + queues[a][i + 1:]
                            qb = queues[b][:j] + [queues[a][i]] + queues[b][j + 1:]
                            ea, eb = evaluate(a, qa), evaluate(b, qb)
                            if ea is None or eb is None:
                                continue
                            new_costs, new_ends = dict(costs), dict(ends)
                            new_costs[a], new_ends[a] = ea
                            new_costs[b], new_ends[b] = eb
                            total = self._total(new_costs, new_ends)
                            if total < current - 1e-6 and (best_move is None or total < best_move[0]):
                                best_move = (total, {a: (qa, ea), b: (qb, eb)})
            if best_move is None:
                break
            current, changes = best_move
            for tid, (queue, (c, e)) in changes.items():
                queues[tid], costs[tid], ends[tid] = queue, c, e
        return queues, costs, ends

    # ---------------------------------------------------------------- decide
    def decide(self, state: SimState, now: int) -> list[Assignment]:
        """Plan everything; return first steps for idle tractors (no mutation)."""
        self.annotations = []
        pending = sorted((job for job in state.jobs.values()
                          if job.state == JobStatus.PENDING and job.submitted_at <= now),
                         key=lambda job: job.id)
        fleet = {}
        for tractor in sorted(state.tractors.values(), key=lambda t: t.id):
            projection = projected_available(state, tractor, now)
            if projection is not None:
                fleet[tractor.id] = projection
        planner = _Planner(state, now, self.weights)
        queues, unplaced = self._plan(planner, fleet, pending) if pending and fleet else ({t: [] for t in fleet}, list(pending))

        plan_steps = {}
        for tid, queue in queues.items():
            result = planner.simulate(fleet[tid][0], fleet[tid][1], queue)
            plan_steps[tid] = result[1] if result else []
        self.last_plan = {tid: [{"job": s["job"].id, "start": s["start"], "finish": s["finish"],
                                 "refuel_first": s["refuel_first"]} for s in steps]
                          for tid, steps in plan_steps.items()}

        planned_finish = {s["job"].id: (tid, s) for tid, steps in plan_steps.items() for s in steps}
        for job in pending:
            if job.id in planned_finish:
                tid, step = planned_finish[job.id]
                at_risk = step["finish"] > job.deadline + EPS
                reason = (f"Planned for {tid}, est. finish {clock(step['finish'])}, "
                          f"{abs(job.deadline - step['finish']):.0f} min "
                          f"{'after' if at_risk else 'before'} the {clock(job.deadline)} deadline.")
                if step["finish"] > state.horizon + EPS:
                    reason += " That is past the end of the shift, so it may not be finished today."
            else:
                at_risk = True
                reason = "No compatible, reachable, fuel-feasible tractor is available."
            self.annotations.append({"type": "AT_RISK" if at_risk else "RISK_CLEARED",
                                     "job": job.id, "reason": reason})

        self._annotate_replan(state, pending, plan_steps)

        idle_ids = [tid for tid, (tractor, free_at) in fleet.items()
                    if free_at == now and state.tractors[tid].state == TractorStatus.IDLE
                    and state.tractors[tid].assignment is None]
        assignments = []
        reserved_fields = set()
        for tid in idle_ids:
            steps = plan_steps.get(tid) or []
            tractor = state.tractors[tid]
            if not steps:
                self._annotate_hold(state, tid, plan_steps, fleet, now)
                continue
            first = steps[0]
            job = first["job"]
            if job.field in reserved_fields:
                continue
            if first["refuel_first"]:
                refuel = estimate_refuel(state, tractor, [job], now)
                if refuel:
                    refuel.explanation = {"reason": f"Refuel first so {tid} can safely do {job.id}",
                                          "next_job": job.id}
                    assignments.append(refuel)
                continue
            assignment = estimate_job(state, tractor, job, now)
            if assignment is None:
                continue
            assignment.explanation = self._explain(state, tid, job, assignment, plan_steps, fleet, planner, now)
            assignments.append(assignment)
            reserved_fields.add(job.field)
        return assignments

    def _annotate_replan(self, state, pending, plan_steps):
        """After an outside change, log which queued jobs moved to another tractor and why."""
        owner = {s["job"].id: tid for tid, steps in plan_steps.items() for s in steps}
        snapshot = {"owner": owner,
                    "broken": {t.id for t in state.tractors.values() if t.state == TractorStatus.BROKEN},
                    "blocked": set(state.navigation.blocked),
                    "priority": {job.id: job.priority for job in state.jobs.values()},
                    "jobs": set(state.jobs)}
        previous, self._previous = self._previous, snapshot
        if previous is None:
            return
        causes = []
        for tid in sorted(snapshot["broken"] - previous["broken"]):
            causes.append(f"{tid} broke down")
        for tid in sorted(previous["broken"] - snapshot["broken"]):
            causes.append(f"{tid} was repaired")
        for a, b in sorted(snapshot["blocked"] - previous["blocked"]):
            causes.append(f"road {a}-{b} was closed")
        for a, b in sorted(previous["blocked"] - snapshot["blocked"]):
            causes.append(f"road {a}-{b} reopened")
        for job_id, priority in sorted(snapshot["priority"].items()):
            old = previous["priority"].get(job_id)
            if old is not None and old != priority:
                causes.append(f"{job_id} priority changed from {old} to {priority}")
        new_jobs = sorted(snapshot["jobs"] - previous["jobs"])
        if new_jobs:
            causes.append("new job" + ("s " if len(new_jobs) > 1 else " ") + ", ".join(new_jobs) + " arrived")
        if not causes:
            return
        cause = "; ".join(causes)
        for job in pending:
            before, after = previous["owner"].get(job.id), owner.get(job.id)
            if before and before != after:
                self.annotations.append({
                    "type": "REPLAN", "job": job.id, "from_tractor": before, "to_tractor": after,
                    "cause": cause,
                    "reason": (f"Because {cause}, {job.id} moved from {before} to {after}."
                               if after else f"Because {cause}, {job.id} has no tractor that can do it right now.")})

    def _annotate_hold(self, state, tid, plan_steps, fleet, now):
        """Log why an idle tractor is left waiting while compatible work is queued."""
        for other, steps in sorted(plan_steps.items()):
            if other == tid or not steps:
                continue
            for step in steps:
                job = step["job"]
                if compatible(state.tractors[tid], job):
                    wait = fleet[other][1] - now
                    self.annotations.append({
                        "type": "HOLD", "job": job.id, "tractor": other,
                        "reason": f"Kept for {other} (free in {wait} min); giving it to idle {tid} "
                                  f"would cost more travel or delay other work."})
                    return

    def _explain(self, state, tid, job, assignment, plan_steps, fleet, planner, now):
        """Numbers behind the choice: travel, slack, queue, and alternatives."""
        alternatives, rejected = [], []
        for other, (tractor, free_at) in sorted(fleet.items()):
            if other == tid:
                continue
            if not compatible(tractor, job):
                rejected.append({"tractor": other, "reason": "missing required implement or capability"})
                continue
            result = planner.simulate(tractor, free_at, [job])
            if result is None:
                rejected.append({"tractor": other, "reason": "no safe route or not enough fuel"})
            else:
                step = result[1][0]
                alternatives.append({"tractor": other, "free_in": free_at - now,
                                     "est_finish": round(step["finish"], 1)})
        for broken in sorted(t.id for t in state.tractors.values() if t.state == TractorStatus.BROKEN):
            rejected.append({"tractor": broken, "reason": "broken down"})
        queue = [s["job"].id for s in plan_steps.get(tid, [])]
        tractor = state.tractors[tid]
        _, return_length = state.navigation.route(job.field, "DEPOT")
        fuel_required = ((assignment.est_start - now + return_length / tractor.speed) * tractor.burn_travel
                         + job.remaining / tractor.work_rate * tractor.burn_work + tractor.reserve)
        return {"score": None, "priority": job.priority,
                "fuel_at_assignment": tractor.fuel, "fuel_required": fuel_required,
                "travel_minutes": assignment.est_start - now,
                "deadline_slack": job.deadline - assignment.est_finish,
                "at_risk": assignment.est_finish > job.deadline + EPS,
                "queue": queue, "alternatives": alternatives, "rejected": rejected}
