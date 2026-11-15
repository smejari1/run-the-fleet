"""Joint matching, urgency, fuel, lookahead, and honest comparison tests."""

from copy import deepcopy
import json
from pathlib import Path

from fleet.comparison import compare_runs
from fleet.dispatchers.coordinator import CoordinatorDispatcher
from fleet.models import Assignment, JobStatus, TractorStatus
from fleet.scenario import load_scenario
from fleet.sim import Simulation

ROOT = Path(__file__).resolve().parents[1]


def test_joint_matching_keeps_specialist_available(make_state, scenario_data):
    tractors = [dict(scenario_data["tractors"][0], implements=["mower", "sprayer"]),
                dict(scenario_data["tractors"][0], id="T2")]
    fields = [scenario_data["fields"][0], dict(scenario_data["fields"][0], id="F2", x=8)]
    jobs = [dict(scenario_data["jobs"][0], deadline=5),
            dict(scenario_data["jobs"][0], id="J2", field="F2", deadline=7, requires_implement="sprayer")]
    state, _ = make_state(tractors=tractors, jobs=jobs, fields=fields, edges=None)
    before = deepcopy(state.jobs)
    assignments = CoordinatorDispatcher().decide(state, 0)
    assert {(a.tractor_id, a.job_id) for a in assignments} == {("T1", "J2"), ("T2", "J1")}
    assert state.jobs == before


def test_nearer_tractors_are_matched_jointly(make_state, scenario_data):
    fields = [scenario_data["fields"][0], {"id": "F2", "name": "Far", "x": 100, "y": 0, "acres": 10}]
    tractors = [scenario_data["tractors"][0], dict(scenario_data["tractors"][0], id="T2", location="F2")]
    jobs = [dict(scenario_data["jobs"][0], deadline=200), dict(scenario_data["jobs"][0], id="J2", field="F2", deadline=200)]
    state, _ = make_state(fields=fields, edges=None, tractors=tractors, jobs=jobs)
    assignments = CoordinatorDispatcher().decide(state, 0)
    assert {(a.tractor_id, a.job_id) for a in assignments} == {("T1", "J1"), ("T2", "J2")}


def test_prefers_tight_deadline_and_priority(make_state, scenario_data):
    jobs = [dict(scenario_data["jobs"][0], deadline=500),
            dict(scenario_data["jobs"][0], id="J2", priority=5, deadline=6)]
    state, _ = make_state(jobs=jobs)
    assert CoordinatorDispatcher().decide(state, 0)[0].job_id == "J2"


def test_excludes_incompatible_and_broken_tractors(make_state, scenario_data):
    tractors = [dict(scenario_data["tractors"][0], implements=["sprayer"]),
                dict(scenario_data["tractors"][0], id="T2"),
                dict(scenario_data["tractors"][0], id="T3", capabilities=["rtk"])]
    jobs = [dict(scenario_data["jobs"][0], requires_capabilities=["rtk"])]
    state, _ = make_state(tractors=tractors, jobs=jobs)
    dispatcher = CoordinatorDispatcher()
    assert [a.tractor_id for a in dispatcher.decide(state, 0)] == ["T3"]
    state.tractors["T3"].state = TractorStatus.BROKEN
    assert dispatcher.decide(state, 0) == []


def test_late_fallback_preserves_at_risk_flag(make_state, scenario_data):
    state, events = make_state(jobs=[dict(scenario_data["jobs"][0], deadline=1)])
    sim = Simulation(state, events, CoordinatorDispatcher())
    sim.run()
    assert state.jobs["J1"].late and state.jobs["J1"].at_risk
    assert any(r["type"] == "AT_RISK" for r in sim.log.records)


def test_low_fuel_plans_refuel(make_state, scenario_data):
    state, _ = make_state(tractors=[dict(scenario_data["tractors"][0], fuel=12)])
    dispatcher = CoordinatorDispatcher()
    assignment = dispatcher.decide(state, 0)[0]
    assert assignment.action == "REFUEL"
    assert any(a["type"] == "RISK_CLEARED" for a in dispatcher.annotations)


def test_holds_for_soon_free_nearby_tractor(make_state, scenario_data):
    tractors = [dict(scenario_data["tractors"][0], speed=1, burn_travel=0),
                dict(scenario_data["tractors"][0], id="T2", location="F1", work_rate=1)]
    jobs = [dict(scenario_data["jobs"][0], deadline=200),
            dict(scenario_data["jobs"][0], id="J0", acres_total=1, deadline=200)]
    state, _ = make_state(tractors=tractors, jobs=jobs,
                          edges=[{"a": "DEPOT", "b": "F1", "length": 100}])
    state.jobs["J0"].state = JobStatus.IN_PROGRESS
    busy = state.tractors["T2"]
    busy.state = TractorStatus.WORKING
    busy.assignment = Assignment("T2", "J0", ["F1"], 0, 1)
    dispatcher = CoordinatorDispatcher()
    assert dispatcher.decide(state, 0) == []
    assert any(item["type"] == "HOLD" and item["job"] == "J1" for item in dispatcher.annotations)


def test_comparison_replays_same_input_and_exports_real_numbers(tmp_path):
    data = json.loads((ROOT / "scenarios/default.json").read_text())
    before = deepcopy(data)
    result = compare_runs(data, tmp_path)
    assert data == before
    assert result["coordinator"]["jobs_completed"] == 12
    assert result["coordinator"]["jobs_on_time"] >= result["fcfs"]["jobs_on_time"]
    assert result["coordinator"]["travel_minutes"] <= result["fcfs"]["travel_minutes"]
    assert result["coordinator"]["duplicate_or_lost_work"] == 0
    assert json.loads((tmp_path / "comparison.json").read_text()) == result
    assert json.loads((tmp_path / "comparison_input.json").read_text()) == data
    assert (tmp_path / "coordinator_events.txt").is_file()
