"""Minute-tick simulation; dispatchers, not this module, choose jobs."""

import heapq
from itertools import count

from fleet.dispatchers.base import Dispatcher
from fleet.dispatchers.fcfs import compatible
from fleet.eventlog import EventLog
from fleet.metrics import consistency_errors
from fleet.models import Assignment, Event, Job, JobStatus, SimState, Tractor, TractorStatus
from fleet.scenario import validate_job
from fleet.safety import check as check_assignment, UnsafeAssignmentError

EPS = 1e-9


class Simulation:
    """Advance exact fractional movement/work inside deterministic minute ticks."""

    def __init__(self, state: SimState, events: list[Event], dispatcher: Dispatcher):
        self.state = state
        self.dispatcher = dispatcher
        self.log = EventLog()
        self._sequence = count()
        self._events: list[tuple[int, int, Event]] = []
        self._started = False
        self._decision_needed = True
        self._annotation_cache = {}
        for job in sorted(state.jobs.values(), key=lambda job: (job.submitted_at, job.id)):
            self.schedule(Event(job.submitted_at, "job_ready", {"job": job.id}))
        for event in events:
            self.schedule(event)

    def schedule(self, event: Event) -> None:
        """Queue an event; equal-time events retain scenario insertion order."""
        if type(event.at) is not int or event.at < self.state.now:
            raise ValueError("Event time must be an integer at or after the current time")
        heapq.heappush(self._events, (event.at, next(self._sequence), event))

    def start(self) -> None:
        """Apply time-zero events, then ask the dispatcher for initial work."""
        if not self._started:
            self._started = True
            self.log.add(0, "RUN_STARTED", dispatcher=self.dispatcher.name, seed=self.state.seed)
            self._boundary()

    def process_current_events(self) -> None:
        """Apply operator events and replan now, even when playback is paused."""
        self.start()
        self._boundary()

    def step(self) -> None:
        """Advance one minute; finish prior work before same-time external events."""
        self.start()
        if self.state.now >= self.state.horizon:
            return
        for tractor in sorted(self.state.tractors.values(), key=lambda item: item.id):
            self._advance(tractor, self.state.now, 1.0)
        # Per-tractor advancement is independent. Sort same-tick logs by time;
        # stable insertion order breaks ties by tractor id.
        self.log.records.sort(key=lambda record: record["t"])
        self.state.now += 1
        self._boundary()

    def run(self) -> SimState:
        """Run through the configured horizon, including events after work ends."""
        self.start()
        while self.state.now < self.state.horizon:
            self.step()
        self.check_invariants()
        self.log.add(self.state.now, "RUN_FINISHED", completed=len(self.state.completed))
        return self.state

    def _boundary(self) -> None:
        operator_batches = {}
        while self._events and self._events[0][0] == self.state.now:
            _, _, event = heapq.heappop(self._events)
            batch = event.details.get("operator_batch")
            if batch is not None:
                operator_batches.setdefault(batch, []).append(event)
                continue
            self._apply_event(event)
            self._decision_needed = True
            self.check_invariants()
        self._dispatch()
        for events in operator_batches.values():
            for event in events:
                self._apply_event(event)
                self._decision_needed = True
                self.check_invariants()
            self._dispatch()
        self.check_invariants()

    def _dispatch(self) -> None:
        if self._decision_needed and self.state.now < self.state.horizon:
            self._decision_needed = False
            self.state.excluded_pairs.clear()
            try:
                limit = len(self.state.tractors) * (len(self.state.jobs) + 1) + 1
                for _ in range(limit):
                    assignments = self.dispatcher.decide(self.state, self.state.now)
                    for annotation in getattr(self.dispatcher, "annotations", []):
                        job = self.state.jobs[annotation["job"]]
                        kind = annotation["type"]
                        if kind in {"AT_RISK", "RISK_CLEARED"}:
                            job.at_risk = kind == "AT_RISK"
                        cache_key = (job.id, "risk" if kind in {"AT_RISK", "RISK_CLEARED"} else kind)
                        if self._annotation_cache.get(cache_key) != annotation:
                            self._annotation_cache[cache_key] = dict(annotation)
                            self.log.add(self.state.now, kind, job=job.id,
                                         **{key: value for key, value in annotation.items() if key not in {"type", "job"}})
                    rejected = False
                    for assignment in assignments:
                        pair = (assignment.tractor_id, assignment.job_id)
                        if pair in self.state.excluded_pairs:
                            continue
                        try:
                            self.apply_assignment(assignment)
                        except UnsafeAssignmentError:
                            self.state.excluded_pairs.add(pair)
                            rejected = True
                    if not rejected:
                        break
            finally:
                self.state.excluded_pairs.clear()
        self.check_invariants()

    def apply_assignment(self, assignment: Assignment) -> None:
        """Apply only proposals accepted by the independent Safety Agent."""
        result = check_assignment(assignment, self.state)
        if not result.ok:
            self.state.safety_violations += 1
            self.log.add(self.state.now, "SAFETY_REJECTED", tractor=assignment.tractor_id,
                         job=assignment.job_id, violations=result.violations,
                         message="Rejected assignment: " + "; ".join(result.violations))
            raise UnsafeAssignmentError("; ".join(result.violations))
        if result.warnings:
            self.log.add(self.state.now, "SAFETY_WARNING", tractor=assignment.tractor_id,
                         job=assignment.job_id, warnings=result.warnings,
                         message="; ".join(result.warnings))
        tractor = self.state.tractors[assignment.tractor_id]
        route = assignment.planned_route
        if assignment.action == "JOB":
            job = self.state.jobs[assignment.job_id]
            job.state = JobStatus.ASSIGNED
            job.at_risk = assignment.est_finish > job.deadline + EPS
            if job.recovery_history and job.recovery_history[-1]["to_tractor"] is None:
                recovery = job.recovery_history[-1]
                recovery["to_tractor"] = tractor.id
                recovery["reassigned_at"] = self.state.now
                self.log.add(self.state.now, "RECOVERY_ASSIGNED", tractor=tractor.id, job=job.id,
                             from_tractor=recovery["from_tractor"], acres_remaining=job.remaining,
                             acres_preserved=recovery["acres_done"],
                             message=f"{tractor.id} resumes {job.id}: {job.remaining:g} acres remain; "
                             f"{recovery['acres_done']:g} completed acres were preserved.")
        tractor.assignment = assignment
        tractor.waiting_reason = None
        tractor.state = TractorStatus.TRAVELING
        self.log.add(self.state.now, "ASSIGNED", tractor=tractor.id, job=assignment.job_id,
                     action=assignment.action, route=route,
                     est_start=assignment.est_start, est_finish=assignment.est_finish,
                     explanation=assignment.explanation)
        if len(route) == 1:
            self._arrive(tractor, self.state.now)

    def _arrive(self, tractor: Tractor, at: float) -> None:
        assignment = tractor.assignment
        if assignment.action == "REFUEL":
            tractor.state = TractorStatus.REFUELING
            assignment.refuel_remaining = self.state.refuel_minutes
            self.log.add(at, "REFUEL_STARTED", tractor=tractor.id)
        else:
            job = self.state.jobs[assignment.job_id]
            finish = at + job.remaining / tractor.work_rate
            occupied = any(
                other.id != tractor.id and other.state == TractorStatus.WORKING
                and self.state.jobs[other.assignment.job_id].field == job.field
                and at < self.state.now + self.state.jobs[other.assignment.job_id].remaining / other.work_rate - EPS
                for other in self.state.tractors.values())
            occupied |= any(entry["end"] > at + EPS and entry["start"] < finish - EPS
                            for other_job in self.state.jobs.values() if other_job.field == job.field
                            for entry in other_job.history if entry["tractor"] != tractor.id)
            if occupied:
                job.state = JobStatus.PENDING
                self._release(tractor)
                tractor.waiting_reason = f"Waiting for exclusive work access to {job.field}."
                self.log.add(at, "FIELD_WAIT", tractor=tractor.id, job=job.id,
                             message=tractor.waiting_reason)
                return
            tractor.state = TractorStatus.WORKING
            job.state = JobStatus.IN_PROGRESS
            self.log.add(at, "WORK_STARTED", tractor=tractor.id, job=assignment.job_id)

    def _release(self, tractor: Tractor) -> None:
        tractor.assignment = None
        tractor.state = TractorStatus.IDLE
        self._decision_needed = True

    def _burn(self, tractor: Tractor, amount: float) -> None:
        if amount > tractor.fuel + EPS:
            raise RuntimeError(f"{tractor.id} exhausted fuel; assignment was infeasible")
        tractor.fuel = max(0, tractor.fuel - amount)

    def _refresh_route(self, tractor: Tractor, at: float) -> None:
        """Route around a closure, preserving an edge already being traversed."""
        assignment = tractor.assignment
        if assignment is None or tractor.state == TractorStatus.REFUELING:
            return
        navigation = self.state.navigation
        job = self.state.jobs[assignment.job_id] if assignment.job_id else None
        target = job.field if job else "DEPOT"
        mid_edge = tractor.state == TractorStatus.TRAVELING and assignment.edge_progress > EPS
        committed = None
        committed_remaining = 0.0
        if mid_edge:
            a, b = assignment.planned_route[assignment.route_index:assignment.route_index + 2]
            committed = (a, b)
            committed_remaining = navigation.adjacency[a][b] - assignment.edge_progress
            origin = b
        else:
            origin = tractor.location
        path, distance = navigation.route(origin, target)
        travel = (committed_remaining + distance) / tractor.speed
        work = job.remaining / tractor.work_rate if job else 0
        return_path, return_distance = navigation.route(target, "DEPOT")
        needed = travel * tractor.burn_travel + work * tractor.burn_work
        if job:
            needed += return_distance / tractor.speed * tractor.burn_travel
        reason = None
        if not path or not return_path:
            reason = "No open route to the destination and back to the depot."
        elif tractor.fuel - needed < tractor.reserve - EPS:
            reason = "The detour needs more fuel than available after keeping the return reserve."
        if reason:
            if mid_edge:
                # Exit the entered edge without teleporting; reassess at its end.
                assignment.planned_route = list(committed)
                assignment.route_index = 0
                assignment.reroute = True
                assignment.route_pending = True
                tractor.waiting_reason = reason
                self.log.add(at, "REROUTE_PENDING", tractor=tractor.id, job=assignment.job_id,
                             reason=reason, message=reason + " Finishing the current road before stopping.")
            else:
                if job:
                    job.state = JobStatus.PENDING
                    job.at_risk = True
                self.log.add(at, "ROUTE_UNAVAILABLE", tractor=tractor.id, job=assignment.job_id, reason=reason)
                self._release(tractor)
                tractor.waiting_reason = reason
            return
        old_route = list(assignment.planned_route)
        assignment.planned_route = ([committed[0]] + path) if mid_edge else path
        assignment.route_index = 0
        if not mid_edge:
            assignment.edge_progress = 0
        assignment.reroute = False
        assignment.route_pending = False
        tractor.waiting_reason = None
        if tractor.state != TractorStatus.WORKING:
            assignment.est_start = at + travel
        assignment.est_finish = at + travel + (work if job else self.state.refuel_minutes)
        if job:
            job.at_risk = assignment.est_finish > job.deadline + EPS
        if old_route != assignment.planned_route:
            self.log.add(at, "REROUTED", tractor=tractor.id, job=assignment.job_id,
                         route=assignment.planned_route, est_finish=assignment.est_finish)

    def _advance(self, tractor: Tractor, at: float, budget: float) -> None:
        while budget > EPS:
            if tractor.state == TractorStatus.BROKEN:
                return
            if tractor.state == TractorStatus.IDLE:
                tractor.idle_intervals.append((at, at + budget))
                return
            assignment = tractor.assignment
            if tractor.state == TractorStatus.TRAVELING:
                if assignment.edge_progress <= EPS and assignment.reroute:
                    self._refresh_route(tractor, at)
                    if tractor.assignment is None:
                        continue
                route = assignment.planned_route
                if assignment.route_index == len(route) - 1:
                    self._arrive(tractor, at)
                    continue
                a, b = route[assignment.route_index:assignment.route_index + 2]
                length = self.state.navigation.adjacency[a][b]
                minutes = min(budget, (length - assignment.edge_progress) / tractor.speed)
                self._burn(tractor, minutes * tractor.burn_travel)
                tractor.travel_minutes += minutes
                assignment.edge_progress += minutes * tractor.speed
                if assignment.edge_progress >= length - EPS:
                    assignment.route_index += 1
                    assignment.edge_progress = 0
                    tractor.location = b
                at += minutes
                budget -= minutes
                if assignment.edge_progress <= EPS and assignment.reroute:
                    self._refresh_route(tractor, at)
                if tractor.assignment is not None and assignment.route_index == len(assignment.planned_route) - 1:
                    self._arrive(tractor, at)
                continue
            if tractor.state == TractorStatus.REFUELING:
                minutes = min(budget, assignment.refuel_remaining)
                assignment.refuel_remaining -= minutes
                at += minutes
                budget -= minutes
                if assignment.refuel_remaining <= EPS:
                    tractor.fuel = tractor.fuel_capacity
                    self.log.add(at, "REFUEL_FINISHED", tractor=tractor.id, fuel=tractor.fuel)
                    self._release(tractor)
                continue
            job = self.state.jobs[assignment.job_id]
            minutes = min(budget, job.remaining / tractor.work_rate)
            acres = min(job.remaining, minutes * tractor.work_rate)
            self._burn(tractor, minutes * tractor.burn_work)
            job.acres_done += acres
            job.history.append({"tractor": tractor.id, "start": at, "end": at + minutes, "acres": acres})
            at += minutes
            budget -= minutes
            if job.remaining <= EPS:
                job.acres_done = job.acres_total
                job.state = JobStatus.DONE
                job.finished_at = at
                job.late = at > job.deadline + EPS
                self.state.completed.append(job.id)
                self.log.add(at, "JOB_DONE", tractor=tractor.id, job=job.id,
                             acres_done=job.acres_done, late=job.late)
                self._release(tractor)

    def _apply_event(self, event: Event) -> None:
        details = event.details
        now = self.state.now
        if event.type == "job_ready":
            self.log.add(now, "JOB_SUBMITTED", job=details["job"])
        elif event.type == "submit_job":
            values = dict(details["job"])
            values.setdefault("submitted_at", now)
            job = Job(**values)
            validate_job(job, self.state)
            if job.id in self.state.jobs or job.submitted_at != now:
                raise ValueError("Invalid submitted job id or time")
            self.state.jobs[job.id] = job
            self.log.add(now, "JOB_SUBMITTED", job=job.id)
        elif event.type == "breakdown":
            tractor = self.state.tractors[details["tractor"]]
            assignment = tractor.assignment
            job = self.state.jobs[assignment.job_id] if assignment and assignment.job_id else None
            if job is not None:
                job.state = JobStatus.PENDING
                job.recovery_history.append({"at": now, "from_tractor": tractor.id,
                                             "acres_done": job.acres_done, "acres_remaining": job.remaining,
                                             "to_tractor": None, "reassigned_at": None})
            # Mid-road failures stay at the physical interpolated position.
            if assignment and tractor.state == TractorStatus.TRAVELING and assignment.edge_progress > EPS:
                self._failure_node(tractor)
            tractor.assignment = None
            tractor.state = TractorStatus.BROKEN
            tractor.waiting_reason = None
            self.log.add(now, "BREAKDOWN", tractor=tractor.id, job=job.id if job else None,
                         acres_done=job.acres_done if job else 0,
                         message=f"{tractor.id} BROKE DOWN; " + (
                             f"{job.id} paused at {100 * job.acres_done / job.acres_total:.1f}% "
                             f"({job.acres_done:g}/{job.acres_total:g} acres)" if job else "no active job"))
            if job and not any(t.state != TractorStatus.BROKEN and compatible(t, job)
                               for t in self.state.tractors.values()):
                job.at_risk = True
                self.log.add(now, "RECOVERY_WAITING", job=job.id, acres_remaining=job.remaining,
                             message=f"{job.id} remains pending and at risk: no non-broken tractor has "
                             "the required equipment and capabilities. Completed work is preserved.")
        elif event.type == "repair":
            tractor = self.state.tractors[details["tractor"]]
            if tractor.state == TractorStatus.BROKEN:
                self._release(tractor)
                tractor.waiting_reason = None
                self.log.add(now, "REPAIRED", tractor=tractor.id)
        elif event.type in {"block_route", "unblock_route"}:
            function = self.state.navigation.block if event.type == "block_route" else self.state.navigation.unblock
            function(details["a"], details["b"])
            for tractor in self.state.tractors.values():
                if tractor.state in {TractorStatus.TRAVELING, TractorStatus.WORKING}:
                    self._refresh_route(tractor, now)
                elif tractor.state == TractorStatus.IDLE:
                    tractor.waiting_reason = None
            self.log.add(now, event.type.upper(), **details)
        elif event.type == "change_priority":
            job = self.state.jobs[details["job"]]
            if type(details["priority"]) is not int or not 1 <= details["priority"] <= 5:
                raise ValueError("Priority must be 1..5")
            job.priority = details["priority"]
            self.log.add(now, "PRIORITY_CHANGED", job=job.id, priority=job.priority)
        else:
            raise ValueError(f"Unknown event: {event.type}")

    def _failure_node(self, tractor: Tractor) -> None:
        assignment = tractor.assignment
        a, b = assignment.planned_route[assignment.route_index:assignment.route_index + 2]
        navigation = self.state.navigation
        length = navigation.adjacency[a][b]
        fraction = assignment.edge_progress / length
        start, end = navigation.nodes[a], navigation.nodes[b]
        node_id = f"BREAKDOWN_{tractor.id}_{self.state.now}"
        from fleet.models import Field
        node = Field(node_id, f"{tractor.name} breakdown", start.x + fraction * (end.x - start.x),
                     start.y + fraction * (end.y - start.y))
        # A virtual shoulder node permits repair without teleporting. The
        # original road is retained; other tractors cannot shortcut through it.
        navigation.nodes[node_id] = node
        navigation.adjacency[node_id] = {a: assignment.edge_progress, b: length - assignment.edge_progress}
        tractor.location = node_id

    def position(self, tractor_id: str) -> tuple[float, float]:
        """Return current road-interpolated coordinates for a future dashboard."""
        tractor = self.state.tractors[tractor_id]
        node = self.state.fields[tractor.location]
        assignment = tractor.assignment
        if tractor.state != TractorStatus.TRAVELING or not assignment or assignment.edge_progress <= EPS:
            return node.x, node.y
        a, b = assignment.planned_route[assignment.route_index:assignment.route_index + 2]
        start, end = self.state.fields[a], self.state.fields[b]
        fraction = assignment.edge_progress / self.state.navigation.adjacency[a][b]
        return start.x + fraction * (end.x - start.x), start.y + fraction * (end.y - start.y)

    def check_invariants(self) -> None:
        """Log and raise on inconsistent ownership, work history, or completion."""
        errors = consistency_errors(self.state)
        if errors:
            self.log.add(self.state.now, "INVARIANT_FAILED", violations=errors,
                         message="Simulation paused: " + "; ".join(errors))
            raise AssertionError("; ".join(errors))
