# FinanceBench open sample Adapter card

## Identity and use

- Dataset: `financebench`
- Pinned revision: `cc39aeb4afdf33909ee1412188bf89035950c2eb`
- Adapter: `restricted-external-adapter-v1`
- Scope: the public 150-question open sample only
- Data license record: CC BY-NC 4.0; internal non-commercial evaluation only
- Release eligibility: `false`

The registered JSONL artifacts are materialized under `.data/evals`, verified by byte size and
SHA-256, and excluded from Git. Linked filing PDFs and source documents are separately governed;
the Adapter does not download or commit them.

## Conversion contract

The sanitized input contains the question, company, question/reasoning type, document identity,
period, type, sector, and source URL. Answer, justification, Evidence text, full-page text, and
page gold remain in `FinanceBenchGold` and cannot enter model input through the Adapter contract.

The pinned release contains:

- 150 questions, split evenly across the three upstream question types;
- 84 referenced document ids;
- 189 Evidence records;
- 361 metadata rows covering 360 unique document ids.

`FOOTLOCKER_2023_annualreport` has two upstream metadata rows with different periods. It is not
referenced by the open 150 questions, so the Adapter records the conflict and does not guess a
canonical period. Any ambiguity on a referenced document fails conversion.

## Scoring boundary

The checked report validates artifact integrity, deterministic conversion, and gold separation.
It does not execute a model, retrieve the source PDFs, or produce an official answer-correctness
score. Human answer review, source-document rights review, and owner license approval remain
required.
