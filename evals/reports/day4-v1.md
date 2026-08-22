# Day 4 aggregate deterministic baseline

Day 4 retains the complete 24-case Day 2/3 baseline and adds four independent datasets. The cumulative inventory is 50 cases; metrics are not merged across unlike scorer semantics.

| Layer | Dataset | Cases | Scorer / authority |
|---|---|---:|---|
| Day 2/3 Agent baseline | Day 2 L0 + Day 3 L1/L2 | 24 | Existing Day 2/3 reports and repository tests |
| Memory | `day4-memory@v1` | 8 | `memory-scorer-v1` |
| Memory ablation | `day4-memory-ablation@v1` | 2 | `memory-ablation-scorer-v1` |
| Evidence / Claim | `day4-evidence@v1` | 6 | `evidence-scorer-v1` |
| Research | `day4-research@v1` | 10 | `research-scorer-v1` |
| Total | — | 50 | Repository test suite is the pass/fail authority |

The Research report includes the same-question L0/L2/L3 resource-and-coverage comparison. All Day 4 numbers are deterministic fixture baselines, not live Provider quality, source freshness, external-network latency, or production pricing claims. Day 6 still owns final Report/Citation completeness.
