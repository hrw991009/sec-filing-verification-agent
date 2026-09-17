# Day 10 release recovery and rollback runbook

> Scope: `sec-release-recovery-v1` staging/release exercises only  
> Safety boundary: never run destructive steps against an unverified production database, bucket, index, collection, or image  
> Evidence boundary: component tests are prerequisites; only completed exercises recorded in `evals/observations/sec-release-recovery-v1.json` are scored

## Preconditions

1. Record the source commit, immutable image digest, operator, environment, start time, expected Run/Case/Workspace IDs, and the approved maintenance window.
2. Confirm the target is an isolated staging environment or a disposable `iip_postgres_test_*` database. Resolve and record every database, bucket, index, collection, container, and image name before changing state.
3. Capture business counts and SHA-256 state digests before fault injection. At minimum include Run, Job, Outbox, Checkpoint, Evidence, Calculation, Monitor, Case, Case Evidence, object, lexical document, and vector entity counts relevant to the scenario.
4. Create an evidence directory outside tracked source files. Evidence must exclude credentials, raw private documents, access tokens, provider payloads, and unrestricted database dumps.
5. Stop immediately if a resolved target is outside the approved environment. Do not use `docker compose down --volumes`, wildcard deletion, or an unpinned image tag.

The scorer requires one observation for every manifest scenario. Each observation binds its evidence file hash, recovery command hash, starting/final state hashes, duration, recovery result, data loss, unauthorized writes, and duplicate side effects. Runtime scenarios also require the original Run and Workspace IDs.

## Automated prerequisites

Run from the repository root after the isolated services are healthy and the endpoint values match the Compose host ports:

```powershell
$env:POSTGRES_TESTS_REQUIRED = '1'
$env:REDIS_TESTS_REQUIRED = '1'
$env:MINIO_TESTS_REQUIRED = '1'
$env:VECTOR_TESTS_REQUIRED = '1'
$env:ELASTICSEARCH_TESTS_REQUIRED = '1'
$env:MILVUS_ENDPOINT = 'http://127.0.0.1:19530'
$env:ELASTICSEARCH_ENDPOINT = 'http://127.0.0.1:19200'
uv run --env-file .env --locked --all-packages pytest --cov=industry_platform --cov-branch --cov-fail-under=80
uv run --locked coverage report --include='apps/backend/src/industry_platform/modules/memory/domain.py,apps/backend/src/industry_platform/modules/memory/service.py,apps/backend/src/industry_platform/modules/memory/eval.py,apps/backend/src/industry_platform/modules/evidence/domain.py,apps/backend/src/industry_platform/modules/evidence/service.py,apps/backend/src/industry_platform/modules/evidence/normalizer.py,apps/backend/src/industry_platform/modules/evidence/eval.py,apps/backend/src/industry_platform/modules/research/domain.py,apps/backend/src/industry_platform/modules/research/service.py,apps/backend/src/industry_platform/modules/research/eval.py,apps/backend/src/industry_platform/workflows/research/*.py' --fail-under=90
```

The pytest references in `evals/manifests/sec-release-recovery-v1.json` must also pass individually. They establish deterministic prerequisites, not the manual outage or rollback result.

## Isolated executor

For the independent **actual-container** exercise and its verified candidate image, see
[Isolated recovery exercise](../isolated-recovery-exercise.md). It covers a real Worker kill
during a Research model request, scoped container outages and populated backup/restore.
Keep its latest-attempt summary separate from the full diagnostic ledger and the frozen
twelve-scenario release gate below. The historical-image rollback is still owner-deferred.

For the eleven locally executable capability checks, use a new evidence directory:

```powershell
pnpm run acceptance:recovery:checks --output-directory .data/evals/recovery-capabilities-20260916
```

This entry point uses disposable PostgreSQL databases, scoped index/object identities, a killed
test Worker process and controlled dependency connection refusal. It never stops the shared
development Compose services, never calls a model API and never sends a real notification.
Each check has a JUnit result and redacted log hash. Skips, empty results, failures, timeouts or
source changes during the batch fail the local check. The report records the source commit,
dirty-tree status and source-file digest. An existing output directory is not overwritten.

