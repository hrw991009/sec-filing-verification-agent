# SEC release recovery evidence

- Execution: `not_executed`
- Scenarios: 0/12
- Recovery gate: `false`
- Release ready: `false`

## Metrics

| Metric | Status | Value | Threshold | Gate |
|---|---|---:|---:|---|
| `recovery_success` | `not_measured` | null | >= 1.00 | `unknown` |
| `zero_duplicate_side_effect_rate` | `not_measured` | null | >= 1.00 | `unknown` |
| `zero_data_loss_rate` | `not_measured` | null | >= 1.00 | `unknown` |
| `zero_unauthorized_write_rate` | `not_measured` | null | >= 1.00 | `unknown` |

## Alerts

- `sec-release-recovery-success`: `unknown` - Metric has no eligible recovery exercise evidence.
- `sec-release-zero-duplicate-side-effect-rate`: `unknown` - Metric has no eligible recovery exercise evidence.
- `sec-release-zero-data-loss-rate`: `unknown` - Metric has no eligible recovery exercise evidence.
- `sec-release-zero-unauthorized-write-rate`: `unknown` - Metric has no eligible recovery exercise evidence.

## Blockers

- `recovery_success_not_measured`
- `zero_duplicate_side_effect_rate_not_measured`
- `zero_data_loss_rate_not_measured`
- `zero_unauthorized_write_rate_not_measured`
- `previous_image_release_artifact_not_verified`
- `remote_ci_not_verified`

Unit tests and recovery contracts are not exercise observations. Missing backup, dependency-fault, and previous-image evidence remains unknown and release-blocking.
