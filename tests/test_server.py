"""Verify the map shows simulation state and controls preserve deterministic runs."""

import pytest

from fleet.dispatchers.fcfs import FCFSDispatcher
from fleet.dispatchers.coordinator import CoordinatorDispatcher
from fleet.metrics import calculate_metrics
from fleet.scenario import load_scenario
from fleet.sim import Simulation
from server import ROOT, create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_map_loads_with_three_tractors_and_eight_fields(client):
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200
    data = client.get("/api/state").json
    assert len(data["tractors"]) == 3
    assert len(data["fields"]) == 8
    assert {item["id"] for item in data["tractors"]} == {"agxt1", "agxt2", "agxt3"}
    assert data["time"] == 0 and not data["running"]
    assert data["safety"]["enabled"] and data["safety"]["rejections"] == 0


def test_demo_has_alternate_routes_after_any_one_road_closes():
    state, _ = load_scenario(ROOT / "scenarios/default.json")
    navigation = state.navigation
    for a, b in navigation.edges:
        navigation.block(a, b)
        path, _ = navigation.route(a, b)
        assert len(path) >= 3, f"Road {a}-{b} has no alternative route"
        navigation.unblock(a, b)


def test_step_updates_real_map_positions_and_reset_restores_them(client):
    before = client.get("/api/state").json
    after = client.post("/api/control", json={"action": "step"}).json
    assert after["time"] == 1
    assert (after["tractors"][0]["x"], after["tractors"][0]["y"]) != (before["tractors"][0]["x"], before["tractors"][0]["y"])
    assert after["tractors"][0]["fuel"] < before["tractors"][0]["fuel"]
    reset = client.post("/api/control", json={"action": "reset"}).json
    reset["generation"] = before["generation"]
    assert reset == before


def test_play_pause_and_speed(client):
    assert client.post("/api/control", json={"action": "start", "speed": 10}).json["running"]
    assert client.get("/api/state").json["speed"] == 10
    assert not client.post("/api/control", json={"action": "pause"}).json["running"]


@pytest.mark.parametrize("payload", [None, [], {"action": "bad"}, {"action": "speed", "speed": 0},
                                     {"action": "start", "speed": True}, {"action": "start", "speed": "fast"}])
def test_rejects_bad_controls_without_changing_state(client, payload):
    before = client.get("/api/state").json
    assert client.post("/api/control", json=payload).status_code == 400
    assert client.get("/api/state").json == before


def test_map_run_matches_cli_without_automatic_breakdowns(client):
    state, events = load_scenario(ROOT / "scenarios/default.json")
    Simulation(state, events, CoordinatorDispatcher()).run()
    for _ in range(600):
        response = client.post("/api/control", json={"action": "step"})
        assert response.status_code == 200
    data = response.json
    assert data["metrics"] == calculate_metrics(state)
    assert all(t["state"] != "BROKEN" for t in data["tractors"])
    assert not client.post("/api/control", json={"action": "start"}).json["running"]
    assert client.post("/api/control", json={"action": "step"}).json["time"] == 600


def test_queue_job_while_paused(client):
    response = client.post("/api/jobs", json={"type": "plowing", "field": "F4", "acres_total": 5,
                                              "priority": 4, "deadline_after": 120})
    assert response.status_code == 201
    data = response.json
    assert data["time"] == 0 and not data["running"]
    assert data["added_job_ids"] == ["J13"]
    job = next(job for job in data["jobs"] if job["id"] == "J13")
    assert job["priority"] == 4 and job["deadline"] == 120
    assert data["operator_events"] == 1


def test_bad_job_batch_is_atomic(client):
    before = client.get("/api/state").json
    valid = {"type": "spraying", "field": "F1", "acres_total": 3}
    response = client.post("/api/jobs", json=[valid, dict(valid, field="missing")])
    assert response.status_code == 400
    assert client.get("/api/state").json == before


@pytest.mark.parametrize("change", [{"type": "mowing"}, {"acres_total": -2}, {"priority": 8},
                                     {"id": "J01"}, {"deadline_after": 0}, {"state": "DONE"}])
def test_bad_jobs_are_rejected(client, change):
    values = {"type": "spraying", "field": "F1", "acres_total": 3}
    values.update(change)
    assert client.post("/api/jobs", json=values).status_code == 400


def test_injected_breakdown_is_immediate_and_keeps_progress(client):
    for _ in range(40):
        client.post("/api/control", json={"action": "step"})
    before = client.get("/api/state").json
    tractor = next(t for t in before["tractors"] if t["state"] == "WORKING")
    original = next(j for j in before["jobs"] if j["id"] == tractor["job"])
    data = client.post("/api/event", json={"type": "breakdown", "tractor": tractor["id"]}).json
    assert data["time"] == before["time"]
    broken = next(t for t in data["tractors"] if t["id"] == tractor["id"])
    assert broken["state"] == "BROKEN" and broken["job"] is None
    job = next(j for j in data["jobs"] if j["id"] == original["id"])
    assert job["acres_done"] == original["acres_done"] > 0
    assert data["metrics"]["duplicate_or_lost_work"] == 0
    assert client.post("/api/event", json={"type": "breakdown", "tractor": tractor["id"]}).status_code == 400
    repaired = client.post("/api/event", json={"type": "repair", "tractor": tractor["id"]}).json
    assert next(t for t in repaired["tractors"] if t["id"] == tractor["id"])["state"] != "BROKEN"


