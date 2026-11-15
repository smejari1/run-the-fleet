# FCFS vs nearest feasible vs coordinator

| Metric | FCFS | Nearest feasible | Coordinator | Coordinator vs FCFS | Coordinator vs nearest |
|---|---:|---:|---:|---:|---:|
| jobs_completed | 12 | 12 | 12 | +0.00% | +0.00% |
| jobs_on_time | 11 | 10 | 11 | +0.00% | +10.00% |
| deadline_misses | 1 | 2 | 1 | +0.00% | -50.00% |
| priority_weighted_on_time | 21 | 20 | 21 | +0.00% | +5.00% |
| acres_completed | 200 | 200 | 200 | +0.00% | +0.00% |
| travel_minutes | 609.996283 | 546.935958 | 489.958878 | -19.68% | -10.42% |
| idle_minutes | 189.660238 | 211.740703 | 23.874327 | -87.41% | -88.72% |
| makespan | 548.107729 | 530.669998 | 448.055513 | -18.25% | -15.57% |
| safety_violations | 0 | 0 | 0 | +0.00% | +0.00% |
| duplicate_or_lost_work | 0 | 0 | 0 | +0.00% | +0.00% |

Positive differences mean more; negative differences mean less.

All three dispatchers replayed the same 12 initial jobs and 0 operator/scenario events. Completed jobs: FCFS 12, nearest 12, coordinator 12; on time: 11, 10, and 11 respectively. Fleet travel minutes in that order: 610.00, 546.94, and 489.96. Fleet idle minutes: 189.66, 211.74, and 23.87. These are measured outcomes, not a guarantee that the coordinator wins every metric.

All proposals are checked by the independent Safety Agent. Late at-risk work and horizon overruns generate warnings, not rejections.
