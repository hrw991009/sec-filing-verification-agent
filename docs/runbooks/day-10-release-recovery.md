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

The release acceptance runner builds the complete 12-scenario plan from the production release
Run collection and executes it automatically:

```powershell
pnpm run acceptance:sec
```

No Run ID, Workspace ID, target, probe, command, or observation JSON is entered manually. The
runner derives production Run ownership from PostgreSQL, assigns the frozen targets, creates a
unique state directory per execution, and requires a clean tree whose HEAD matches
`source_commit`. Commands use argv execution without a shell; destructive/secret-bearing commands
are rejected. Captured output is redacted and hashed under `.data/evals`. See the
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

PostgreSQL and MinIO are the durable truth; Milvus and Elasticsearch are derived. Use recovery-specific index/collection names or the disposable integration database. Run the manifest's ingestion test, then remove only the recovery indexes, replay the existing ingestion Job with the same idempotency identity, and compare retrieved filing locators/Evidence IDs. Record exactly one completed side effect and identical final retrieval results.

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

## Notification unknown idempotency

Use a delivery adapter that outlives the bounded drain so the Tool outcome is `unknown`. Do not retry with a new idempotency key. Reconcile provider/application state first, then retry the original identity and prove the total external effect count is at most one.

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