When PostgreSQL is a standalone CI service rather than the development Compose service, set
`RECOVERY_POSTGRES_CONTAINER` to that exact container name/ID. This only selects where the
PostgreSQL dump/restore client runs; it does not authorize service stops or source-data deletion.

The original release acceptance runner still builds a 12-scenario plan:

```powershell
pnpm run acceptance:sec
```

Its automatic component checks do **not** prove that a historical production Run underwent the
fault. The final probe now keeps `scenario_verified=false` for those bindings. Likewise, importing
the previous image is only a prerequisite, not rollback acceptance. Full release acceptance
requires the isolated staging exercises below, actual exercised Run identities, an owner-approved
rollback image and a clean tree matching `source_commit`. Do not copy local capability results into
the frozen release observations or change the twelve-scenario denominator to eleven.

Commands use argv execution without a shell; destructive/secret-bearing commands are rejected.
Captured output is redacted and hashed under `.data/evals`. See the
[SEC release acceptance runbook](sec-release-acceptance.md) for inputs and artifacts.

## Fresh migration

Run the manifest's migration node against its randomly named disposable database. Preserve the Alembic output showing `upgrade head -> downgrade base -> upgrade head`, the final head revision, and the database name prefix. A skipped test is a failed exercise.

## Postgres backup restore

Use a dedicated disposable restore database. Never overwrite the source database.

```powershell
$composeFile = 'infra/compose/compose.yaml'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$restoreDatabase = "iip_restore_$stamp" -replace '-', '_'
$containerDump = "/tmp/$restoreDatabase.dump"
$localDump = Join-Path $env:TEMP "$restoreDatabase.dump"
docker compose --env-file .env -f $composeFile exec -T postgres sh -ceu 'pg_dump --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --format=custom --file="$1"' sh $containerDump
docker compose --env-file .env -f $composeFile cp "postgres:$containerDump" $localDump
docker compose --env-file .env -f $composeFile exec -T postgres sh -ceu 'createdb --username="$POSTGRES_USER" "$1"; pg_restore --username="$POSTGRES_USER" --dbname="$1" --exit-on-error "$2"' sh $restoreDatabase $containerDump
```

Query and hash the scoped business rows in both databases. Record identical counts/hashes before dropping only the verified `$restoreDatabase` and removing the two named dump files. A successful `pg_restore` without business-state comparison is insufficient.

## Filing index rebuild

PostgreSQL and MinIO are the durable truth; Milvus and Elasticsearch are derived. Remove only
verified recovery-scoped entries (or an independently isolated index/collection). A completed
ingestion Job must not be reopened. A workspace owner requests a durable reconstruction Job via
`POST /api/v1/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}/versions/{version_id}/rebuild-indexes`
with an `Idempotency-Key`. The dedicated `knowledge.index_rebuild.v1` handler reuses persisted
chunks/embeddings and the existing index writers. The document version, chunks, source objects and
Evidence locators retain their identities; only the operational Job is new. Readiness is restored
only after both indexes succeed. Duplicate acceptance reuses the same Job, and a partial failure
retries idempotent upserts without repeating parsing or model calls. Deletion is serialized with
reconstruction writes and final readiness is lease-fenced.

## Worker interruption resume

Start a formal Job/Run in the isolated environment, capture its lease/fence/checkpoint, then terminate only the selected Worker after a durable checkpoint. Start a replacement Worker, wait for lease expiry/reconciliation, and resume the same Job/Run. The old fence must fail to mutate state; the final Run/Case and side-effect identity must remain singular.

## Redis outage recovery

```powershell
$composeFile = 'infra/compose/compose.yaml'
docker compose --env-file .env -f $composeFile stop redis
docker compose --env-file .env -f $composeFile up -d --wait redis
```

During the stopped interval, verify PostgreSQL retains the unpublished Outbox/Job state and no success is returned. After restart, run the dispatcher/Worker and prove exactly one publication and business effect for the original identity.

## MinIO outage recovery

