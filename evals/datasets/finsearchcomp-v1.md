# FinSearchComp Adapter card

## Identity and artifacts

- Dataset: `finsearchcomp`
- Pinned revision: `55b6393fcf3c8f749ba5a69a70b20d4ef6f67caf`
- Adapter: `restricted-external-adapter-v1`
- Data license record: CC BY 4.0; code license record: MIT
- Release eligibility: `false`

The full 635-case artifact and 594-case AkShare artifact are materialized under `.data/evals`,
verified by byte size and SHA-256, and excluded from Git. Raw references, ground truth, and judge
prompts are separated from the sanitized Agent input.

## Historical and live separation

Reports are intentionally split:

- Historical: 219 T2 lookup cases and 172 T3 investigation cases, all 391 present in both
  artifacts.
- Dynamic live: 244 T1 cases; 203 have an AkShare-compatible version and 41 require other or
  professional data dependencies.

The 594 AkShare cases share prompt identity, reference answer, judge prompts, and ground truth
with their full-release counterparts. All 203 shared dynamic cases have a different upstream
`time` field in the AkShare artifact. The Adapter preserves each artifact timestamp and reports
the drift rather than silently normalizing it.

## Scoring boundary

The historical report validates conversion only. The dynamic report sets live dependency runs,
model runs, repeated runs, upstream LLM judge execution, official scores, and `pass^k` to false,
zero, or null as applicable. Dynamic market state and judge variance must be reported separately
and cannot become ordinary PR hard gates or be averaged into fixed historical results.
