# `sec-verification-v1` A2/A3/A4 report

- Manifest SHA-256: `3cf99c06957858ad5ec2f7db1e147b52524f8cda9d9488b79f0f0740383d8a3e`
- Cases / strategy runs: 14 / 42
- Deterministic gate: PASS
- Security gate: PASS
- Fault gate: PASS
- Day 8 closeout ready: NO

## Strategy quality

| Strategy | Question | Simple | Complex | Operational | Citation | Recovery | False support | Duplicate effect | Cost (micro USD) | Latency (ms) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A2 | 0.444444 | 1.000000 | 0.285714 | 0.000000 | 0.777778 | 0.000000 | 0.285714 | 0.000000 | 1360 | 1640 |
| A3 | 1.000000 | 1.000000 | 1.000000 | 0.000000 | 1.000000 | 0.250000 | 0.000000 | 0.000000 | 1700 | 2040 |
| A4 | 1.000000 | 1.000000 | 1.000000 | 1.000000 | 1.000000 | 1.000000 | 0.000000 | 0.000000 | 2020 | 3050 |

## Deterministic / security / fault layers

- `deterministic`: 9/9 (1.000000)
- `security`: 7/7 (1.000000)
- `fault`: 6/6 (1.000000)

## A3 versus A2

- Complex gain: 0.714286
- Simple degradation: 0.000000
- Cost increase: 340 micro USD
- Latency increase: 400 ms

## Boundary

A4 operational accuracy and recovery are reported separately from ordinary question quality. This checked report uses frozen observations backed by executable contract references; it is not a live SEC or live-model result.

Closeout blockers:
- `live_sec_not_executed`
- `live_model_not_executed`
- `dedicated_monitor_browser_not_executed`
- `monitor_fault_injection_not_executed`
- `branch_ci_not_passed`
- `pr_ci_not_passed`
- `main_ci_not_passed`
- `owner_review_missing`
