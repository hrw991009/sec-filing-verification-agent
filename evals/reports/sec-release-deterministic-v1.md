# SEC release deterministic contract report

- Contract gate: PASS
- Global A0-A4 comparable: `false`
- Production default selected: `false`

## Pairwise decisions

| Segment | Shared cases | Primary gain | Simple degradation | Cost delta | Latency delta | Decision |
|---|---:|---:|---:|---:|---:|---|
| `sec-tool-a1-to-a2` | 10 | 0.833333 | 0.000000 | 73 | 43 | `retain_for_next_evidence_layer` |
| `sec-verification-a2-to-a3` | 14 | 0.714286 | 0.000000 | 340 | 400 | `retain_for_next_evidence_layer` |
| `sec-verification-a3-to-a4-operational` | 14 | 1.000000 | 0.000000 | 320 | 1010 | `retain_for_operational_scope` |

The pairwise decisions are valid only inside their named common-case source suite. The 10-case A0/A1/A2 and 14-case A2/A3/A4 aggregates are not merged into a global A0-A4 score.

Release blockers:
- `global_a0_a4_common_case_manifest_missing`
- `retrieval_recall_at_5_not_measured`
- `offline_capability_runs_not_executed`
- `live_model_runs_below_three_repetitions`
- `case_run_trace_evidence_binding_missing`
- `external_dataset_owner_review_missing`
- `language_review_missing`
- `remote_ci_not_passed`
- `owner_review_missing`
