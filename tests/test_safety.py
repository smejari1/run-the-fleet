"""Independent validation, bounded retries, field protection and seeded stress."""

from copy import deepcopy
from dataclasses import replace

import pytest

from fleet.dispatchers.base import NoopDispatcher
from fleet.dispatchers.coordinator import CoordinatorDispatcher
from fleet.dispatchers.fcfs import FCFSDispatcher
from fleet.dispatchers.nearest import NearestDispatcher
from fleet.metrics import calculate_metrics
from fleet.models import Assignment, JobStatus, TractorStatus
from fleet.safety import check, UnsafeAssignmentError
from fleet.scenario import scenario_from_dict
from fleet.sim import Simulation
from run_safety_stress import safety_scenario


@pytest.mark.parametrize("issue,reason", [
    ("tractor", "does not exist"), ("broken", "broken"), ("busy", "already assigned"),
    ("job", "Job does not exist"), ("done", "already done"), ("held", "held by"),
    ("implement", "Missing implement"), ("capability", "Missing capabilities"),
    ("empty_route", "Route is empty"), ("unknown_node", "unknown node"),
    ("wrong_start", "does not start"), ("wrong_end", "wrong destination"),
    ("blocked", "blocked road"), ("fuel", "Insufficient fuel"),
    ("deadline", "deadline"), ("estimates", "Estimated times"),
    ("nan", "finite numbers"), ("progress", "existing travel"),
    ("nan_fuel", "fuel must be finite"), ("nonexistent_road", "nonexistent road"),
])
def test_bad_proposals_rejected_for_the_actual_reason(make_state, issue, reason):
    state, _ = make_state()
    assignment = Assignment("T1", "J1", ["DEPOT", "F1"], 2.5, 4.5)
    tractor, job = state.tractors["T1"], state.jobs["J1"]
    if issue == "tractor": assignment.tractor_id = "missing"
    elif issue == "broken": tractor.state = TractorStatus.BROKEN
    elif issue == "busy": tractor.assignment = deepcopy(assignment)
    elif issue == "job": assignment.job_id = "missing"
    elif issue == "done": job.state = JobStatus.DONE
    elif issue == "held":
        state.tractors["T2"] = replace(tractor, id="T2", assignment=deepcopy(assignment))
    elif issue == "implement": tractor.implements = ["sprayer"]
    elif issue == "capability": job.requires_capabilities = ["rtk"]
    elif issue == "empty_route": assignment.planned_route = []
    elif issue == "unknown_node": assignment.planned_route = ["DEPOT", "missing", "F1"]
    elif issue == "wrong_start": assignment.planned_route = ["F1"]
    elif issue == "wrong_end": assignment.planned_route = ["DEPOT"]
    elif issue == "blocked": state.navigation.block("DEPOT", "F1")
    elif issue == "fuel": tractor.fuel = 14
    elif issue == "deadline": job.deadline = 3
    elif issue == "estimates": assignment.est_finish = 1
    elif issue == "nan": assignment.est_start = float("nan")
    elif issue == "progress": assignment.edge_progress = 1
    elif issue == "nan_fuel": tractor.fuel = float("nan")
    elif issue == "nonexistent_road": assignment.planned_route = ["DEPOT", "DEPOT", "F1"]
    result = check(assignment, state)
    assert not result.ok and any(reason in item for item in result.violations)


def test_return_route_and_exact_reserve_are_checked(make_state):
    state, _ = make_state()
    assignment = Assignment("T1", "J1", ["DEPOT", "F1"], 2.5, 4.5)
    state.tractors["T1"].fuel = 14.5
    assert check(assignment, state).ok
    state.tractors["T1"].location = "F1"
    state.navigation.block("DEPOT", "F1")
    result = check(Assignment("T1", "J1", ["F1"], 0, 2), state)
    assert not result.ok and "No open return route to the depot." in result.violations


def test_late_and_horizon_exceptions_are_warnings_not_rejections(make_state):
    state, _ = make_state()
    state.jobs["J1"].deadline = 3
    state.jobs["J1"].at_risk = True
    state.horizon = 4
    result = check(Assignment("T1", "J1", ["DEPOT", "F1"], 2.5, 4.5), state)
    assert result.ok and len(result.warnings) == 2


def test_fuel_uses_the_proposed_route_not_just_the_shortest_route(make_state):
    state, _ = make_state()
    state.tractors["T1"].fuel = 16
    proposal = Assignment("T1", "J1", ["DEPOT", "F1", "DEPOT", "F1"], 7.5, 9.5)
    result = check(proposal, state)
    assert not result.ok and any("Insufficient fuel" in reason for reason in result.violations)


