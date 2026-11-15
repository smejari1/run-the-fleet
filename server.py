"""Local field map for the existing simulation; no frontend build tools needed."""

import argparse
import json
import math
import threading
import time
from copy import deepcopy
from pathlib import Path

from flask import Flask, jsonify, request

from fleet.dispatchers.fcfs import FCFSDispatcher
from fleet.dispatchers.coordinator import CoordinatorDispatcher
from fleet.dispatchers.nearest import NearestDispatcher
from fleet.comparison import compare_runs
from fleet.explain import explain_job, plan_feed
from fleet.metrics import calculate_metrics
from fleet.models import Event, Job, TractorStatus
from fleet.operations import OPERATIONS
from fleet.scenario import scenario_from_dict, validate_job
from fleet.sim import Simulation

ROOT = Path(__file__).resolve().parent


class MapSession:
    """Protect one simulation shared by browser controls and a playback thread."""

    def __init__(self, scenario: Path):
        self.scenario = scenario
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.running = False
        self.speed = 4.0
        self.error = None
        self.dispatcher_name = "coordinator"
        self.generation = 0
        self.reset()

    def reset(self) -> None:
        """Restore demo jobs and clear this session's operator actions."""
        with self.lock:
            with self.scenario.open(encoding="utf-8-sig") as stream:
                self.source_data = json.load(stream)
            state, events = scenario_from_dict(deepcopy(self.source_data))
            dispatcher = {"coordinator": CoordinatorDispatcher, "fcfs": FCFSDispatcher,
                          "nearest": NearestDispatcher}[self.dispatcher_name]()
            self.simulation = Simulation(state, events, dispatcher)
            self.simulation.start()
            self.operator_events = []
            self.running = False
            self.error = None
            self.notice = "Ready. Queue jobs or inject a breakdown at any time."
            self.generation += 1

    def _require_open_shift(self) -> None:
        if self.simulation.state.now >= self.simulation.state.horizon:
            raise ValueError("This shift has ended. Reset to queue jobs or inject events.")
        if self.error:
            raise ValueError("Reset the paused simulation before adding work.")

    def _inject(self, events: list[dict]) -> None:
        # Batch metadata preserves ordering relative to the normal dispatch at
        # this minute, so full-shift comparison can replay operator actions.
        batch = len(self.operator_events) + 1
        for item in events:
            item["operator_batch"] = batch
            details = {key: value for key, value in item.items() if key not in {"type", "at"}}
            self.simulation.schedule(Event(item["at"], item["type"], details))
        try:
            self.simulation.process_current_events()
        except AssertionError:
            self.running = False
            self.error = "The simulation failed a consistency check and paused. Reset before continuing."
            raise
        self.operator_events.extend(deepcopy(events))

    def submit_jobs(self, payload) -> list[str]:
        """Validate a whole job batch before changing the queue or auto-assigning."""
        with self.lock:
            self._require_open_shift()
            state = self.simulation.state
            items = payload.get("jobs") if isinstance(payload, dict) and "jobs" in payload else payload
            items = items if isinstance(items, list) else [items]
            if not items or len(items) > 50 or len(state.jobs) + len(items) > 100:
                raise ValueError("Submit 1-50 jobs per batch, up to 100 jobs per shift.")
            reserved = set(state.jobs)
            records = []
            allowed = {"id", "type", "field", "acres_total", "priority", "deadline", "deadline_after",
                       "submitted_at", "requires_implement", "requires_capabilities"}
            for item in items:
                if not isinstance(item, dict) or set(item) - allowed:
                    raise ValueError("Job input must contain only job description fields.")
                operation = item.get("type")
                if not isinstance(operation, str) or operation not in OPERATIONS:
                    raise ValueError("Choose plowing, irrigation, fertilizing, harvesting, planting, or spraying.")
                next_id = len(reserved) + 1
                while f"J{next_id:02}" in reserved:
                    next_id += 1
                job_id = item.get("id", f"J{next_id:02}")
                if not isinstance(job_id, str) or not job_id or len(job_id) > 40 or job_id in reserved:
                    raise ValueError("Job ids must be unique nonempty strings, at most 40 characters.")
                if item.get("submitted_at", state.now) != state.now:
                    raise ValueError("Queued jobs must be submitted at the current simulation minute.")
                if item.get("requires_implement", OPERATIONS[operation]) != OPERATIONS[operation]:
                    raise ValueError("The implement must match the selected operation.")
                deadline = item.get("deadline")
                if deadline is None:
                    delay = item.get("deadline_after", 180)
                    if type(delay) is not int or delay < 1:
                        raise ValueError("Deadline from now must be a positive whole number of minutes.")
                    deadline = state.now + delay
                record = {"id": job_id, "type": operation, "field": item.get("field"),
                          "requires_implement": OPERATIONS[operation],
                          "requires_capabilities": item.get("requires_capabilities", []),
                          "priority": item.get("priority", 3), "deadline": deadline,
                          "submitted_at": state.now, "acres_total": item.get("acres_total")}
                if not isinstance(record["field"], str):
                    raise ValueError("Choose a valid field.")
                validate_job(Job(**record), state)
                records.append(record)
                reserved.add(job_id)
            self._inject([{"type": "submit_job", "at": state.now, "job": record} for record in records])
            ids = [record["id"] for record in records]
            self.notice = f"Added {', '.join(ids)} to the queue. Available tractors have been reconsidered."
            return ids

    def inject_event(self, payload: dict) -> None:
        """Apply a tractor or road incident immediately without advancing time."""
        with self.lock:
            self._require_open_shift()
            if not isinstance(payload, dict):
                raise ValueError("Send a tractor and an event type.")
            kind, tractor_id = payload.get("type"), payload.get("tractor")
            if kind == "change_priority":
                job_id, priority = payload.get("job"), payload.get("priority")
                state = self.simulation.state
                if not isinstance(job_id, str) or job_id not in state.jobs:
                    raise ValueError("Choose a valid pending job.")
                job = state.jobs[job_id]
                if job.state != "PENDING" or job.submitted_at > state.now:
                    raise ValueError("Change priority on a submitted pending job; active work is not interrupted.")
                if type(priority) is not int or not 1 <= priority <= 5:
                    raise ValueError("Priority must be a whole number from 1 to 5.")
                self._inject([{"type": kind, "job": job_id, "priority": priority, "at": state.now}])
                self.notice = f"{job_id} priority changed to {priority}. All pending work was reconsidered; active jobs continue."
                return
            if isinstance(kind, str) and kind in {"block_route", "unblock_route"}:
                a, b = payload.get("a"), payload.get("b")
                if not isinstance(a, str) or not isinstance(b, str):
                    raise ValueError("Choose a valid road.")
                navigation = self.simulation.state.navigation
                key = tuple(sorted((a, b)))
                if key not in navigation.edges:
                    raise ValueError("That road does not exist.")
                if (kind == "block_route") == (key in navigation.blocked):
                    raise ValueError("That road is already blocked." if kind == "block_route" else "That road is already open.")
                self._inject([{"type": kind, "a": a, "b": b, "at": self.simulation.state.now}])
                self.notice = f"Road {a}–{b} {'blocked' if kind == 'block_route' else 'reopened'}. " + (
                    "Tractors use the shortest open alternative; detours must keep sufficient fuel. Entered roads may be exited."
                    if kind == "block_route" else "Routes and pending jobs have been reconsidered.")
                return
            if not isinstance(kind, str) or kind not in {"breakdown", "repair"}:
                raise ValueError("Choose breakdown, repair, block_route, unblock_route, or change_priority.")
            if not isinstance(tractor_id, str) or tractor_id not in self.simulation.state.tractors:
                raise ValueError("Choose a valid tractor.")
            tractor = self.simulation.state.tractors[tractor_id]
            if (kind == "breakdown") == (tractor.state == TractorStatus.BROKEN):
                raise ValueError("That tractor is already broken." if kind == "breakdown" else "That tractor is not broken.")
            assignment = tractor.assignment
            job = self.simulation.state.jobs[assignment.job_id] if assignment and assignment.job_id else None
            self._inject([{"type": kind, "tractor": tractor_id, "at": self.simulation.state.now}])
            if kind == "repair":
                self.notice = f"{tractor_id} repaired and available. The queue has been reconsidered."
            else:
                self.notice = f"{tractor_id} broke down. " + (
                    f"{job.id} keeps {job.acres_done:.1f}/{job.acres_total:g} completed acres; its remainder is back in the queue for reassignment."
                    if job else "No active job was lost.")

    def replay_input(self) -> tuple[dict, int]:
        """Capture the same immutable scenario and operator timeline for both runs."""
        with self.lock:
            scenario = deepcopy(self.source_data)
            scenario.setdefault("events", []).extend(deepcopy(self.operator_events))
            return scenario, self.simulation.state.now

    def step(self) -> None:
        """Advance once under the lock and stop at the horizon."""
        with self.lock:
            try:
                self.simulation.step()
            except Exception:
                self.running = False
                self.error = "The simulation paused after an error. Reset to start again."
                raise
            if self.simulation.state.now >= self.simulation.state.horizon:
                self.running = False

    def play(self) -> None:
        """Drive playback; wall time changes playback speed, never simulation rules."""
        previous = time.monotonic()
        credit = 0.0
        while not self.stop_event.wait(0.05):
            current = time.monotonic()
            elapsed, previous = current - previous, current
            with self.lock:
                if not self.running:
                    credit = 0
                    continue
                credit += min(elapsed, 0.25) * self.speed
                while credit >= 1 and self.running:
                    try:
                        self.step()
                    except Exception:
                        # Keep the HTTP server responsive so Reset remains usable.
                        break
                    credit -= 1

    def snapshot(self) -> dict:
        """Serialize real positions, assignments and progress for the map."""
        with self.lock:
            sim = self.simulation
            state = sim.state
            holders = {t.assignment.job_id: t.id for t in state.tractors.values() if t.assignment and t.assignment.job_id}
            jobs = [{"id": job.id, "field": job.field, "type": job.type,
                     "state": job.state, "acres_done": job.acres_done,
                     "acres_total": job.acres_total, "submitted_at": job.submitted_at,
                     "deadline": job.deadline, "late": job.late, "at_risk": job.at_risk,
                     "priority": job.priority, "tractor": holders.get(job.id),
                     "recovery_count": len(job.recovery_history),
                     "recovery": deepcopy(job.recovery_history[-1]) if job.recovery_history else None,
                     "progress": job.acres_done / job.acres_total}
                    for job in state.jobs.values()]
            fields = []
            for node in state.fields.values():
                if node.id == "DEPOT" or node.id.startswith("BREAKDOWN_"):
                    continue
                work = [job for job in jobs if job["field"] == node.id]
                total = sum(job["acres_total"] for job in work)
                fields.append({"id": node.id, "name": node.name, "x": node.x, "y": node.y,
                               "acres": node.acres, "jobs": work,
                               "progress": sum(job["acres_done"] for job in work) / total if total else 0})
            tractors = []
            for tractor in state.tractors.values():
                x, y = sim.position(tractor.id)
                assignment = tractor.assignment
                tractors.append({"id": tractor.id, "name": tractor.name, "x": x, "y": y,
                                 "state": tractor.state, "fuel": tractor.fuel,
                                 "fuel_capacity": tractor.fuel_capacity, "implements": tractor.implements,
                                 "job": assignment.job_id if assignment else None,
                                 "waiting_reason": tractor.waiting_reason,
                                 "action": assignment.action if assignment else None,
                                 "route": assignment.planned_route[assignment.route_index:] if assignment else [],
                                 "est_finish": assignment.est_finish if assignment and not assignment.route_pending else None})
            depot = state.fields["DEPOT"]
            return {"time": state.now, "horizon": state.horizon, "running": self.running,
                    "speed": self.speed, "error": self.error, "dispatcher": self.dispatcher_name,
                    "notice": self.notice, "generation": self.generation,
                    "safety": {"enabled": True, "rejections": state.safety_violations,
                               "warnings": sum(record["type"] == "SAFETY_WARNING" for record in sim.log.records)},
                    "operator_events": len(self.operator_events), "operations": list(OPERATIONS),
                    "fields": fields, "tractors": tractors, "jobs": jobs,
                    "depot": {"id": "DEPOT", "x": depot.x, "y": depot.y},
                    "nodes": {key: {"x": node.x, "y": node.y} for key, node in state.fields.items()},
                    "edges": [{"a": a, "b": b, "blocked": (a, b) in state.navigation.blocked}
                              for a, b in state.navigation.edges],
                    "metrics": calculate_metrics(state)}


