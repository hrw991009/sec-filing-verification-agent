# sec-fixture-v1 dataset card

## Purpose

This fixture supports deterministic Day 5 contract tests for filing identity,
Dense Knowledge retrieval, typed decimal calculation, Evidence lineage, and
no-answer behavior. It does not measure live EDGAR freshness or general model
quality.

## Source

- Filer: Apple Inc. (`CIK 0000320193`)
- Form/accession: `10-K`, `0000320193-23-000106`
- Report period: `2023-09-30`
- Filed/accepted: `2023-11-03`, `2023-11-02T18:08:27Z`
- Official filing: <https://www.sec.gov/Archives/edgar/data/320193/000032019323000106/aapl-20230930.htm>
- Filing detail: <https://www.sec.gov/Archives/edgar/data/320193/000032019323000106/0000320193-23-000106-index.htm>

The selected facts were checked against the official filing. SEC states that
information on sec.gov is public information that users may copy or further
distribute, with appropriate source citation. This repository does not use the
SEC seal, EDGAR logo, or imply SEC affiliation.

## Frozen questions

1. What were total net sales in fiscal 2023?
2. By how much did total net sales change from fiscal 2022 to fiscal 2023?
3. What was gross margin for the reportable cloud segment?

The third question is intentionally unsupported by this fixture.

## Limitations

- One filer, one accession, one form, and one report period.
- Selected English filing facts only; not a complete filing snapshot.
- No live SEC client, XBRL channel, BM25, RRF, reranker, Verifier, or monitor.
- The fixture must be ingested through the ordinary Knowledge pipeline. The
  manifest hash must match the uploaded file before it can become Evidence.
