"""Compare FCFS, nearest feasible and coordinator on the same event timeline."""

import argparse
import json
from pathlib import Path

from fleet.comparison import compare_runs

ROOT = Path(__file__).resolve().parent


def main() -> None:
    """Export measured results, all three logs, and the exact replay input."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, default=ROOT / "scenarios/default.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs")
    args = parser.parse_args()
    with args.scenario.open(encoding="utf-8-sig") as stream:
        result = compare_runs(json.load(stream), args.output_dir)
    print(result["summary"])
    print(f"Comparison: {args.output_dir.resolve() / 'comparison.md'}")


if __name__ == "__main__":
    main()
