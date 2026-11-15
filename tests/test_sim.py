"""Physics, timing, input validation, and reproducibility checks."""

from copy import deepcopy
from pathlib import Path

import pytest

from fleet.dispatchers.base import NoopDispatcher
from fleet.models import Assignment, JobStatus, TractorStatus
from fleet.navigation import Navigation
from fleet.scenario import load_scenario, scenario_from_dict
from fleet.sim import Simulation


class ScriptedDispatcher:
    name = "scripted"

    def __init__(self, action="JOB"):
        self.action = action
        self.called = []
        self.assigned = False

    def decide(self, state, now):
        self.called.append(now)
        if self.assigned:
            return []
        self.assigned = True
        target = "DEPOT" if self.action == "REFUEL" else "F1"
        route, distance = state.navigation.route(state.tractors["T1"].location, target)
        tractor = state.tractors["T1"]
        travel = distance / tractor.speed
        work = state.refuel_minutes if self.action == "REFUEL" else state.jobs["J1"].remaining / tractor.work_rate
        return [Assignment("T1", "J1" if self.action == "JOB" else None, route,
                           now + travel, now + travel + work, action=self.action)]


def test_fractional_travel_work_and_fuel(make_state):
    state, events = make_state()
    dispatcher = ScriptedDispatcher()
    sim = Simulation(state, events, dispatcher)
    sim.step()
    assert sim.position("T1") == pytest.approx((2, 0))
    sim.run()
    assert next(record["t"] for record in sim.log.records if record["type"] == "WORK_STARTED") == 2.5
    assert state.jobs["J1"].finished_at == 4.5
    assert state.tractors["T1"].travel_minutes == 2.5
    assert state.tractors["T1"].fuel == pytest.approx(96.75)
    assert dispatcher.called == [0, 5]


def test_breakdown_keeps_partial_work(make_state):
    state, events = make_state(events=[{"at": 4, "type": "breakdown", "tractor": "T1"}])
    sim = Simulation(state, events, ScriptedDispatcher())
    sim.run()
    assert state.tractors["T1"].state == TractorStatus.BROKEN
    assert state.tractors["T1"].assignment is None
    assert state.jobs["J1"].state == JobStatus.PENDING
    assert state.jobs["J1"].acres_done == 3
    sim.check_invariants()


def test_midroad_breakdown_does_not_teleport(make_state):
    state, events = make_state(events=[{"at": 1, "type": "breakdown", "tractor": "T1"}])
    sim = Simulation(state, events, ScriptedDispatcher())
    sim.run()
    assert sim.position("T1") == pytest.approx((2, 0))
    assert state.jobs["J1"].acres_done == 0


def test_refuelling_takes_fifteen_minutes(make_state):
    state, events = make_state(tractors=[{"id": "T1", "name": "One", "implements": ["mower"],
                                       "speed": 2, "fuel": 20}])
    sim = Simulation(state, events, ScriptedDispatcher("REFUEL"))
    for _ in range(14):
        sim.step()
    assert state.tractors["T1"].fuel == 20
    assert state.tractors["T1"].state == TractorStatus.REFUELING
    sim.step()
    assert state.tractors["T1"].fuel == 100
    assert state.tractors["T1"].state == TractorStatus.IDLE


def test_same_seed_has_identical_logs(make_state):
    runs = []
    for _ in range(2):
        state, events = make_state(seed=19)
        sim = Simulation(state, events, ScriptedDispatcher())
        sim.run()
        runs.append(sim.log.records)
    assert runs[0] == runs[1]


def test_none_runs_full_demo_and_writes_logs(tmp_path):
    state, events = load_scenario(Path(__file__).resolve().parents[1] / "scenarios/default.json")
    sim = Simulation(state, events, NoopDispatcher())
    sim.run()
    sim.log.write(tmp_path, "none")
    assert state.now == 600
    assert len(state.tractors) == 3 and len(state.jobs) == 12
    assert state.tractors["agxt2"].state == TractorStatus.IDLE
    assert (tmp_path / "none_events.jsonl").is_file()
    assert "BREAKDOWN" not in (tmp_path / "none_events.txt").read_text(encoding="utf-8")


