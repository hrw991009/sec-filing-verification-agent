# Day 4 Memory Eval v1

- Dataset: `day4-memory@v1`
- Scorer: `memory-scorer-v1`
- Cases: 8
- Deterministic fixture report; no LLM judge is used.

| Metric | Numerator | Denominator | Value |
| --- | ---: | ---: | ---: |
| Write accuracy | 1 | 1 | 1.000 |
| Retrieval precision | 3 | 3 | 1.000 |
| Utility | 3 | 3 | 1.000 |
| Pollution | 0 | 3 | 0.000 |
| Conflict handling | 1 | 1 | 1.000 |
| Edit effectiveness | 1 | 1 | 1.000 |
| Deletion residual | 0 | 1 | 0.000 |
| Average input tokens | 1592 | 8 | 199.000 |
| Average latency (ms) | 200 | 8 | 25.000 |

The deletion denominator is fixed to one explicit deletion case; residual is the number of Memory references left in the next Run/search/detail projection. Token and latency values are deterministic fixture baselines, not production SLO claims.