Stop only MinIO after an accepted upload/filing Job is durable. The Worker must persist a retryable dependency failure without marking the DocumentVersion ready. Restart MinIO, verify the private bucket remains non-public, resume the same Job, and compare the final object key/hash and side-effect count.

## Elasticsearch outage rebuild

Stop only Elasticsearch during a recovery-scoped ingestion. PostgreSQL/MinIO state must remain authoritative and the version must not become ready with only one index. Restart Elasticsearch, rebuild the recovery lexical index from the same version, and resolve every checked locator to the same Evidence identity.

## Milvus outage rebuild

Stop only Milvus during a recovery-scoped ingestion. Restart it without deleting PostgreSQL or MinIO data, rebuild the recovery collection, and verify vector candidate identity plus the dual-index ready transition. An Elasticsearch-only success is not a pass.

## SEC 429 backoff

Run the frozen adapter and real Redis budget tests. For a controlled release exercise, use a stubbed response sequence or an explicitly approved SEC window; do not deliberately overload SEC. Record bounded waits, attempt count, shared budget identity, final typed result, and confirmation that 429 never became `no_result` or fabricated Evidence.

## Dead letter replay

Force the bounded retry path using the frozen failure adapter, preserve the Job/Outbox/Event dead-letter state, then issue one authorized replay using the original idempotency and side-effect identity. The replay may complete once; a second replay must be a no-op or conflict, never a second Case/Monitor/notification.

The owner-only endpoint is `POST /api/v1/workspaces/{workspace_id}/jobs/{job_id}/replay`.
Send `Idempotency-Key` for the recovery request and JSON
`{"expected_dispatch_generation":1,"additional_attempts":1}`. One request grants at most three
additional attempts, with a lifetime ceiling of 100; it preserves the logical Job ID and original
business idempotency key. The database rechecks current owner membership even on repeated calls.
It retains the dead-letter event and appends a replay audit plus one new Outbox generation.
Cancelled Jobs and terminal business Runs are not resurrected; original Run budgets are unchanged.
Migration `f8c0d2e4a579` preserves one terminal Job event per delivery generation. Downgrade refuses
to discard history after a Job has terminal facts from multiple generations.

## Notification unknown idempotency

Use a delivery adapter that outlives the bounded drain so the Tool outcome is `unknown`. Do not retry with a new idempotency key. Reconcile provider/application state first, then retry the original identity and prove the total external effect count is at most one.

`IdempotentWriteRecovery` enforces the original authorized Tool scope, key and payload digest,
validates returned receipt identity and keeps failed/uncertain lookups UNKNOWN. A provider must
offer durable atomic same-key delivery and authoritative lookup; plain SMTP does not satisfy this
contract. The current check uses a controlled notification provider backed by a disposable
PostgreSQL receipt table. It proves recovery behavior, not an actual email/webhook integration.

## Previous image rollback

Set `PREVIOUS_IMAGE_DIGEST` to the last owner-approved immutable digest and verify it before launch:

```powershell
docker image inspect $env:PREVIOUS_IMAGE_DIGEST --format '{{json .RepoDigests}}'
```

Restore a pre-exercise database backup into a disposable database, confirm its Alembic revision is supported by both images, and launch the previous image against only that restored environment. Run health, authentication, SEC filing read, one fixed verification Scenario, Evidence drilldown, and Monitor/Case read smoke tests. Do not downgrade the live database in place. Record both image digests, schema revision, smoke results, rollback duration, and final state hash.

## Closeout

1. Restore every stopped service and confirm Compose health.
2. Verify recovery success is 12/12, data loss/unauthorized writes/duplicate side effects are zero, and all runtime-bound scenarios reference the original Run/Workspace.
3. Open the generated redacted evidence files and verify their hashes against the automatic observation/report.
4. Archive the dynamic report/schema plus remote release-job URL. Do not copy dynamic observations into the checked `not_executed` snapshot. A local report does not close branch/main CI or owner acceptance.
5. Delete only the verified disposable databases, recovery indexes/collections, and named temporary dumps. Preserve the redacted evidence bundle according to the release retention policy.
