"""Structured and readable logs without timestamps from the real clock."""

import json
from pathlib import Path


class EventLog:
    """Retain deterministic records and export both required log formats."""

    def __init__(self):
        self.records: list[dict] = []

    def add(self, t: float, event_type: str, *, tractor=None, job=None, **details) -> None:
        """Record a state change with simulation time and decision data."""
        record = {"t": round(t, 9), "type": event_type, "details": details}
        if tractor is not None:
            record["tractor"] = tractor
        if job is not None:
            record["job"] = job
        self.records.append(record)

    def write(self, directory: str | Path, run: str) -> None:
        """Write UTF-8 JSONL and human-readable text files."""
        output = Path(directory)
        output.mkdir(parents=True, exist_ok=True)
        (output / f"{run}_events.jsonl").write_text(
            "".join(json.dumps(record, sort_keys=True, allow_nan=False) + "\n" for record in self.records),
            encoding="utf-8",
        )
        lines = []
        for record in self.records:
            subjects = " ".join(str(record[key]) for key in ("tractor", "job") if key in record)
            details = record["details"]
            message = details.get("message") or details.get("reason") or json.dumps(details, sort_keys=True)
            lines.append(f"[t={record['t']:g}] {subjects} {record['type']}: {message}".strip())
        (output / f"{run}_events.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
