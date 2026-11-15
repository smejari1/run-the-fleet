"""Replay one immutable input scenario through all three dispatchers."""

import json
from copy import deepcopy
from pathlib import Path

from fleet.dispatchers.coordinator import CoordinatorDispatcher, DEFAULT_WEIGHTS
from fleet.dispatchers.fcfs import FCFSDispatcher
from fleet.dispatchers.nearest import NearestDispatcher
from fleet.metrics import calculate_metrics
from fleet.scenario import scenario_from_dict
from fleet.sim import Simulation


def compare_runs(scenario: dict, output_dir: Path | None = None) -> dict:
    """Compare identical inputs; optionally export measured results and logs."""
    metrics = {}
    for dispatcher in (FCFSDispatcher(), NearestDispatcher(), CoordinatorDispatcher()):
        state, events = scenario_from_dict(deepcopy(scenario))
        simulation = Simulation(state, events, dispatcher)
        simulation.run()
        metrics[dispatcher.name] = calculate_metrics(state)
        if output_dir is not None:
            simulation.log.write(output_dir, dispatcher.name)
            (output_dir / f"{dispatcher.name}_metrics.json").write_text(
                json.dumps(metrics[dispatcher.name], indent=2) + "\n", encoding="utf-8")
    baseline_changes = {}
    for baseline_name in ("fcfs", "nearest"):
        changes = {}
        for name, baseline in metrics[baseline_name].items():
            value = metrics["coordinator"][name]
            if baseline is None or value is None:
                changes[name] = {"difference": None, "percent": None}
            else:
                difference = value - baseline
                changes[name] = {"difference": round(difference, 6),
                                 "percent": round(100 * difference / abs(baseline), 2) if baseline else (0 if value == 0 else None)}
        baseline_changes[baseline_name] = changes
    fcfs, nearest, coordinator = metrics["fcfs"], metrics["nearest"], metrics["coordinator"]
    summary = (
        f"All three dispatchers replayed the same {len(scenario['jobs'])} initial jobs and "
        f"{len(scenario.get('events', []))} operator/scenario events. "
        f"Completed jobs: FCFS {fcfs['jobs_completed']}, nearest {nearest['jobs_completed']}, "
        f"coordinator {coordinator['jobs_completed']}; on time: {fcfs['jobs_on_time']}, "
        f"{nearest['jobs_on_time']}, and {coordinator['jobs_on_time']} respectively. "
        f"Fleet travel minutes in that order: {fcfs['travel_minutes']:.2f}, {nearest['travel_minutes']:.2f}, "
        f"and {coordinator['travel_minutes']:.2f}. Fleet idle minutes: {fcfs['idle_minutes']:.2f}, "
        f"{nearest['idle_minutes']:.2f}, and {coordinator['idle_minutes']:.2f}. "
        "These are measured outcomes, not a guarantee that the coordinator wins every metric."
    )
    result = {**metrics, "changes": baseline_changes["fcfs"], "baseline_changes": baseline_changes,
              "summary": summary, "weights": DEFAULT_WEIGHTS,
              "input": {"initial_jobs": len(scenario["jobs"]), "events": len(scenario.get("events", [])),
                        "horizon": scenario.get("horizon", 600), "seed": scenario.get("seed", 0)}}
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "comparison_input.json").write_text(json.dumps(scenario, indent=2) + "\n", encoding="utf-8")
        (output_dir / "comparison.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        rows = ["# FCFS vs nearest feasible vs coordinator", "",
                "| Metric | FCFS | Nearest feasible | Coordinator | Coordinator vs FCFS | Coordinator vs nearest |",
                "|---|---:|---:|---:|---:|---:|"]
        for name, baseline in fcfs.items():
            value = coordinator[name]
            percentages = [baseline_changes[key][name]["percent"] for key in ("fcfs", "nearest")]
            labels = [f"{percent:+.2f}%" if percent is not None else "n/a" for percent in percentages]
            rows.append(f"| {name} | {baseline} | {nearest[name]} | {value} | {labels[0]} | {labels[1]} |")
        rows.extend(["", "Positive differences mean more; negative differences mean less.", "", summary,
                     "", "All proposals are checked by the independent Safety Agent. Late at-risk work and horizon overruns generate warnings, not rejections.", ""])
        (output_dir / "comparison.md").write_text("\n".join(rows), encoding="utf-8")
    return result
