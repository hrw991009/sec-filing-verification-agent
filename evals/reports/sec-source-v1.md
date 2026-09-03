# SEC Source v1 Deterministic Closeout Report

Date: 2026-09-01

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
| Closeout pass rate | 6 / 6 |
| Overall case pass rate | 24 / 24 |
| Tool surface adherence | 15 / 15 |
| Source locator resolvability | 22 / 22 |
| Snapshot presence accuracy | 24 / 24 |
| Import presence accuracy | 24 / 24 |
| Bulk coverage readiness | 2 / 2 |
| Future leakage | 0 / 7 |
| Scope violations | 0 / 11 |
| Workspace leakage | 0 / 9 |
| Duplicate commits | 0 / 7 |
| Dependency failures mislabeled as `no_result` | 0 / 7 |

The deterministic closeout gate is **passed**. Both bulk cases execute the production-composable
streaming archive, immutable object locator, validated CIK member, published/coverage watermark,
and post-watermark official API source contracts. Failure fixtures cover missing watermarks,
truncated archives, unsafe ZIP members, and missing CIK entries.

## Evidence Boundary

- PostgreSQL/MinIO/Milvus/Elasticsearch behavior is bound to the referenced integration tests.
- Ordinary PR evaluation remains deterministic and does not access live SEC endpoints.
- The deterministic report keeps `live_sec_executed=false`. A separate live identity smoke ran on
  2026-09-01 through `OfficialSecJsonClient`; its non-PII artifact remains under ignored `.data`.
- This report does not evaluate Day 7 calculation, reconciliation, Hybrid Retrieval, filing diff,
  Day 8 verification/monitoring, or final financial judgment quality.
