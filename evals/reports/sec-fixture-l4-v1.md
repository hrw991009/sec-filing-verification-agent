# SEC Fixture Research L4 Recovery Report

Dataset: `sec-fixture-research-l4-recovery@sec-fixture-l4-v1`  
Scorer: `research-l4-recovery-v1`

| Gate | Expected behavior | Duplicate side effects |
| --- | --- | ---: |
| hard stop | Continue after the last successful Research node Checkpoint | 0 |
| allow | Persist Decision, claim one resume proof, create Job + Outbox | 0 |
| deny | Append `approval_denied`; do not create resume work | 0 |
| timeout | Append `approval_timed_out`; do not create resume work | 0 |
| repeat | Return the existing Decision and resume Job | 0 |
| cancel | Reauthorize current cancellation before scheduling | 0 |
| budget | Recheck step, Token, cost, and deadline ceilings | 0 |

The executable hard-stop test resumes after `research_loop` without calling Knowledge or calculator again. The PostgreSQL test verifies hashed resume proofs, allow/deny/expiry-timeout, cancel and budget gates, stable repeated resume Job identity, atomic Job/Outbox creation, and a unique side-effect ledger row. HTTP and Workbench component tests verify that a user decision is persisted before resume is requested.

This is deterministic fixture and local dependency evidence. Timeout is materialized when a decision is attempted at or after expiry; no background expiry scanner is claimed. The report does not prove live SEC freshness, production-model quality, or the Day 8 cross-refresh and Worker-restart combination gate.
