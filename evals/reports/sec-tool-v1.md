# `sec-tool-v1` deterministic A0/A1/A2 report

- Manifest SHA-256: `64b55978b122bf043858f884a360b4c72c142ddfb7c56e26ad8d4b80f4068107`
- Cases / strategy runs: 10 / 30
- Deterministic gate: PASS
- Day 7 closeout ready: NO

| Strategy | Case | Simple | Complex | Abstention | Citation | Calc lineage | Steps | Cost (micro USD) | Latency (ms) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A0 | 1.000000 | 1.000000 | 1.000000 | 1.000000 | 1.000000 | 0.000000 | 10 | 252 | 342 |
| A1 | 0.500000 | 1.000000 | 0.166667 | 1.000000 | 0.625000 | 0.000000 | 39 | 1047 | 1171 |
| A2 | 1.000000 | 1.000000 | 1.000000 | 1.000000 | 1.000000 | 1.000000 | 37 | 1120 | 1214 |

## Comparison

- A2 complex gain over A1: 0.833333
- A2 simple degradation from A1: 0.000000
- A2 cost increase: 73 micro USD
- A2 latency increase: 43 ms

## Boundary

This is a deterministic frozen-observation contract report. It is not a live SEC, live model, public benchmark, bilingual, or browser end-to-end result.

Closeout blockers:
- `real_dependencies_not_executed`
- `live_sec_not_executed`
- `live_model_not_executed`
- `browser_e2e_not_executed`
- `paired_bilingual_not_executed`
- `branch_ci_not_passed`
- `pr_ci_not_passed`
- `main_ci_not_passed`
- `owner_review_missing`