def create_app(scenario: Path | None = None) -> Flask:
    """Create an isolated local map app; tests do not start background threads."""
    app = Flask(__name__, static_folder=str(ROOT / "web"), static_url_path="/static")
    session = MapSession(scenario or ROOT / "scenarios/default.json")
    app.extensions["map_session"] = session

    @app.errorhandler(AssertionError)
    def integrity_failure(error):
        session.running = False
        session.error = "The simulation failed a consistency check and paused. Reset before continuing."
        return jsonify(error=session.error), 503

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    @app.get("/api/state")
    def state():
        response = jsonify(session.snapshot())
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/api/control")
    def control():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(error="Send a JSON object."), 400
        action = data.get("action")
        if not isinstance(action, str) or action not in {"start", "pause", "step", "reset", "speed"}:
            return jsonify(error="Unknown playback action."), 400
        speed = data.get("speed", session.speed)
        if (isinstance(speed, bool) or not isinstance(speed, (float, int))
                or not math.isfinite(speed) or not 1 <= speed <= 60):
            return jsonify(error="Speed must be between 1 and 60 simulated minutes per second."), 400
        dispatcher = data.get("dispatcher", session.dispatcher_name)
        if not isinstance(dispatcher, str) or dispatcher not in {"fcfs", "nearest", "coordinator"}:
            return jsonify(error="Choose FCFS, Nearest feasible, or Coordinator."), 400
        if dispatcher != session.dispatcher_name and action != "reset":
            return jsonify(error="Change dispatcher with Reset to start a fair new run."), 400
        with session.lock:
            session.speed = float(speed)
            if action == "reset":
                session.dispatcher_name = dispatcher
                session.reset()
            elif action == "start":
                session.running = not session.error and session.simulation.state.now < session.simulation.state.horizon
            elif action in {"pause", "step"}:
                session.running = False
                if action == "step":
                    session.step()
            return jsonify(session.snapshot())

    @app.post("/api/jobs")
    def jobs():
        try:
            ids = session.submit_jobs(request.get_json(silent=True))
        except (ValueError, TypeError) as error:
            return jsonify(error=str(error)), 400
        return jsonify(**session.snapshot(), added_job_ids=ids), 201

    @app.post("/api/event")
    def event():
        try:
            session.inject_event(request.get_json(silent=True))
        except (ValueError, TypeError) as error:
            return jsonify(error=str(error)), 400
        return jsonify(session.snapshot())

    @app.post("/api/compare")
    def compare():
        scenario, captured_at = session.replay_input()
        result = compare_runs(scenario)
        result["captured_at"] = captured_at
        return jsonify(result)

    @app.get("/api/replay")
    def replay():
        scenario, _ = session.replay_input()
        response = jsonify(scenario)
        response.headers["Content-Disposition"] = 'attachment; filename="fleet_replay.json"'
        return response

    # Phase 5: plain-language explanations ("Why?" and the plan-updates feed).
    @app.get("/api/why/<job_id>")
    def why(job_id):
        with session.lock:
            state = session.simulation.state
            if job_id not in state.jobs:
                return jsonify(error="Unknown job."), 404
            response = jsonify(**explain_job(state, session.simulation.log.records, job_id),
                               time=state.now, generation=session.generation)
            response.headers["Cache-Control"] = "no-store"
            return response

    @app.get("/api/explanations")
    def explanations():
        with session.lock:
            sim = session.simulation
            response = jsonify(feed=plan_feed(sim.state, sim.log.records), time=sim.state.now,
                               generation=session.generation)
        response.headers["Cache-Control"] = "no-store"
        return response

    return app


def main() -> None:
    """Serve the interactive map on this computer only."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, default=ROOT / "scenarios/default.json")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--autoplay", action="store_true")
    args = parser.parse_args()
    app = create_app(args.scenario)
    session = app.extensions["map_session"]
    session.running = args.autoplay
    worker = threading.Thread(target=session.play, daemon=True, name="fleet-playback")
    worker.start()
    print(f"Field map: http://127.0.0.1:{args.port}", flush=True)
    try:
        app.run(host="127.0.0.1", port=args.port, debug=False, use_reloader=False)
    finally:
        session.stop_event.set()
        worker.join(timeout=2)


if __name__ == "__main__":
    main()
