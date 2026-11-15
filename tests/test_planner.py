"""Rolling-horizon coordinator: sequencing, balancing, and replanning."""

from fleet.dispatchers.coordinator import CoordinatorDispatcher
from fleet.sim import Simulation


def test_urgent_late_job_goes_first_when_others_stay_on_time(make_state, scenario_data):
    # J2 cannot meet its deadline, but doing it first keeps J1 on time too.
    jobs = [dict(scenario_data["jobs"][0], deadline=25),
            dict(scenario_data["jobs"][0], id="J2", priority=5, deadline=3)]
    state, _ = make_state(jobs=jobs)
    dispatcher = CoordinatorDispatcher()
    assert dispatcher.decide(state, 0)[0].job_id == "J2"
    assert dispatcher.last_plan["T1"][1]["job"] == "J1"


def test_work_is_spread_across_idle_tractors(make_state, scenario_data):
    tractors = [scenario_data["tractors"][0], dict(scenario_data["tractors"][0], id="T2")]
    fields = [scenario_data["fields"][0], dict(scenario_data["fields"][0], id="F2", y=5)]
    jobs = [scenario_data["jobs"][0], dict(scenario_data["jobs"][0], id="J2", field="F2")]
    state, _ = make_state(tractors=tractors, jobs=jobs, fields=fields, edges=None)
    assignments = CoordinatorDispatcher().decide(state, 0)
    assert sorted(a.tractor_id for a in assignments) == ["T1", "T2"]


def test_breakdown_replans_remaining_work(make_state, scenario_data):
    tractors = [scenario_data["tractors"][0], dict(scenario_data["tractors"][0], id="T2")]
    jobs = [dict(scenario_data["jobs"][0], acres_total=20, deadline=29)]
    state, events = make_state(tractors=tractors, jobs=jobs, horizon=40)
    from fleet.models import Event
    sim = Simulation(state, events + [Event(5, "breakdown", {"tractor": "T1"})], CoordinatorDispatcher())
    sim.run()
    job = state.jobs["J1"]
    assert job.state == "DONE"
    assert {entry["tractor"] for entry in job.history} == {"T1", "T2"}
