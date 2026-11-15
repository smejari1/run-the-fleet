# FCFS vs nearest feasible vs coordinator

| Metric | FCFS | Nearest feasible | Coordinator | Coordinator vs FCFS | Coordinator vs nearest |
|---|---:|---:|---:|---:|---:|
| jobs_completed | 9 | 10 | 10 | +11.11% | +0.00% |
| jobs_on_time | 8 | 8 | 9 | +12.50% | +12.50% |
| deadline_misses | 4 | 4 | 3 | -25.00% | -25.00% |
| priority_weighted_on_time | 14 | 16 | 19 | +35.71% | +18.75% |
| acres_completed | 162.914635 | 181.818883 | 168 | +3.12% | -7.60% |
| travel_minutes | 630.340036 | 556.199725 | 615.083719 | -2.42% | +10.59% |
| idle_minutes | 7.001426 | 4.145725 | 7.568772 | +8.10% | +82.57% |
| makespan | 552.212805 | 554.671727 | 534.626727 | -3.18% | -3.61% |
| safety_violations | 0 | 0 | 0 | +0.00% | +0.00% |
| duplicate_or_lost_work | 0 | 0 | 0 | +0.00% | +0.00% |

Positive differences mean more; negative differences mean less.

All three dispatchers replayed the same 12 initial jobs and 1 operator/scenario events. Completed jobs: FCFS 9, nearest 10, coordinator 10; on time: 8, 8, and 9 respectively. Fleet travel minutes in that order: 630.34, 556.20, and 615.08. Fleet idle minutes: 7.00, 4.15, and 7.57. These are measured outcomes, not a guarantee that the coordinator wins every metric.

All proposals are checked by the independent Safety Agent. Late at-risk work and horizon overruns generate warnings, not rejections.
