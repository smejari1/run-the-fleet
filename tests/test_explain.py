"""Phase 5: explanations come from real decisions and mention the facts that matter."""

import json
from pathlib import Path

import pytest

from fleet.dispatchers.coordinator import CoordinatorDispatcher
from fleet.explain import explain_job, plan_feed
from fleet.scenario import scenario_from_dict
from fleet.sim import Simulation
from server import create_app

ROOT = Path(__file__).resolve().parents[1]


def run_default(events, until=None):
    data = json.loads((ROOT / "scenarios/default.json").read_text(encoding="utf-8-sig"))
    data["events"] = events
    state, scheduled = scenario_from_dict(data)
    sim = Simulation(state, scheduled, CoordinatorDispatcher())
    sim.start()
    while state.now < (until or state.horizon):
        sim.step()
    return state, sim


def test_assignment_explanation_names_tractor_job_field_and_reason():
    state, sim = run_default([], until=5)
    first = next(r for r in sim.log.records if r["type"] == "ASSIGNED" and r["details"]["action"] == "JOB")
    result = explain_job(state, sim.log.records, first["job"])
    text = " ".join(result["lines"])
    job = state.jobs[first["job"]]
    assert first["tractor"] in text and job.id in text and job.field in text
    assert "min" in text and "deadline" in text


def test_breakdown_explanation_mentions_preserved_progress_and_replan():
    state, sim = run_default([{"type": "breakdown", "at": 120, "tractor": "agxt2"}], until=140)
    feed = plan_feed(state, sim.log.records, limit=100)
    breakdown = [item for item in feed if item["type"] == "BREAKDOWN"]
    assert breakdown and "%" in breakdown[0]["text"] and breakdown[0]["highlight"]
    replans = [item for item in feed if item["type"] == "REPLAN"]
    assert replans and all("agxt2 broke down" in item["text"] for item in replans)


def test_late_job_reports_the_plan_without_claiming_an_impossibility_proof():
    state, sim = run_default([])
    result = explain_job(state, sim.log.records, "J12")
    assert "late" in result["status"]
    assert any("this plan finishes the job late" in line for line in result["lines"])
    assert not any("no tractor can make it on time" in line for line in result["lines"])


def test_why_and_feed_endpoints():
    client = create_app().test_client()
    client.post("/api/control", json={"action": "reset", "dispatcher": "coordinator"})
    for _ in range(3):
        client.post("/api/control", json={"action": "step"})
    feed = client.get("/api/explanations").get_json()
    assert feed["feed"] and all("text" in item for item in feed["feed"])
    why = client.get("/api/why/J01").get_json()
    assert why["job"] == "J01" and why["lines"]
    assert client.get("/api/why/NOPE").status_code == 404


def test_explanations_are_available_immediately_while_paused():
    client = create_app().test_client()
    response = client.get("/api/why/J01")
    assert response.status_code == 200
    assert response.json["lines"] and response.json["generation"] >= 1
    assert response.headers["Cache-Control"] == "no-store"
    assert client.get("/api/explanations").json["feed"]
    assert not client.get("/api/state").json["running"]


def test_scheduled_job_has_a_specific_explanation_before_submission():
    client = create_app().test_client()
    result = client.get("/api/why/J12").json
    assert "Scheduled" in result["status"]
    assert "00:30" in " ".join(result["lines"])


def test_assignment_fuel_is_a_snapshot_not_the_tractors_later_fuel():
    state, sim = run_default([], until=5)
    record = next(r for r in sim.log.records if r["type"] == "ASSIGNED" and r.get("job"))
    captured = record["details"]["explanation"]["fuel_at_assignment"]
    assert state.tractors[record["tractor"]].fuel < captured
    text = " ".join(explain_job(state, sim.log.records, record["job"])["lines"])
    assert f"At dispatch it had {captured:.1f}% fuel" in text


def test_reset_clears_the_explanation_feed_and_changes_generation():
    client = create_app().test_client()
    initial = client.get("/api/why/J03").json
    client.post("/api/event", json={"type": "breakdown", "tractor": "agxt1"})
    assert any(item["type"] == "BREAKDOWN" for item in client.get("/api/explanations").json["feed"])
    client.post("/api/control", json={"action": "reset"})
    fresh = client.get("/api/why/J03").json
    assert fresh["generation"] != initial["generation"]
    assert not any(item["type"] == "BREAKDOWN" for item in client.get("/api/explanations").json["feed"])


@pytest.mark.parametrize("dispatcher,phrase", [("fcfs", "earliest-submitted"), ("nearest", "least open-road travel")])
def test_why_explains_the_selected_baseline_policy(dispatcher, phrase):
    client = create_app().test_client()
    client.post("/api/control", json={"action": "reset", "dispatcher": dispatcher})
    assert phrase in " ".join(client.get("/api/why/J01").json["lines"])


def test_road_update_describes_the_actual_path_without_inventing_a_detour():
    from fleet.explain import record_text
    state, sim = run_default([], until=1)
    text = record_text(state, {"t": 1, "type": "REROUTED", "tractor": "agxt1", "job": "J03",
                               "details": {"route": ["DEPOT", "F4", "F1"], "est_finish": 65}})
    assert "DEPOT → F4 → F1" in text and "01:05" in text
    assert "detour" not in text
