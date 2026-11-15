"""Run a single deterministic simulation from PowerShell or any terminal."""

import argparse
import json
from pathlib import Path

from fleet.dispatchers.base import NoopDispatcher
from fleet.dispatchers.fcfs import FCFSDispatcher
from fleet.dispatchers.coordinator import CoordinatorDispatcher
from fleet.dispatchers.nearest import NearestDispatcher
from fleet.metrics import calculate_metrics
from fleet.scenario import load_scenario
from fleet.sim import Simulation

ROOT = Path(__file__).resolve().parent


def main() -> None:
    """Load a scenario, run it, and write both event log formats."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, default=ROOT / "scenarios/default.json")
    parser.add_argument("--dispatcher", choices=["none", "fcfs", "nearest", "coordinator"], default="coordinator")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs")
    args = parser.parse_args()
    try:
        state, events = load_scenario(args.scenario)
        dispatcher = {"fcfs": FCFSDispatcher, "nearest": NearestDispatcher,
                      "coordinator": CoordinatorDispatcher, "none": NoopDispatcher}[args.dispatcher]()
        simulation = Simulation(state, events, dispatcher)
        simulation.run()
        simulation.log.write(args.output_dir, args.dispatcher)
        metrics = calculate_metrics(state)
        (args.output_dir / f"{args.dispatcher}_metrics.json").write_text(
            json.dumps(metrics, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(2, f"Scenario/run error: {error}\n")
    print(json.dumps({"dispatcher": args.dispatcher, "time": state.now,
                      "tractors": {key: tractor.state for key, tractor in state.tractors.items()},
                      "jobs": {key: job.state for key, job in state.jobs.items()},
                      "metrics": metrics}, indent=2))
    print(f"Event logs: {args.output_dir.resolve() / (args.dispatcher + '_events.*')}")


if __name__ == "__main__":
    main()
