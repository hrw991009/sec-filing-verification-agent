# TAT-QA Dataset Card

## Identity And Rights

- Source: [TAT-QA](https://github.com/NExTplusplus/TAT-QA)
- Pinned revision: `870accc41953dcde885aabeb963d94aabdc0fbc3`
- Data license: CC BY 4.0; code license: MIT. Attribution and the separate rights of source financial reports remain required.
- The authoritative artifact byte sizes, SHA-256 values, split counts, and allowed uses are in `evals/registry/sec-agent-datasets-v1.json`.

## Adapter And Scorer

- The Adapter extracts only question, paragraphs, table, and typed source locators into `fixed-context-adapter-v1`; gold answer, scale, derivation, and mapping remain outside model input.
- The official `tatqa_metric.py` and `tatqa_utils.py` sources are pinned with SHA-256 `2aeeac479f89f8c76300af1cc0e8d098eb86af84bc386b38b6ab4af484a6dea8` and `a84bb2f960737cf0a53733637a674cc4b20ef030a2be6a4b21dc2c4356f415ec`.
- Official metrics are answer exact match, numeracy-aware F1, and scale accuracy. Derivation exact and source exact/F1 are project auxiliary metrics with independent denominators.
- Scorable split denominators are train `13215`, dev `1668`, and released test gold `1663`.

## Known Boundaries

- At the pinned revision, `tatqa_dataset_test.json` has `1669` questions while `tatqa_dataset_test_gold.json` has `1663`, with zero shared question UIDs. They must not be joined by order or fuzzy text.
- The released test gold contains its own table/paragraph context and is therefore registered as mixed input/gold. The Adapter sanitizes that artifact into 1663 test cases; the separate 1669-question test input is integrity-checked but not scored.
- Train/dev only provide partial paragraph relevance, while released test mappings provide exact cells/spans except three empty mappings. Source metrics include only cases with complete mappings.
- This benchmark does not measure open retrieval, EDGAR identity, temporal visibility, or durable Agent state.
