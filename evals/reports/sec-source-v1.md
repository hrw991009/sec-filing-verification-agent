# SEC Source v1 Deterministic Closeout Report

Date: 2026-08-27

This report covers the Day 6 source contract only. It uses deterministic fixtures and checked
pytest evidence. It is not a live SEC or model-quality report.

## Inventory

- Dataset: `sec-source-v1@v1`
- Scorer: `sec-source-scorer-v1`
- Cases: 24
- Contract split: 18
- Closeout regression split: 6
- Harness profile: `sec-source-l2-v1`
- Available Tool surface: exactly five `sec.*@v1` read Tools

## Result

| Metric | Result |
|---|---:|
| Contract pass rate | 18 / 18 |
| Closeout pass rate | 4 / 6 |
| Overall case pass rate | 22 / 24 |
| Tool surface adherence | 15 / 15 |
| Source locator resolvability | 20 / 22 |
| Snapshot presence accuracy | 22 / 24 |
| Import presence accuracy | 24 / 24 |
| Bulk coverage readiness | 0 / 2 |
| Future leakage | 0 / 7 |
| Scope violations | 0 / 11 |
| Workspace leakage | 0 / 9 |
| Duplicate commits | 0 / 7 |
| Dependency failures mislabeled as `no_result` | 0 / 7 |

The closeout gate is **not passed**. The two blockers are:

1. `submissions-bulk-watermark`
2. `companyfacts-bulk-watermark`

The repository does not yet implement immutable `submissions.zip`/`companyfacts.zip` snapshots,
`bulk_published_at`/`coverage_through`, or official post-watermark gap closure. These cases keep
their successful golden expectations and record the current observation as `capability_missing`;
they are not removed from the denominator or rewritten as passing.

## Evidence Boundary

- PostgreSQL/MinIO/Milvus/Elasticsearch behavior is bound to the referenced integration tests.
- Ordinary PR evaluation remains deterministic and does not access live SEC endpoints.
- No legal SEC application/contact identity is configured in the current environment, so no live
  SEC smoke was executed.
- This report does not evaluate Day 7 calculation, reconciliation, Hybrid Retrieval, filing diff,
  Day 8 verification/monitoring, or final financial judgment quality.
