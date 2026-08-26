# SEC Fixture F0/F1/F2 Report

Dataset: `sec-fixture-financial-research@sec-fixture-v1`  
Scorer: `sec-financial-v1`

| Tier | Source | Numeric | Formula | Evidence | Typed calculation | Tokens | Cost (micro USD) | Latency |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| F0 fixture oracle/full context | 1 | correct | correct | 0 | 0 | 160 | 30 | 20 ms |
| F1 fixture Dense RAG | 1 | correct | correct, model-produced | 1 filing | 0 | 400 | 90 | 70 ms |
| F2 F1 + calculator | 1 | correct | correct, typed | 1 filing + 1 calculation | 1 | 640 | 150 | 110 ms |

F2 demonstrates typed formula and Evidence lineage, not better model arithmetic by itself. The five-case dataset also contains a direct filing fact and an evidence-insufficient question; the latter must produce an uncertain draft without fabricated Evidence.

This is a deterministic local fixture comparison. It does not measure live SEC access, EDGAR freshness, XBRL coverage, BM25/RRF/reranking, Verifier/Monitor behavior, or production-model quality.
