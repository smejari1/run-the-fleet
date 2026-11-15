"""Reproduce twenty seeded field/job/fuel/failure scenarios without extra packages."""

import argparse
import json
import random
from pathlib import Path

from fleet.dispatchers.coordinator import CoordinatorDispatcher
from fleet.dispatchers.fcfs import FCFSDispatcher
from fleet.dispatchers.nearest import NearestDispatcher
from fleet.metrics import calculate_metrics
from fleet.scenario import scenario_from_dict
from fleet.sim import Simulation

ROOT = Path(__file__).resolve().parent
DISPATCHERS = (FCFSDispatcher, NearestDispatcher, CoordinatorDispatcher)


def safety_scenario(seed: int) -> dict:
    """Generate repeated field work, varied fuel/equipment and timed disruptions."""
    rng = random.Random(seed)
    kits = [["plow", "sprayer"], ["sprayer", "planter"], ["plow", "planter"]]
    data = {"seed": seed, "horizon": 120, "refuel_minutes": 5,
            "fields": [{"id": f"F{i}", "name": f"Field {i}", "x": rng.uniform(2, 25),
                        "y": rng.uniform(2, 25), "acres": 20} for i in range(4)],
            "tractors": [{"id": f"T{i}", "name": f"Tractor {i}", "implements": kit,
                          "capabilities": ["rtk"] if i != 1 else [], "speed": rng.uniform(1, 2),
                          "fuel": [25, 60, 100][i], "burn_travel": rng.uniform(.1, .4),
                          "burn_work": rng.uniform(.2, .5), "work_rate": rng.uniform(.7, 1.5)}
                         for i, kit in enumerate(kits)], "jobs": []}
    for i in range(8):
        submitted = 0 if i < 5 else rng.randint(5, 20)
        implement = ["plow", "sprayer", "planter"][i % 3]
        data["jobs"].append({"id": f"J{i}", "type": implement, "field": f"F{rng.randrange(4)}",
                             "requires_implement": implement, "priority": rng.randint(1, 5),
                             "deadline": rng.randint(max(25, submitted + 1), 115),
                             "submitted_at": submitted, "acres_total": rng.randint(1, 7)})
    initial, _ = scenario_from_dict(data)
    a, b = rng.choice(sorted(initial.navigation.edges))
    failed = rng.choice(["T0", "T1", "T2"])
    failure_at = rng.randint(15, 45)
    block_at = rng.randint(5, 20)
    data["events"] = [{"type": "breakdown", "tractor": failed, "at": failure_at},
                      {"type": "repair", "tractor": failed, "at": failure_at + 12},
                      {"type": "block_route", "a": a, "b": b, "at": block_at},
                      {"type": "unblock_route", "a": a, "b": b, "at": block_at + 15}]
    return data


def main() -> None:
    """Write exact stress inputs and outcomes, failing if any rejection/error occurs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs/safety_stress")
    args = parser.parse_args()
    if not 1 <= args.count <= 100:
        parser.error("count must be between 1 and 100")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for seed in range(args.count):
        data = safety_scenario(seed)
        (args.output_dir / f"seed_{seed:02}.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        for dispatcher_type in DISPATCHERS:
            state, events = scenario_from_dict(data)
            sim = Simulation(state, events, dispatcher_type())
            error = None
            try:
                sim.run()
            except (AssertionError, RuntimeError) as failure:
                error = str(failure)
            rows.append({"seed": seed, "dispatcher": sim.dispatcher.name, "error": error,
                         "metrics": calculate_metrics(state),
                         "warnings": sum(r["type"] == "SAFETY_WARNING" for r in sim.log.records)})
    failed = [row for row in rows if row["error"] or row["metrics"]["safety_violations"]
              or row["metrics"]["duplicate_or_lost_work"]]
    report = {"scenarios": args.count, "runs": len(rows), "failed_runs": len(failed), "results": rows}
    (args.output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"{len(rows) - len(failed)}/{len(rows)} runs passed over {args.count} seeded scenarios.")
    if failed:
        print(json.dumps(failed, indent=2))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
