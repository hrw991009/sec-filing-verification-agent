# FinQA Dataset Card

## Identity And Rights

- Source: [FinQA](https://github.com/czyssrs/FinQA)
- Pinned revision: `0f16e2867befa6840783e58be38c9efb9229d742`
- Data license: CC BY 4.0; code license: MIT. Attribution and the separate rights of the underlying financial reports remain required.
- The authoritative artifact byte sizes, SHA-256 values, split counts, and allowed uses are in `evals/registry/sec-agent-datasets-v1.json`.

## Adapter And Scorer

- The Adapter extracts only question, pre/post text, table, and stable source locators into `fixed-context-adapter-v1`; raw mixed input/gold files never enter model context.
- The official `evaluate.py` source is pinned at the dataset revision with SHA-256 `845cd131cab843eceff256cf6d392978cc470a7da4a80107beb56027fdca5c13`.
- Official metrics are execution accuracy and symbolic program accuracy. Supporting-fact exact/F1 are project auxiliary metrics and are reported separately.
- Official split denominators are train `6251`, dev `883`, and test `1147`.

## Known Boundaries

- The upstream data contains 74 empty display answers; the official execution gold remains present, so the Adapter uses `exe_ans` only as the non-empty display-answer fallback.
- One train case uses the upstream sentinel `text_-1`. It remains in the official execution/program denominator but is excluded from the supporting-fact auxiliary denominator because no valid locator exists.
- This fixed-context benchmark does not measure retrieval, accession selection, point-in-time behavior, durable Agent state, or live model quality by itself.
