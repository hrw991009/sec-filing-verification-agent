# SEC release common-case Run evidence

- Execution: `not_executed`
- Evidence layer: `offline_capability`
- Common cases: 10
- Runs: 0/50
- Global A0-A4 comparable: `false`
- Production default: `null`

## Metrics

| Metric | Category | Status | Value | Threshold | Gate |
|---|---|---|---:|---|---|
| `case_accuracy` | `capability` | `not_measured` | null | >= 1.00 | `unknown` |
| `citation_resolvability` | `capability` | `not_measured` | null | >= 1.00 | `unknown` |
| `no_answer_abstention` | `capability` | `not_measured` | null | >= 0.90 | `unknown` |
| `retrieval_recall_at_5` | `capability` | `not_measured` | null | >= 0.80 | `unknown` |
| `runtime_binding_completeness` | `observability` | `not_measured` | null | >= 1.00 | `unknown` |
| `future_leakage_rate` | `security` | `not_measured` | null | <= 0.00 | `unknown` |
| `cross_workspace_rate` | `security` | `not_measured` | null | <= 0.00 | `unknown` |
| `unauthorized_write_rate` | `security` | `not_measured` | null | <= 0.00 | `unknown` |
| `duplicate_effect_rate` | `security` | `not_measured` | null | <= 0.00 | `unknown` |
| `injection_attack_success_rate` | `security` | `not_measured` | null | <= 0.00 | `unknown` |
| `recovery_success` | `recovery` | `not_measured` | null | >= 1.00 | `unknown` |

## Alerts

- `sec-agent-case-accuracy`: `unknown` - Metric has no eligible production Run evidence.
- `sec-agent-citation-resolvability`: `unknown` - Metric has no eligible production Run evidence.
- `sec-agent-no-answer-abstention`: `unknown` - Metric has no eligible production Run evidence.
- `sec-agent-retrieval-recall-at-5`: `unknown` - Metric has no eligible production Run evidence.
- `sec-agent-runtime-binding-completeness`: `unknown` - Metric has no eligible production Run evidence.
- `sec-agent-future-leakage-rate`: `unknown` - Metric has no eligible production Run evidence.
- `sec-agent-cross-workspace-rate`: `unknown` - Metric has no eligible production Run evidence.
- `sec-agent-unauthorized-write-rate`: `unknown` - Metric has no eligible production Run evidence.
- `sec-agent-duplicate-effect-rate`: `unknown` - Metric has no eligible production Run evidence.
- `sec-agent-injection-attack-success-rate`: `unknown` - Metric has no eligible production Run evidence.
- `sec-agent-recovery-success`: `unknown` - Metric has no eligible production Run evidence.

## Blockers

- `common_case_runtime_runs_not_executed`
- `case_accuracy_not_measured`
- `citation_resolvability_not_measured`
- `no_answer_abstention_not_measured`
- `retrieval_recall_at_5_not_measured`
- `runtime_binding_completeness_not_measured`
- `future_leakage_rate_not_measured`
- `cross_workspace_rate_not_measured`
- `unauthorized_write_rate_not_measured`
- `duplicate_effect_rate_not_measured`
- `public_benchmark_predictions_not_executed`
- `live_three_repetitions_not_executed`
- `external_license_review_not_complete`
- `language_owner_review_not_complete`
- `remote_ci_owner_closeout_not_complete`

Missing Run evidence remains unknown and release-blocking; it is not scored as a zero-failure success.
