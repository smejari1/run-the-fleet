# Run the Fleet

**Hack Arizona AgX · "Run the Fleet" challenge (American Tractor Company)**

A coordinator that assigns jobs to a small fleet of simulated autonomous
tractors, checks every assignment for safety, re-plans the moment something
changes (a breakdown, a blocked road, a new job, a priority change) and
explains every decision in plain language.

- **Simulation:** our own Python simulation, **not AgOpenWeb**. The challenge
  is scored on the coordinator, so we kept the simulator small and spent the
  time on decisions.
- **Approach:** algorithmic. No language model, so every decision is
  computed, repeatable and explainable.
- **Stack:** Python 3.11+ standard library for the simulation, routing and
  planning. Flask serves the dashboard (plain HTML/JS/SVG, no build step).
  pytest runs the checks.
- **Code:** <https://github.com/smejari1/run-the-fleet>

## Hand-in checklist

| Required | Where |
|---|---|
| Code | This repository |
| How to run it, AgOpenWeb or own simulation | [Quick start](#quick-start-windows-powershell) below · own simulation |
| Scenario / configuration file | `scenarios/default.json` (8 fields, 21 roads, 3 tractors, 12 jobs) and `scenarios/demo_events.json` (the same shift plus the demo's live events) |
| Event log from the demo run | `outputs/demo/coordinator_events.txt` (readable) and `.jsonl` (structured); baseline logs beside it |
| Comparison against the simple baseline | `outputs/comparison.md` (default shift), `outputs/demo/comparison.md` (demo with events), [Results](#results) below |

## Quick start (Windows PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-lock.txt
python server.py
```

Open <http://127.0.0.1:5000>. If script activation is disabled, call the
environment's Python directly:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe server.py
```

The server listens on this computer only. Use `--port 5001` if port 5000 is
busy and `--autoplay` to start playing immediately. Closing the browser does
not stop playback (press Pause first); Ctrl+C stops the server. After
updating the code, restart the server and refresh the page with Ctrl+F5.

## Two-minute demo

1. **Start** the shift at 4–8 min/s. Each tractor takes the first job in its
   planned queue; planned routes appear on the map.
2. Around 01:40, choose **agxt2** and press **Break down tractor**. Its
   finished acres are kept and the rest of its work is re-planned. The
   **Plan updates** panel says why each job moved, e.g. *"Because agxt2
   broke down, J09 moved from agxt2 to agxt3."*
3. Choose road **F4–F7** and press **Block road**. Tractors detour on the
   shortest open road, with fuel rechecked.
4. Click **Why?** on any job (J12 is a good one) for its full reasoning:
   travel, deadline, alternatives, and which tractors can't do it and why.
5. Press **Run comparison** to replay the same shift and events with all
   three dispatchers, side by side.

## What the dashboard does

- **Field map:** 8 fields, 21 roads, depot and tractors **agxt1**, **agxt2**,
  **agxt3** moving along real roads. Fields fill as acres are worked. Closed
  roads are red and dashed. Click a field or tractor for details.
- **Playback:** Start, Pause, Step +1 min, speed, Reset. The dispatcher
  (Coordinator, FCFS baseline, Nearest feasible) is chosen on Reset so every
  run is a fair, fresh start.
- **Preloaded jobs:** the demo uses the scenario's existing plowing, irrigation,
  fertilizing, harvesting, planting and spraying jobs. There is no Add a job form.
- **Inject an event:** break down or repair a tractor. There are no automatic
  breakdowns in the default scenario; the operator decides when.
- **Road controls:** block or reopen any road.
- **Job queue:** status, owner, progress, deadline, at-risk and recovery
  badges, a **Why?** button on every job, and a priority control for pending
  jobs (active work is never interrupted).
- **Plan updates:** a live, plain-language feed of assignments, breakdowns,
  re-plans, detours and at-risk warnings, with the important ones highlighted.
- **Independent Safety Agent bar:** rejected proposals and warnings.
- **Three-way comparison:** headline cards (jobs, on time, travel and idle
  versus FCFS, in real minutes and %), bar charts and the full metrics table.
  The replay includes every job and event added so far and does not change
  the live run. **Download this session's replay** saves the exact input.

Up to 100 jobs per shift (50 per API batch). The queue lives in memory, so
download the replay before a restart if you want to keep a session.

## Architecture

We follow ATC's suggested "AI Farm Orchestrator" layer; each agent is a plain
Python module.

| ATC component | Our implementation |
|---|---|
| Fleet Agent (the core) | `fleet/dispatchers/coordinator.py`: rolling-horizon planner |
| Navigation Agent | `fleet/navigation.py`: Dijkstra on open roads, detours around closures |
| Safety Agent | `fleet/safety.py`: independent check of every proposal before it runs |
| Command Center | `server.py` + `web/` (map, controls, queue, Why?, Plan updates, comparison) |
| Agronomy Agent (optional) | Not built |

Other modules: `fleet/sim.py` (minute-tick simulation and events),
`fleet/explain.py` (plain-language explanations), `fleet/metrics.py`
(metrics and consistency checks), `fleet/comparison.py` (fair replays),
`fleet/dispatchers/fcfs.py` and `nearest.py` (baselines),
`fleet/dispatchers/matching.py` (our earlier one-step coordinator, kept for
reference and benchmarking only).

## How the coordinator decides

At every decision point (a tractor becomes free, a job arrives, or any event
happens) the coordinator plans **all** waiting work for the **whole** fleet,
then commits only the next step:

1. **Screen out unsafe pairs:** wrong implement or capability, no open route,
   or not enough fuel to do the work, return to the depot and keep the reserve.
   If a tractor needs fuel first, the plan sends it to refuel.
2. **Plan every tractor's queue:** busy tractors join the plan from the time
   and place they will become free. Queues are built by cheapest insertion
   from four job orderings (deadline, priority, slack, arrival) and improved
   by local search (move a job, swap two jobs) until nothing improves.
3. **Score the whole plan** (lower is better):

   | Term | Weight |
   |---|---|
   | Job not finished before the shift ends | 5000 |
   | Late job | 1500 + 200 × priority + 2 × priority × minutes late |
   | Finish time | 0.05 per minute |
   | Fleet travel | 1 per minute |
   | Planned idle (tractors waiting while another still has work) | 2 per minute |

4. **Dispatch, then re-plan:** each free tractor gets the first job in its
   queue (or a refuel), and field reservations keep one active job per field.
   Breakdowns, closures, repairs and priority changes need no special code:
   they simply produce a new plan.

The weights were tuned on 40 random shifts plus the demo farm with events,
then checked on 40 different random shifts (`benchmark.py`). The planner is a
heuristic, not a proof of optimality.

**Baselines.** FCFS gives each free tractor the earliest-submitted job it can
do. Nearest feasible gives it the closest job it can do. Both apply the same
equipment, capability, route, fuel and field rules as the coordinator, so the
comparison is between valid plans.

## Safety (independent check)

Every proposal from any dispatcher passes `fleet/safety.py` before it changes
any state. The validator recomputes the route, travel and work times, fuel for
work plus depot return and reserve, and checks tractor and job availability,
unique ownership, equipment, capabilities, open roads, correct endpoints and
overlapping field work. A rejected proposal is logged, counted and not
applied; the dispatcher is asked again without that pair (retries are
bounded). Late work is allowed only when flagged at risk, and shows as a
warning, not a hidden failure.

## Recovery

A breakdown stops the tractor where it is (mid-road breakdowns keep their
physical position). Completed acres are recorded in a work ledger and never
redone; only the remainder returns to the queue, and **all** pending work is
re-planned. If no working tractor can do a job it stays pending and at risk
until a repair. Invariant checks after every event and tick catch duplicate
ownership, lost or double-counted work and bad completions; a failure is
logged and pauses the live server.

## Explanations

Every sentence comes from the real decision data (`fleet/explain.py`):

- **Assignments:** the chosen tractor, job and field, travel time, required
  equipment, estimated finish/deadline slack, planned queue and alternatives.
  Fuel numbers are captured at dispatch, so viewing the explanation later
  does not substitute the tractor's current fuel for its original fuel.
  Late assignments describe this plan's lateness; they do not claim a proof
  that no other possible plan could meet the deadline.
- **Re-plans:** after a breakdown, repair, road closure or reopening,
  priority change or new job, each job that changed tractor is logged with
  the cause (`REPLAN` in the event log).
- **At risk:** the planned finish, minutes past the deadline, and whether it
  runs past the end of the shift.
- **API:** `GET /api/why/<job_id>` and `GET /api/explanations` (the feed).

Click **Why?** beside a job in the queue; the explanation appears in the
**Plan updates** panel. This works while paused, including for jobs scheduled
to arrive later. Explanations name the active dispatcher policy. Failed
requests remain visible with Retry, and closing/resetting cannot be undone
by an older response arriving late.

After backend code changes, restart the Python server as well as refreshing
the browser. A browser refresh alone does not load new Flask endpoints.
Use Ctrl+F5 to refresh dashboard assets after an update.

## Results

All numbers below are measured from the code in this repository. Positive %
means more, negative means less. Table times are minutes; hh:mm is the shift
clock (00:00 is the start of the 10-hour shift).

### Default shift (12 jobs, no events) — `outputs/comparison.md`

| Metric | FCFS | Nearest feasible | Coordinator | vs FCFS |
|---|---:|---:|---:|---:|
| Jobs completed | 12 | 12 | 12 | tie |
| Jobs on time | 11 | 10 | 11 | tie |
| Fleet travel (min) | 610.0 | 546.9 | **490.0** | **−19.7%** |
| Fleet idle (min) | 189.7 | 211.7 | **23.9** | **−87.4%** |
| Last job finished (min) | 548.1 | 530.7 | **448.1** | **−18.3%** |
| Safety rejections | 0 | 0 | 0 | |
| Lost or duplicated work | 0 | 0 | 0 | |

On-time jobs can't improve here: urgent job J12 (deadline 01:40, submitted at
00:30) can't be finished on time by any tractor. The coordinator does it
first, finishing at 03:01; FCFS leaves it until 09:08.

### Demo shift with events — `outputs/demo/comparison.md`

agxt2 breaks down at 01:40, road F4–F7 closes at 02:10, J11 is raised to
priority 5 at 02:30, and agxt2 is repaired at 03:40
(`scenarios/demo_events.json`).

| Metric | FCFS | Nearest feasible | Coordinator | vs FCFS |
|---|---:|---:|---:|---:|
| Jobs completed | 10 | 12 | **12** | **+20%** |
| Jobs on time | 10 | 8 | **11** | **+10%** |
| Priority-weighted on time | 19 | 20 | **24** | **+26%** |
| Fleet travel (min) | 775.8 | 430.3 | 457.3 | **−41.1%** |
| Fleet idle (min) | 56.2 | 268.2 | 211.6 | +276% |
| Safety rejections | 0 | 0 | 0 | |
| Lost or duplicated work | 0 | 0 | 0 | |

The coordinator's idle time is higher than FCFS's here. FCFS keeps its
tractors busy driving (776 travel minutes) and still leaves 2 jobs unfinished;
the coordinator finishes all 12, so tractors that are done wait while the last
one finishes, and that waiting counts as idle.

### Stress test — `python benchmark.py`

Totals across many deterministic shifts (about 4 minutes to run).

| Coordinator vs FCFS | Demo farm + breakdowns, repairs, closures, priority change (19 runs) | 40 new random shifts (never used for tuning) |
|---|---:|---:|
| Jobs completed | **+14%** (212 vs 186) | **+7%** (460 vs 428) |
| Jobs on time | **+11%** (184 vs 166) | **+31%** (355 vs 271) |
| Fleet travel | **−22%** | **−20%** |
| Priority-weighted lateness | **−70%** | **−37%** |
| Fleet idle | **−28%** | +57% (worse) |
| Lost or duplicated work | 0 | 0 |

On the random shifts the coordinator completes the most jobs and has the least
travel and lateness of all four dispatchers. Two honest exceptions: its idle
time is higher than FCFS's (mainly the waiting after it finishes everything,
as above), and our earlier one-step coordinator gets slightly more jobs on
time there (379 vs 355) while completing fewer (449 vs 460).

### Safety stress test — `python run_safety_stress.py`

20 seeded random scenarios (fields, jobs, deadlines, fuel, equipment,
breakdowns, repairs, road closures) × 3 dispatchers: **60/60 runs passed**,
with zero rejected assignments and zero invariant failures. Inputs and report:
`outputs/safety_stress/`.

## Run the checks

```powershell
.\.venv\Scripts\python.exe -m pytest -q                       # 156 tests
.\.venv\Scripts\python.exe run_compare.py                     # default shift -> outputs/
.\.venv\Scripts\python.exe run_compare.py --scenario scenarios/demo_events.json --output-dir outputs/demo
.\.venv\Scripts\python.exe benchmark.py                       # stress test (~4 min)
.\.venv\Scripts\python.exe run_safety_stress.py               # safety stress test
.\.venv\Scripts\python.exe run_sim.py --dispatcher coordinator   # or fcfs, nearest, none
```

`run_compare.py` writes `comparison.md`, `comparison.json`, the exact
`comparison_input.json`, and each dispatcher's metrics and event logs. A
replay downloaded from the dashboard can be passed with
`--scenario path\to\fleet_replay.json`. Recorded breakdown replays are kept in
`outputs/recovery_replay_180.json` and `outputs/recovery_replay.json`, with
results in `outputs/recovery_180/` and `outputs/recovery/`.

The 156 tests cover simulation timing and fuel, input validation,
determinism, all three dispatchers, the planner's sequencing and balancing,
breakdown recovery and work preservation, detours and disconnected roads,
every safety rejection reason, explanations and the dashboard API.

## Simulation conventions

- One tick is one simulated minute; travel, work and fuel keep fractional
  progress inside a tick. Decisions happen at whole minutes, only when a job
  arrives, an event occurs or a tractor becomes free.
- Completions come before events at the same minute; same-minute events keep
  their order; operator actions run right after that minute's regular
  dispatch, and replays preserve this order exactly.
- Every single-road closure in the default map leaves an alternative route
  (multiple closures can isolate a field). A tractor already on a closed road
  finishes that segment first; there is no teleporting and no negative fuel.

## Limitations

- On the default shift with no events the coordinator ties FCFS on completed
  and on-time jobs (one job is impossible to finish on time); its advantage
  there is travel, idle time and finishing sooner.
- Idle time can be higher than FCFS's on shifts where the coordinator finishes
  all the work early (see the demo and random-shift results).
- Field reservations are deliberately conservative: one active job per field,
  even when two visits might not overlap.
- Road traffic spacing is not modeled, only field work.
- No language model, no Agronomy Agent and no AgOpenWeb integration.

Build history and per-phase reports are in `AGENTS.md` and
`outputs/phase*_report.txt`.
