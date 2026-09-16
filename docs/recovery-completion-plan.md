# Recovery completion plan

Scope agreed on 2026-09-16: complete and verify the existing recovery capabilities,
including missing recovery operations. Previous-image rollback remains deferred until
the owner selects a baseline. This does not reopen the accepted product-stability work.

## Boundaries

- Keep PostgreSQL Job/Outbox/checkpoints authoritative; do not add another runtime.
- Recovery mutations require current workspace-owner authorization, scoped targets,
  durable audit facts, and bounded attempts. Never rewind a successful business action.
- Reuse ingestion writers and stable chunk/Evidence identities for index reconstruction.
- An UNKNOWN external write requires reconciliation of the original idempotency key;
  absence of a conclusive provider result must remain UNKNOWN, not authorize a new send.
- Use disposable databases and uniquely scoped objects/index entries. Dependency
  connection refusal is a network-fault exercise, not evidence of a container restart.
- Component tests, controlled fault tests, full runtime exercises and release approval
  remain separate evidence layers. Do not mark the frozen twelve-scenario gate passed
  while rollback or required runtime bindings are missing.

## Work sequence

1. Baseline: migration, PostgreSQL leases, Redis Outbox, private MinIO, dual indexes,
   Research approval/resume and Tool timeout checks.
2. Exercise real adapter connection failures during an accepted ingestion/dispatch;
   verify retry state, retained checkpoints, original identities and duplicate delivery.
3. Verify backup/restore of all public tables, not just a fixed shortlist.
4. Add authorized bounded dead-letter replay, preserving logical Job identity and audit
   history, without changing existing business idempotency keys.
5. Add scoped index reconstruction from persisted source data, including partial-failure
   recovery and repeatability. A completed ingestion Job is not blindly re-executed.
6. Close controlled UNKNOWN/reconciliation/idempotent-delivery verification.
7. Correct the automatic recovery runner: no implicit default Compose outage, no
   unrelated historical Run binding promoted to exercised runtime evidence, no import
   smoke promoted to an image rollback.
8. Run regression/static checks and document measured outcomes plus remaining release
   prerequisites. No external notification delivery or production outage is authorized.

## Initial evidence

- Source baseline: `59b04acb6816fa3e82bb584c79855626e05c08a9`.
- Recovery contract tests: 39 passed.
- Existing real dependency checks: 19 passed.
- Added connection-failure paths: Redis plus MinIO/Milvus/Elasticsearch; all four
  recovered in disposable databases. The ingestion happy path also passed.
- Dynamic local JUnit evidence: `.data/evals/recovery-20260916/` (not release approval).

Completion is assessed per capability and per evidence level, not by the number of
passing pytest nodes alone.

## Implemented recovery operations

- Owner-authorized dead-letter replay: one logical Job, an audited new dispatch
  generation, bounded extra attempts, current membership recheck and stale-lease fencing.
  Historical terminal events are retained per dispatch generation. Cancelled Jobs and
  terminal Agent Runs cannot be resurrected through this endpoint.
- Durable index reconstruction: an owner submits a dedicated Job for a document version;
  the Worker rebuilds both derived indexes from persisted chunks and embeddings using
  existing writers. Partial failure remains non-ready and retries the same identities.
- Idempotent-write reconciliation: the retained authorized Tool call supplies the original
  key and payload digest. An authoritative provider receipt is reused; UNKNOWN remains
  UNKNOWN. Retrying requires provider-side atomic same-key deduplication. This is a
  provider integration contract with a durable controlled test provider, not an implemented
  email/webhook channel or proof of a live notification delivery.
- Backup/restore verifies every public business table and the migration revision. Cleanup
  never drops a restore database whose creation failed or whose identity is out of scope.
- The local eleven-check runner records source identity, JUnit results and redacted log
  hashes. It explicitly keeps release acceptance false and never stops shared services.

## Deployment and remaining release work

Apply migration `f8c0d2e4a579` before using the recovery endpoints. The normal development
database has not been migrated by this work; migration tests use disposable databases.
Downgrade refuses to invalidate retained multi-generation terminal history after a replay.
The migration and runbook therefore belong in the later rollback-baseline review.

Previous-image rollback is deferred by the owner. Formal staging fault injection, live
integration bindings where required, immutable image identity and clean-commit evidence
remain separate release prerequisites. Local capability checks must not be copied into
the frozen twelve-scenario observations as if those exercises had occurred.

## Verified local capability batch (2026-09-16)

The frozen-source batch at `.data/evals/recovery-capabilities-20260916-verified/report.json`
passed **11/11 scenarios, 12/12 test cases, zero failures and zero skips**. Its source
digest is `d10bd451314ff981f568583315141da867a8326b481df79495f5d73d4ca73f02`, based on
commit `59b04acb6816fa3e82bb584c79855626e05c08a9` plus the recorded dirty working tree.
The source remained unchanged throughout this batch. `checks_passed=true` and
`release_accepted=false` are intentional separate results.

Covered capabilities: migration round-trip, PostgreSQL backup/restore, filing index
reconstruction, Worker process interruption/resume, Redis dispatch failure/recovery,
MinIO/Milvus/Elasticsearch connection failure/recovery, bounded SEC 429 backoff,
owner-authorized dead-letter replay and controlled UNKNOWN write reconciliation.

During regression, the index-loss check exposed a read-visibility race: a default
Milvus read could return pre-deletion state. Both deletion and reconstruction checks
now request strong consistency and validate the API success code. The earlier batch
whose source changed while running is retained as non-passing evidence; it is not
the verified batch above.

Final local regression: **1,758 backend tests passed**, with branch coverage **80.68%**
(80% gate), core-module coverage **90.09%** (90% gate); **99 frontend tests passed**.
Ruff lint/format, mypy, TypeScript checks,
Semgrep (zero findings) and the generated release-readiness contract checks passed.
Backend JUnit and coverage evidence are under `.data/evals/recovery-20260916/` as
`full-regression-final.xml` and `coverage-final.json`. The release ledger still retains
its 15 existing release blockers; no owner sign-off or remote CI result is inferred
from these local results.