def test_dijkstra_and_road_closure(make_state):
    state, _ = make_state()
    navigation = state.navigation
    assert navigation.route("DEPOT", "F1") == (["DEPOT", "F1"], 5)
    navigation.block("DEPOT", "F1")
    assert navigation.route("DEPOT", "F1")[0] == []
    navigation.unblock("F1", "DEPOT")
    assert navigation.route("F1", "DEPOT")[1] == 5


def test_generated_roads_bridge_separated_clusters():
    from fleet.models import Field
    nodes = {f"N{i}": Field(f"N{i}", str(i), (i // 4) * 1000 + i, 0) for i in range(8)}
    navigation = Navigation(nodes)
    assert navigation.route("N0", "N7")[0]


@pytest.mark.parametrize("change", ["duplicate", "field", "implement", "deadline", "speed", "nan"])
def test_reject_invalid_scenarios(scenario_data, change):
    data = deepcopy(scenario_data)
    if change == "duplicate":
        data["jobs"].append(dict(data["jobs"][0]))
    elif change == "field":
        data["jobs"][0]["field"] = "missing"
    elif change == "implement":
        data["jobs"][0]["requires_implement"] = "planter"
    elif change == "deadline":
        data["jobs"][0]["deadline"] = 0
    else:
        data["tractors"][0]["speed"] = 0 if change == "speed" else float("nan")
    with pytest.raises(ValueError):
        scenario_from_dict(data)


def test_live_job_priority_and_repair_events(make_state, scenario_data):
    submitted = dict(scenario_data["jobs"][0], id="J2", submitted_at=3)
    state, events = make_state(events=[
        {"type": "breakdown", "at": 1, "tractor": "T1"},
        {"type": "repair", "at": 2, "tractor": "T1"},
        {"type": "submit_job", "at": 3, "job": submitted},
        {"type": "change_priority", "at": 4, "job": "J2", "priority": 5},
    ])
    sim = Simulation(state, events, NoopDispatcher())
    sim.run()
    assert state.tractors["T1"].state == TractorStatus.IDLE
    assert state.jobs["J2"].priority == 5
    assert [record["type"] for record in sim.log.records if record["t"] == 3] == ["JOB_SUBMITTED"]


def test_completion_precedes_breakdown_at_same_minute(make_state):
    state, events = make_state(tractors=[{"id": "T1", "name": "One", "implements": ["mower"],
                                       "speed": 1, "work_rate": 1}],
                              events=[{"type": "breakdown", "tractor": "T1", "at": 9}])
    sim = Simulation(state, events, ScriptedDispatcher())
    sim.run()
    assert state.jobs["J1"].state == JobStatus.DONE
    assert state.jobs["J1"].finished_at == 9
    assert state.tractors["T1"].state == TractorStatus.BROKEN


def test_road_block_finishes_entered_edge_then_waits_without_return_route(make_state):
    state, events = make_state(events=[{"type": "block_route", "at": 1, "a": "DEPOT", "b": "F1"}])
    sim = Simulation(state, events, ScriptedDispatcher())
    sim.run()
    assert state.jobs["J1"].finished_at is None
    assert state.jobs["J1"].state == JobStatus.PENDING
    assert sim.position("T1") == (5, 0)


def test_duplicate_assignment_is_rejected(make_state):
    state, events = make_state()
    sim = Simulation(state, events, NoopDispatcher())
    sim.start()
    sim.apply_assignment(Assignment("T1", "J1", ["DEPOT", "F1"], 2.5, 4.5))
    with pytest.raises(ValueError, match="already assigned"):
        sim.apply_assignment(Assignment("T1", "J1", ["DEPOT", "F1"], 2.5, 4.5))