def test_replay_keeps_operator_order_and_matches_live_run(client):
    client.post("/api/jobs", json={"type": "plowing", "field": "F4", "acres_total": 2})
    for _ in range(3):
        client.post("/api/control", json={"action": "step"})
    client.post("/api/event", json={"type": "breakdown", "tractor": "agxt1"})
    client.post("/api/event", json={"type": "repair", "tractor": "agxt1"})
    replay = client.get("/api/replay").json
    result = client.post("/api/compare").json
    assert len(replay["events"]) == result["input"]["events"] == 3
    for _ in range(597):
        data = client.post("/api/control", json={"action": "step"}).json
    assert result["coordinator"] == data["metrics"]


def test_dispatcher_changes_only_on_reset(client):
    assert client.post("/api/control", json={"action": "start", "dispatcher": "fcfs"}).status_code == 400
    data = client.post("/api/control", json={"action": "reset", "dispatcher": "fcfs"}).json
    assert data["dispatcher"] == "fcfs" and data["time"] == 0
    data = client.post("/api/control", json={"action": "reset", "dispatcher": "nearest"}).json
    assert data["dispatcher"] == "nearest"


def test_manual_road_block_and_reopen_are_immediate_and_validated(client):
    before = client.get("/api/state").json
    road = before["edges"][0]
    request = {"type": "block_route", "a": road["a"], "b": road["b"]}
    response = client.post("/api/event", json=request)
    assert response.status_code == 200
    data = response.json
    assert data["time"] == before["time"] and data["operator_events"] == 1
    assert next(e for e in data["edges"] if e["a"] == road["a"] and e["b"] == road["b"])["blocked"]
    assert client.post("/api/event", json=request).status_code == 400
    request["type"] = "unblock_route"
    data = client.post("/api/event", json=request).json
    assert not any(e["blocked"] for e in data["edges"])
    snapshot = client.get("/api/state").json
    assert client.post("/api/event", json={"type": "block_route", "a": "DEPOT", "b": "missing"}).status_code == 400
    assert client.get("/api/state").json == snapshot


def test_all_three_dispatchers_replay_road_events(client):
    road = client.get("/api/state").json["edges"][0]
    values = {"a": road["a"], "b": road["b"]}
    client.post("/api/event", json={"type": "block_route", **values})
    for _ in range(10):
        client.post("/api/control", json={"action": "step"})
    client.post("/api/event", json={"type": "unblock_route", **values})
    result = client.post("/api/compare").json
    assert result["input"]["events"] == 2
    assert set(result["baseline_changes"]) == {"fcfs", "nearest"}
    for key in ("fcfs", "nearest", "coordinator"):
        assert result[key]["duplicate_or_lost_work"] == 0
    for _ in range(590):
        live = client.post("/api/control", json={"action": "step"}).json
    assert result["coordinator"] == live["metrics"]


def test_pending_priority_changes_immediately_without_interrupting_active_jobs(client):
    before = client.get("/api/state").json
    pending = next(job for job in before["jobs"] if job["state"] == "PENDING" and job["submitted_at"] == 0)
    data = client.post("/api/event", json={"type": "change_priority", "job": pending["id"], "priority": 5}).json
    assert next(job for job in data["jobs"] if job["id"] == pending["id"])["priority"] == 5
    for tractor in before["tractors"]:
        if tractor["job"]:
            assert next(t for t in data["tractors"] if t["id"] == tractor["id"])["job"] == tractor["job"]
    assert data["time"] == 0 and data["operator_events"] == 1
    active = next(job for job in data["jobs"] if job["tractor"])
    assert client.post("/api/event", json={"type": "change_priority", "job": active["id"], "priority": 1}).status_code == 400


def test_recovery_is_visible_in_api_queue(client):
    for _ in range(100):
        client.post("/api/control", json={"action": "step"})
    before = client.get("/api/state").json
    working = next(t for t in before["tractors"] if t["state"] == "WORKING")
    data = client.post("/api/event", json={"type": "breakdown", "tractor": working["id"]}).json
    job = next(j for j in data["jobs"] if j["id"] == working["job"])
    assert job["recovery_count"] == 1
    assert job["recovery"]["from_tractor"] == working["id"]
    assert job["recovery"]["acres_done"] == job["acres_done"] > 0


def test_live_integrity_failure_pauses_and_returns_actionable_error(client):
    session = client.application.extensions["map_session"]
    session.simulation.state.completed.append("missing")
    response = client.post("/api/control", json={"action": "step"})
    assert response.status_code == 503
    assert not session.running and session.error
    assert any(record["type"] == "INVARIANT_FAILED" for record in session.simulation.log.records)
    assert client.post("/api/control", json={"action": "reset"}).status_code == 200
