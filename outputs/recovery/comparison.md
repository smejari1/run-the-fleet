# FCFS vs nearest feasible vs coordinator

| Metric | FCFS | Nearest feasible | Coordinator | Coordinator vs FCFS | Coordinator vs nearest |
|---|---:|---:|---:|---:|---:|
| jobs_completed | 7 | 9 | 10 | +42.86% | +11.11% |
| jobs_on_time | 7 | 7 | 9 | +28.57% | +28.57% |
| deadline_misses | 5 | 5 | 3 | -40.00% | -40.00% |
| priority_weighted_on_time | 12 | 14 | 17 | +41.67% | +21.43% |
| acres_completed | 128.18367 | 177.605418 | 162.251209 | +26.58% | -8.65% |
| travel_minutes | 680.873781 | 483.330972 | 541.219879 | -20.51% | +11.98% |
| idle_minutes | 5.127114 | 4.807337 | 6.524078 | +27.25% | +35.71% |
| makespan | 523.036149 | 376.055513 | 534.626727 | +2.22% | +42.17% |
| safety_violations | 0 | 0 | 0 | +0.00% | +0.00% |
| duplicate_or_lost_work | 0 | 0 | 0 | +0.00% | +0.00% |

Positive differences mean more; negative differences mean less.

All three dispatchers replayed the same 12 initial jobs and 1 operator/scenario events. Completed jobs: FCFS 7, nearest 9, coordinator 10; on time: 7, 7, and 9 respectively. Fleet travel minutes in that order: 680.87, 483.33, and 541.22. Fleet idle minutes: 5.13, 4.81, and 6.52. These are measured outcomes, not a guarantee that the coordinator wins every metric.

All proposals are checked by the independent Safety Agent. Late at-risk work and horizon overruns generate warnings, not rejections.