def test_refuel_action_also_needs_a_valid_destination_and_reserve(make_state):
    state, _ = make_state()
    tractor = state.tractors["T1"]
    tractor.location = "F1"
    tractor.fuel = 10
    result = check(Assignment("T1", None, ["F1", "DEPOT"], 2.5, 17.5, action="REFUEL"), state)
    assert not result.ok and any("Insufficient fuel" in reason for reason in result.violations)
    tractor.fuel = 100
    result = check(Assignment("T1", None, ["F1"], 0, 15, action="REFUEL"), state)
    assert not result.ok and any("wrong destination" in reason for reason in result.violations)


def test_overlapping_field_work_rejected_but_adjacent_windows_allowed(make_state):
    state, _ = make_state()
    other_job = deepcopy(state.jobs["J1"])
    other_job.id = "J2"
    other_job.state = JobStatus.IN_PROGRESS
    state.jobs["J2"] = other_job
    state.tractors["T2"] = replace(state.tractors["T1"], id="T2", location="F1", state=TractorStatus.WORKING,
                                   assignment=Assignment("T2", "J2", ["F1"], 0, 10))
    proposed = Assignment("T1", "J1", ["DEPOT", "F1"], 2.5, 4.5)
    assert any("overlaps" in reason for reason in check(proposed, state).violations)
    state.tractors["T2"].assignment.est_finish = 2.5
    assert check(proposed, state).ok


def test_invalid_assignment_cannot_mutate_the_simulation(make_state):
    state, events = make_state()
    sim = Simulation(state, events, NoopDispatcher())
    sim.start()
    before = deepcopy((state.tractors, state.jobs))
    state.tractors["T1"].implements = ["sprayer"]
    with pytest.raises(UnsafeAssignmentError):
        sim.apply_assignment(Assignment("T1", "J1", ["DEPOT", "F1"], 2.5, 4.5))
    assert state.tractors["T1"].assignment is None
    assert state.jobs == before[1] and state.safety_violations == 1


def test_rejected_pair_is_excluded_and_dispatcher_is_reasked(make_state, scenario_data):
    tractors = [dict(scenario_data["tractors"][0], implements=["sprayer"]),
                dict(scenario_data["tractors"][0], id="T2")]
    jobs = [scenario_data["jobs"][0], dict(scenario_data["jobs"][0], id="J2", requires_implement="sprayer")]
    state, events = make_state(tractors=tractors, jobs=jobs)
    class RetryDispatcher:
        name = "retry-test"
        calls = 0
        def decide(self, state, now):
            self.calls += 1
            job_id = "J2" if ("T1", "J1") in state.excluded_pairs else "J1"
            return [Assignment("T1", job_id, ["DEPOT", "F1"], 2.5, 4.5)]
    dispatcher = RetryDispatcher()
    Simulation(state, events, dispatcher).start()
    assert dispatcher.calls == 2
    assert state.tractors["T1"].assignment.job_id == "J2"
    assert state.safety_violations == 1 and not state.excluded_pairs


def test_stubborn_invalid_dispatcher_cannot_loop_forever(make_state):
    state, events = make_state()
    class Stubborn:
        name = "stubborn"
        calls = 0
        def decide(self, state, now):
            self.calls += 1
            return [Assignment("missing", "J1", ["DEPOT", "F1"], 2.5, 4.5)]
    dispatcher = Stubborn()
    Simulation(state, events, dispatcher).start()
    assert dispatcher.calls == 2 and state.safety_violations == 1


@pytest.mark.parametrize("dispatcher_type", [FCFSDispatcher, NearestDispatcher, CoordinatorDispatcher])
def test_same_field_jobs_are_serialized_without_safety_rejections(make_state, scenario_data, dispatcher_type):
    tractors = [scenario_data["tractors"][0], dict(scenario_data["tractors"][0], id="T2")]
    jobs = [scenario_data["jobs"][0], dict(scenario_data["jobs"][0], id="J2")]
    state, events = make_state(tractors=tractors, jobs=jobs)
    Simulation(state, events, dispatcher_type()).run()
    assert len(state.completed) == 2 and state.safety_violations == 0
    assert calculate_metrics(state)["duplicate_or_lost_work"] == 0


@pytest.mark.parametrize("seed", range(20))
def test_twenty_seeded_safety_scenarios(seed):
    for dispatcher_type in (FCFSDispatcher, NearestDispatcher, CoordinatorDispatcher):
        state, events = scenario_from_dict(safety_scenario(seed))
        sim = Simulation(state, events, dispatcher_type())
        sim.run()
        assert state.safety_violations == 0, (seed, dispatcher_type.name)
        assert calculate_metrics(state)["duplicate_or_lost_work"] == 0
