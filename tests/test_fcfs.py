"""Baseline ordering, feasibility, and exclusive job ownership."""

from pathlib import Path

from fleet.dispatchers.fcfs import FCFSDispatcher, estimate_job
from fleet.metrics import calculate_metrics, consistency_errors
from fleet.models import JobStatus
from fleet.scenario import load_scenario
from fleet.sim import Simulation


def test_submission_order_then_job_id(make_state, scenario_data):
    original = scenario_data["jobs"][0]
    jobs = [dict(original, id="J3", submitted_at=1, priority=5, deadline=3),
            dict(original, id="J2"), dict(original, id="J1")]
    state, events = make_state(jobs=jobs)
    sim = Simulation(state, events, FCFSDispatcher())
    sim.run()
    assigned = [record["job"] for record in sim.log.records if record["type"] == "ASSIGNED"]
    assert assigned == ["J1", "J2", "J3"]
    assert state.jobs["J3"].late  # Priority and deadline never reorder FCFS.


def test_skips_missing_implement_and_capability(make_state, scenario_data):
    tractors = [dict(scenario_data["tractors"][0], id="T1", implements=["sprayer"]),
                dict(scenario_data["tractors"][0], id="T2"),
                dict(scenario_data["tractors"][0], id="T3", capabilities=["rtk"])]
    job = dict(scenario_data["jobs"][0], requires_capabilities=["rtk"])
    state, _ = make_state(tractors=tractors, jobs=[job])
    assignments = FCFSDispatcher().decide(state, 0)
    assert [(a.tractor_id, a.job_id) for a in assignments] == [("T3", "J1")]


def test_reserves_jobs_within_one_decision(make_state, scenario_data):
    tractors = [dict(scenario_data["tractors"][0], id=f"T{i}") for i in range(3)]
    fields = [dict(scenario_data["fields"][0], id=f"F{i+1}", x=5*(i+1)) for i in range(5)]
    jobs = [dict(scenario_data["jobs"][0], id=f"J{i}", field=f"F{i+1}") for i in range(5)]
    state, events = make_state(tractors=tractors, jobs=jobs, fields=fields, edges=None)
    dispatcher = FCFSDispatcher()
    proposals = dispatcher.decide(state, 0)
    assert len({a.tractor_id for a in proposals}) == len(proposals) == 3
    assert len({a.job_id for a in proposals}) == 3
    assert all(job.state == JobStatus.PENDING for job in state.jobs.values())  # No mutation.
    sim = Simulation(state, events, dispatcher)
    for _ in range(state.horizon):
        sim.step()
        sim.check_invariants()
    assert len(state.completed) == 5


def test_low_fuel_refuels_before_work(make_state, scenario_data):
    tractor = dict(scenario_data["tractors"][0], fuel=12)
    state, events = make_state(tractors=[tractor])
    sim = Simulation(state, events, FCFSDispatcher())
    sim.run()
    assigned = [record for record in sim.log.records if record["type"] == "ASSIGNED"]
    assert [record["details"]["action"] for record in assigned] == ["REFUEL", "JOB"]
    assert assigned[1]["t"] == 15
    assert state.jobs["J1"].state == JobStatus.DONE


def test_includes_return_to_depot_and_reserve(make_state, scenario_data):
    # Outbound 1.25 + work 2 + return 1.25 + reserve 10 = 14.5.
    state, _ = make_state(tractors=[dict(scenario_data["tractors"][0], fuel=14)])
    assert estimate_job(state, state.tractors["T1"], state.jobs["J1"], 0) is None
    state.tractors["T1"].fuel = 14.5
    assert estimate_job(state, state.tractors["T1"], state.jobs["J1"], 0) is not None


def test_impossible_job_does_not_refuel_forever(make_state, scenario_data):
    tractor = dict(scenario_data["tractors"][0], fuel=20, burn_work=100)
    state, events = make_state(tractors=[tractor])
    sim = Simulation(state, events, FCFSDispatcher())
    sim.run()
    assert not any(record["type"] == "ASSIGNED" for record in sim.log.records)
    assert state.jobs["J1"].state == JobStatus.PENDING


def test_disconnected_field_is_not_assigned(make_state):
    state, _ = make_state()
    state.navigation.block("DEPOT", "F1")
    assert FCFSDispatcher().decide(state, 0) == []


def test_jobs_not_assigned_before_submission(make_state, scenario_data):
    state, events = make_state(jobs=[dict(scenario_data["jobs"][0], submitted_at=7)])
    sim = Simulation(state, events, FCFSDispatcher())
    sim.run()
    assignment = next(record for record in sim.log.records if record["type"] == "ASSIGNED")
    assert assignment["t"] == 7


def test_hand_calculated_metrics_stop_idle_at_last_completion(make_state, scenario_data):
    tractors = [scenario_data["tractors"][0],
                dict(scenario_data["tractors"][0], id="T2", implements=["sprayer"])]
    state, events = make_state(tractors=tractors)
    sim = Simulation(state, events, FCFSDispatcher())
    sim.run()
    assert calculate_metrics(state) == {
        "jobs_completed": 1, "jobs_on_time": 1, "deadline_misses": 0,
        "priority_weighted_on_time": 1, "acres_completed": 4,
        "travel_minutes": 2.5, "idle_minutes": 4.5, "makespan": 4.5,
        "safety_violations": 0, "duplicate_or_lost_work": 0,
    }


def test_unfinished_jobs_use_horizon_for_idle(make_state, scenario_data):
    tractor = dict(scenario_data["tractors"][0], fuel=10)
    state, events = make_state(tractors=[tractor], refuel_minutes=40)
    sim = Simulation(state, events, FCFSDispatcher())
    sim.run()
    metrics = calculate_metrics(state)
    assert metrics["jobs_completed"] == 0
    assert metrics["deadline_misses"] == 1
    assert metrics["makespan"] is None
    assert metrics["idle_minutes"] == 0  # Refuelling is not idle.


def test_breakdown_reassigns_only_remaining_work():
    state, events = load_scenario(Path(__file__).resolve().parents[1] / "scenarios/breakdown_only.json")
    sim = Simulation(state, events, FCFSDispatcher())
    sim.run()
    job = state.jobs["J1"]
    assert job.acres_done == 20
    assert sum(item["acres"] for item in job.history if item["tractor"] == "T1") == 5
    assert sum(item["acres"] for item in job.history if item["tractor"] == "T2") == 15
    assert state.completed == ["J1"]
    assert calculate_metrics(state)["duplicate_or_lost_work"] == 0


def test_metrics_detect_ledger_corruption(make_state):
    state, _ = make_state()
    state.jobs["J1"].acres_done = 1
    assert consistency_errors(state)


def test_default_fcfs_finishes_deterministically():
    root = Path(__file__).resolve().parents[1]
    results = []
    for _ in range(2):
        state, events = load_scenario(root / "scenarios/default.json")
        sim = Simulation(state, events, FCFSDispatcher())
        sim.run()
        metrics = calculate_metrics(state)
        assert state.now == 600
        assert metrics["jobs_completed"] > 0
        assert metrics["duplicate_or_lost_work"] == 0
        assert all(tractor.fuel >= tractor.reserve - 1e-8 for tractor in state.tractors.values())
        results.append((metrics, sim.log.records))
    assert results[0] == results[1]
