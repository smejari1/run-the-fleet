r"""Stress-test every dispatcher on many shifts, not just the demo.

Suites (all deterministic):
  DEFAULT+EVENTS  the demo farm with breakdowns, repairs, road blocks, a priority change (19 runs)
  RANDOM-TUNE     40 random shifts used while tuning the coordinator's weights
  RANDOM-TEST     40 different random shifts never used for tuning (honest check)

Usage (PowerShell):  .\.venv\Scripts\python.exe benchmark.py
Takes about 3-4 minutes. Prints totals over each suite.
"""
import json
import random
import sys
from copy import deepcopy
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT))

from fleet.dispatchers.fcfs import FCFSDispatcher  # noqa: E402
from fleet.dispatchers.nearest import NearestDispatcher  # noqa: E402
from fleet.metrics import calculate_metrics  # noqa: E402
from fleet.operations import OPERATIONS  # noqa: E402
from fleet.scenario import scenario_from_dict  # noqa: E402
from fleet.sim import Simulation  # noqa: E402

DEFAULT = json.loads((PROJECT / "scenarios/default.json").read_text(encoding="utf-8-sig"))
TRACTORS = ["agxt1", "agxt2", "agxt3"]
ROADS = [(e["a"], e["b"]) for e in DEFAULT["edges"]]


def with_events(base, events, name):
    data = deepcopy(base)
    data["events"] = events
    data["_name"] = name
    return data


def default_suite():
    suite = [with_events(DEFAULT, [], "default")]
    for t in TRACTORS:
        for at in (60, 150, 250):
            suite.append(with_events(DEFAULT, [{"type": "breakdown", "at": at, "tractor": t}],
                                     f"default+break_{t}@{at}"))
    for t in TRACTORS:
        suite.append(with_events(DEFAULT, [{"type": "breakdown", "at": 100, "tractor": t},
                                           {"type": "repair", "at": 220, "tractor": t}],
                                 f"default+break_{t}@100+repair@220"))
    for a, b in [("DEPOT", "F1"), ("F2", "F4"), ("F4", "F7"), ("F5", "F8"), ("F3", "F5")]:
        suite.append(with_events(DEFAULT, [{"type": "block_route", "at": 20, "a": a, "b": b}],
                                 f"default+block_{a}-{b}@20"))
    suite.append(with_events(DEFAULT, [{"type": "block_route", "at": 40, "a": "F4", "b": "F7"},
                                       {"type": "breakdown", "at": 120, "tractor": "agxt2"},
                                       {"type": "change_priority", "at": 150, "job": "J11", "priority": 5}],
                             "default+combo"))
    return suite


def random_shift(seed):
    rng = random.Random(seed)
    data = deepcopy(DEFAULT)
    data["_name"] = f"random_{seed}"
    data["seed"] = seed
    data["tractors"][2]["fuel"] = rng.choice([22, 40, 70, 100])
    fields = [f["id"] for f in DEFAULT["fields"]]
    jobs = []
    n = rng.randint(10, 15)
    for i in range(n):
        op = rng.choice(list(OPERATIONS))
        submitted = 0 if rng.random() < 0.65 else rng.randint(10, 250)
        acres = rng.randint(6, 24)
        job = {"id": f"R{i:02d}", "type": op, "field": rng.choice(fields),
               "requires_implement": OPERATIONS[op], "priority": rng.randint(1, 5),
               "submitted_at": submitted,
               "deadline": min(599, submitted + rng.randint(90, 520)), "acres_total": acres}
        if op in ("fertilizing", "spraying") and rng.random() < 0.3:
            job["requires_capabilities"] = ["rtk"]
        jobs.append(job)
    data["jobs"] = jobs
    events = []
    if rng.random() < 0.8:
        t = rng.choice(TRACTORS)
        at = rng.randint(30, 300)
        events.append({"type": "breakdown", "at": at, "tractor": t})
        if rng.random() < 0.4:
            events.append({"type": "repair", "at": at + rng.randint(40, 150), "tractor": t})
    for _ in range(rng.randint(0, 2)):
        a, b = rng.choice(ROADS)
        events.append({"type": "block_route", "at": rng.randint(0, 300), "a": a, "b": b})
    if rng.random() < 0.3:
        job = rng.choice([j for j in jobs if j["submitted_at"] == 0])
        events.append({"type": "change_priority", "at": rng.randint(20, 200), "job": job["id"], "priority": 5})
    events.sort(key=lambda e: e["at"])
    data["events"] = events
    return data


def run(data, dispatcher):
    data = {k: v for k, v in deepcopy(data).items() if k != "_name"}
    state, events = scenario_from_dict(data)
    sim = Simulation(state, events, dispatcher)
    sim.run()
    m = calculate_metrics(state)
    # Extra diagnostics: priority-weighted lateness (minutes), unfinished acres.
    late_w = 0.0
    for job in state.jobs.values():
        if job.submitted_at > state.now:
            continue
        end = job.finished_at if job.finished_at is not None else state.horizon + 120
        late_w += job.priority * max(0.0, end - job.deadline)
    m["prio_weighted_lateness"] = round(late_w, 2)
    return m, sim


KEYS = ["jobs_completed", "jobs_on_time", "priority_weighted_on_time", "acres_completed",
        "travel_minutes", "idle_minutes", "makespan", "prio_weighted_lateness", "duplicate_or_lost_work"]


def evaluate(factories, suite, verbose=False):
    totals = {name: {k: 0.0 for k in KEYS} for name in factories}
    rows = []
    for data in suite:
        row = {}
        for name, factory in factories.items():
            try:
                m, _ = run(data, factory())
            except Exception as error:  # surface crashes loudly
                print(f"CRASH {name} on {data['_name']}: {error!r}")
                raise
            row[name] = m
            for k in KEYS:
                totals[name][k] += m[k] or 0
        rows.append((data["_name"], row))
        if verbose:
            print(data["_name"], {n: (r["jobs_completed"], r["jobs_on_time"], round(r["travel_minutes"]),
                                      round(r["idle_minutes"])) for n, r in row.items()})
    return totals, rows


def summarize(totals, n):
    names = list(totals)
    print(f"{'metric (total over ' + str(n) + ' scenarios)':40s}" + "".join(f"{x:>14s}" for x in names))
    for k in KEYS:
        print(f"{k:40s}" + "".join(f"{totals[x][k]:14.1f}" for x in names))


def suites():
    return default_suite(), [random_shift(s) for s in range(1, 41)], [random_shift(s) for s in range(101, 141)]


if __name__ == "__main__":
    dsuite, tune, test = suites()
    from fleet.dispatchers.coordinator import CoordinatorDispatcher
    from fleet.dispatchers.matching import MatchingCoordinator
    factories = {"fcfs": FCFSDispatcher, "nearest": NearestDispatcher,
                 "old_matching": MatchingCoordinator, "coordinator": CoordinatorDispatcher}
    for label, s in (("DEFAULT+EVENTS", dsuite), ("RANDOM-TUNE", tune), ("RANDOM-TEST", test)):
        print("==", label)
        totals, _ = evaluate(factories, s)
        summarize(totals, len(s))
