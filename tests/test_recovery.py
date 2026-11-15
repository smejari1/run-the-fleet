"""Repeated failures must preserve acres, ownership, and recoverable pending jobs."""

from copy import deepcopy

import pytest

from fleet.dispatchers.coordinator import CoordinatorDispatcher
from fleet.dispatchers.fcfs import FCFSDispatcher
from fleet.dispatchers.nearest import NearestDispatcher
from fleet.metrics import calculate_metrics
from fleet.models import Assignment, Event, JobStatus, TractorStatus
from fleet.scenario import scenario_from_dict
from fleet.sim import Simulation

DISPATCHERS = [FCFSDispatcher, NearestDispatcher, CoordinatorDispatcher]


def recovery_sim(dispatcher, events=None, only_one=False):
    state, timeline = scenario_from_dict({
        "horizon": 30,
        "fields": [{"id": "F1", "name": "Recovery Meadow", "x": 2, "y": 0, "acres": 20}],
        "edges": [{"a": "DEPOT", "b": "F1", "length": 2}],
        "tractors": [{"id": "T1", "name": "One", "implements": ["mower"], "speed": 1,
                      "work_rate": 1, "location": "F1"},
                     {"id": "T2", "name": "Two", "implements": ["sprayer"] if only_one else ["mower"],
                      "speed": 1, "work_rate": 1, "location": "F1"}],
        "jobs": [{"id": "J1", "type": "mow", "field": "F1", "requires_implement": "mower",
                  "priority": 3, "deadline": 30, "submitted_at": 0, "acres_total": 10}],
        "events": events or [],
    })
    return Simulation(state, timeline, dispatcher())


@pytest.mark.parametrize("dispatcher", DISPATCHERS)
def test_repeated_breakdowns_and_repairs_finish_each_acre_once(dispatcher):
    sim = recovery_sim(dispatcher, events=[
        {"type": "breakdown", "tractor": "T1", "at": 3},
        {"type": "breakdown", "tractor": "T2", "at": 5},
        {"type": "repair", "tractor": "T1", "at": 6},
        {"type": "breakdown", "tractor": "T1", "at": 7},
        {"type": "repair", "tractor": "T2", "at": 8},
    ])
    for _ in range(30):
        sim.step()
        sim.check_invariants()
    job = sim.state.jobs["J1"]
    assert job.state == JobStatus.DONE and job.finished_at == 12
    assert sum(item["acres"] for item in job.history if item["tractor"] == "T1") == 4
    assert sum(item["acres"] for item in job.history if item["tractor"] == "T2") == 6
    assert [r["acres_done"] for r in job.recovery_history] == [3, 5, 6]
    assert [r["to_tractor"] for r in job.recovery_history] == ["T2", "T1", "T2"]
    assert sim.state.completed == ["J1"]
    assert calculate_metrics(sim.state)["duplicate_or_lost_work"] == 0


@pytest.mark.parametrize("dispatcher", DISPATCHERS)
def test_only_compatible_tractor_failure_is_visible_and_recoverable(dispatcher):
    sim = recovery_sim(dispatcher, only_one=True, events=[
        {"type": "breakdown", "tractor": "T1", "at": 3},
        {"type": "repair", "tractor": "T1", "at": 6},
    ])
    for _ in range(5):
        sim.step()
    job = sim.state.jobs["J1"]
    assert job.state == JobStatus.PENDING and job.at_risk and job.acres_done == 3
    assert sim.state.tractors["T2"].assignment is None
    assert any(record["type"] == "RECOVERY_WAITING" for record in sim.log.records)
    sim.run()
    assert job.finished_at == 13 and not job.at_risk


def test_breakdown_reconsiders_all_pending_jobs_without_preempting_active_work():
    class RecordingCoordinator(CoordinatorDispatcher):
        def __init__(self):
            super().__init__()
            self.observed = []

        def decide(self, state, now):
            self.observed.append((now, {j.id for j in state.jobs.values() if j.state == JobStatus.PENDING}))
            return super().decide(state, now)

    sim = recovery_sim(CoordinatorDispatcher)
    sim.state.jobs["J2"] = deepcopy(sim.state.jobs["J1"])
    sim.state.jobs["J2"].id = "J2"
    sim.state.jobs["J3"] = deepcopy(sim.state.jobs["J1"])
    sim.state.jobs["J3"].id = "J3"
    from fleet.models import Field
    sim.state.fields["F2"] = Field("F2", "Parallel work", 0, 2, 20)
    sim.state.navigation.adjacency["F2"] = {}
    sim.state.navigation._add("DEPOT", "F2", 2)
    sim.state.jobs["J2"].field = "F2"
    sim.state.tractors["T2"].location = "F2"
    dispatcher = RecordingCoordinator()
    sim.dispatcher = dispatcher
    sim.schedule(Event(1, "breakdown", {"tractor": "T1"}))
    sim.step()
    assert dispatcher.observed[-1] == (1, {"J1", "J3"})
    assert sim.state.tractors["T2"].assignment.job_id == "J2"
    assert sim.state.jobs["J2"].acres_done == 1


@pytest.mark.parametrize("dispatcher", DISPATCHERS)
def test_midroad_failure_and_repair_preserve_position_and_unworked_acres(dispatcher):
    sim = recovery_sim(dispatcher)
    sim.state.tractors["T1"].location = "DEPOT"
    sim.state.tractors["T2"].state = TractorStatus.BROKEN
    sim.step()
    assert sim.position("T1") == (1, 0)
    sim.schedule(Event(1, "breakdown", {"tractor": "T1"}))
    sim.process_current_events()
    assert sim.position("T1") == (1, 0)
    assert sim.state.jobs["J1"].acres_done == 0
    sim.schedule(Event(1, "repair", {"tractor": "T1"}))
    sim.process_current_events()
    assert sim.position("T1") == (1, 0)
    sim.run()
    assert sim.state.jobs["J1"].finished_at == 12


def test_completed_job_is_never_requeued_by_a_later_breakdown():
    sim = recovery_sim(CoordinatorDispatcher, events=[{"type": "breakdown", "tractor": "T1", "at": 12}])
    sim.run()
    assert sim.state.jobs["J1"].state == JobStatus.DONE
    assert sim.state.completed == ["J1"]
    assert sim.state.jobs["J1"].recovery_history == []
    with pytest.raises(ValueError, match="not available"):
        sim.apply_assignment(Assignment("T2", "J1", ["F1"], 30, 31))


@pytest.mark.parametrize("corruption", ["duplicate", "lost", "overlap", "unfinished_done", "unknown_completion"])
def test_invariant_failures_are_detected_and_logged(corruption):
    sim = recovery_sim(CoordinatorDispatcher)
    sim.step()
    job = sim.state.jobs["J1"]
    if corruption == "duplicate":
        sim.state.tractors["T2"].assignment = deepcopy(sim.state.tractors["T1"].assignment)
        sim.state.tractors["T2"].state = TractorStatus.WORKING
    elif corruption == "lost":
        job.acres_done = 2
    elif corruption == "overlap":
        job.history.append(dict(job.history[0]))
        job.acres_done = 2
    elif corruption == "unfinished_done":
        sim.state.tractors["T1"].assignment = None
        sim.state.tractors["T1"].state = TractorStatus.IDLE
        job.state = JobStatus.DONE
        job.finished_at = 1
        sim.state.completed = ["J1"]
    else:
        sim.state.completed = ["missing"]
    with pytest.raises(AssertionError):
        sim.check_invariants()
    assert sim.log.records[-1]["type"] == "INVARIANT_FAILED"
