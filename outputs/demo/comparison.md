# FCFS vs nearest feasible vs coordinator

| Metric | FCFS | Nearest feasible | Coordinator | Coordinator vs FCFS | Coordinator vs nearest |
|---|---:|---:|---:|---:|---:|
| jobs_completed | 10 | 12 | 12 | +20.00% | +0.00% |
| jobs_on_time | 10 | 8 | 11 | +10.00% | +37.50% |
| deadline_misses | 2 | 4 | 1 | -50.00% | -75.00% |
| priority_weighted_on_time | 19 | 20 | 24 | +26.32% | +20.00% |
| acres_completed | 196.767375 | 200 | 200 | +1.64% | +0.00% |
| travel_minutes | 775.783069 | 430.253948 | 457.266163 | -41.06% | +6.28% |
| idle_minutes | 56.245878 | 268.179413 | 211.596359 | +276.20% | -21.10% |
| makespan | 507.180489 | 549.922232 | 540.620841 | +6.59% | -1.69% |
| safety_violations | 0 | 0 | 0 | +0.00% | +0.00% |
| duplicate_or_lost_work | 0 | 0 | 0 | +0.00% | +0.00% |

Positive differences mean more; negative differences mean less.

All three dispatchers replayed the same 12 initial jobs and 4 operator/scenario events. Completed jobs: FCFS 10, nearest 12, coordinator 12; on time: 10, 8, and 11 respectively. Fleet travel minutes in that order: 775.78, 430.25, and 457.27. Fleet idle minutes: 56.25, 268.18, and 211.60. These are measured outcomes, not a guarantee that the coordinator wins every metric.

All proposals are checked by the independent Safety Agent. Late at-risk work and horizon overruns generate warnings, not rejections.
