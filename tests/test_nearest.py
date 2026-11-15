"""Nearest baseline ranks feasible road travel, not submission order."""

from fleet.dispatchers.fcfs import FCFSDispatcher
from fleet.dispatchers.nearest import NearestDispatcher
from fleet.models import TractorStatus


def test_nearest_skips_older_far_job(make_state, scenario_data):
    fields = [scenario_data["fields"][0], {"id": "F2", "name": "Far", "x": 20, "y": 0, "acres": 10}]
    jobs = [dict(scenario_data["jobs"][0], id="J1", field="F2", deadline=100),
            dict(scenario_data["jobs"][0], id="J2", deadline=100)]
    state, _ = make_state(fields=fields, jobs=jobs, edges=None)
    assert FCFSDispatcher().decide(state, 0)[0].job_id == "J1"
    assert NearestDispatcher().decide(state, 0)[0].job_id == "J2"


def test_nearest_keeps_compatibility_fuel_and_unique_ownership(make_state, scenario_data):
    tractors = [dict(scenario_data["tractors"][0], id="T0", implements=["sprayer"]),
                scenario_data["tractors"][0], dict(scenario_data["tractors"][0], id="T2")]
    state, _ = make_state(tractors=tractors)
    choices = NearestDispatcher().decide(state, 0)
    assert [(a.tractor_id, a.job_id) for a in choices] == [("T1", "J1")]
    state.tractors["T1"].state = TractorStatus.BROKEN
    state.tractors["T2"].fuel = 12
    assert NearestDispatcher().decide(state, 0)[0].action == "REFUEL"
