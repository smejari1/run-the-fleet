"""Road closures must detour, preserve position, and respect remaining fuel."""

import pytest

from fleet.dispatchers.coordinator import CoordinatorDispatcher
from fleet.dispatchers.fcfs import FCFSDispatcher
from fleet.dispatchers.nearest import NearestDispatcher
from fleet.models import Event, JobStatus, TractorStatus
from fleet.scenario import scenario_from_dict
from fleet.sim import Simulation

DISPATCHERS = [FCFSDispatcher, NearestDispatcher, CoordinatorDispatcher]


def road_sim(dispatcher, fuel=100, acres=2, events=None):
    state, timeline = scenario_from_dict({
        "horizon": 60,
        "depot": {"x": 0, "y": 0},
        "fields": [{"id": "F1", "name": "Junction", "x": 2, "y": 0, "acres": 10},
                   {"id": "F2", "name": "Destination", "x": 4, "y": 0, "acres": 10},
                   {"id": "F3", "name": "Alternate", "x": 0, "y": 3, "acres": 10}],
        "edges": [{"a": "DEPOT", "b": "F1", "length": 2},
                  {"a": "F1", "b": "F2", "length": 2},
                  {"a": "DEPOT", "b": "F3", "length": 3},
                  {"a": "F3", "b": "F2", "length": 5},
                  {"a": "F1", "b": "F3", "length": 4}],
        "tractors": [{"id": "T1", "name": "Test", "implements": ["plow"], "speed": 1,
                      "work_rate": 1, "burn_travel": 1, "burn_work": 1, "fuel": fuel}],
        "jobs": [{"id": "J1", "type": "plowing", "field": "F2", "requires_implement": "plow",
                  "acres_total": acres, "submitted_at": 0, "deadline": 60, "priority": 3}],
        "events": events or [],
    })
    sim = Simulation(state, timeline, dispatcher())
    sim.start()
    return sim


def inject(sim, kind, a, b):
    sim.schedule(Event(sim.state.now, kind, {"a": a, "b": b, "operator_batch": sim.state.now + 1}))
    sim.process_current_events()


@pytest.mark.parametrize("dispatcher", DISPATCHERS)
def test_blocked_shortest_road_uses_open_alternative_immediately(dispatcher):
    sim = road_sim(dispatcher)
    inject(sim, "block_route", "DEPOT", "F1")
    tractor = sim.state.tractors["T1"]
    assert tractor.assignment.planned_route == ["DEPOT", "F3", "F2"]
    assert tractor.assignment.est_finish == 10
    sim.run()
    assert sim.state.jobs["J1"].finished_at == 10
    assert tractor.travel_minutes == 8


@pytest.mark.parametrize("dispatcher", DISPATCHERS)
def test_future_blockage_updates_path_and_eta_without_teleport(dispatcher):
    sim = road_sim(dispatcher)
    sim.step()
    position = sim.position("T1")
    inject(sim, "block_route", "F1", "F2")
    tractor = sim.state.tractors["T1"]
    assert sim.position("T1") == position
    assert tractor.assignment.planned_route == ["DEPOT", "F1", "F3", "F2"]
    assert tractor.assignment.est_finish == 13
    sim.run()
    assert sim.state.jobs["J1"].finished_at == 13
    assert tractor.fuel == 87
    sim.check_invariants()


def test_already_entered_road_can_be_exited():
    sim = road_sim(CoordinatorDispatcher)
    sim.step()
    inject(sim, "block_route", "DEPOT", "F1")
    assert sim.position("T1") == (1, 0)
    sim.run()
    assert sim.state.jobs["J1"].finished_at == 6


@pytest.mark.parametrize("dispatcher", DISPATCHERS)
def test_fuel_infeasible_detour_refuels_instead_of_running_empty(dispatcher):
    sim = road_sim(dispatcher, fuel=23)
    sim.step()
    inject(sim, "block_route", "F1", "F2")
    assert sim.state.tractors["T1"].assignment.route_pending
    sim.step()
    assert sim.state.jobs["J1"].state == JobStatus.PENDING
    assert sim.state.tractors["T1"].assignment.action == "REFUEL"
    sim.run()
    assert sim.state.jobs["J1"].state == JobStatus.DONE
    assert sim.state.tractors["T1"].fuel >= 10
    assert any(r["type"] == "ROUTE_UNAVAILABLE" for r in sim.log.records)


@pytest.mark.parametrize("dispatcher", DISPATCHERS)
def test_disconnected_destination_waits_then_resumes_on_reopening(dispatcher):
    sim = road_sim(dispatcher)
    inject(sim, "block_route", "DEPOT", "F1")
    inject(sim, "block_route", "DEPOT", "F3")
    assert sim.state.jobs["J1"].state == JobStatus.PENDING
    assert sim.state.tractors["T1"].state == TractorStatus.IDLE
    for _ in range(5):
        sim.step()
    assert sim.state.tractors["T1"].fuel == 100
    inject(sim, "unblock_route", "DEPOT", "F3")
    sim.run()
    assert sim.state.jobs["J1"].finished_at == 15


def test_work_progress_is_preserved_if_new_return_route_needs_refuel():
    sim = road_sim(CoordinatorDispatcher, fuel=28, acres=10)
    for _ in range(5):
        sim.step()
    assert sim.state.jobs["J1"].acres_done == 1
    inject(sim, "block_route", "F1", "F2")
    assert sim.state.jobs["J1"].acres_done == 1
    assert sim.state.jobs["J1"].state == JobStatus.PENDING
    sim.run()
    assert sim.state.jobs["J1"].acres_done == 10
    assert sim.state.tractors["T1"].fuel >= 10
    sim.check_invariants()
