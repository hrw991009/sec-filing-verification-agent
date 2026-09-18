# Isolated recovery exercise

The initial exercise used merged source at `dd8ca1672ea92e4111b0d261c379120bb170effd`.
The current owner-selected clean baseline and its successful rollback rehearsal are
recorded in the final section, using merged source `bfe9f742c344be9fe579f944d4df74056a5ef952`.
The application is built from a Git archive, not the developer working tree. Python,
uv and infrastructure images are pinned by digest; the local application image ID,
source revision and exercise artifacts are recorded together. A local image ID is
not a registry digest or an owner-approved historical rollback image.

The corrected candidate and its measured closeout are recorded below. It includes an
explicit source patch and must not be represented as an unmodified merged commit.

The exercise must use its own Compose project, network, volumes, credentials and
loopback ports. No development database or container is a fault-injection target.
Production API, Worker, dispatcher and reconciler run from the same application
image. Acceptance goes through authenticated APIs; database inspection is read-only.
Retry due times and lease expiry must advance naturally, not by rewriting rows.

Scope: fresh migration, authenticated ingestion, actual dependency interruption and
restart, worker interruption, scoped derived-index loss/reconstruction, authorized
dead-letter replay and backup/restore. Source and business identities must survive;
duplicate delivery must not create duplicate results. Model quality is not scored
by a recovery exercise. External SEC throttling must not be deliberately induced.

There is no notification product integration in scope. Historical-image rollback
remains separate. The frozen release denominator is not silently changed: this
exercise records its actual scope and does not claim the original release gate passed.

## Initial failed image baseline (2026-09-17)

The pinned baseline is recorded in [baseline-dd8ca16.json](../infra/recovery/baseline-dd8ca16.json).
The local tag is `sec-filing-verification-agent:recovery-dd8ca16`; its immutable local
image ID is `sha256:28da74f2bb52142745bb6ae0f7d43904eac214569a20607204f71003fff56383`.
The image archive is retained under `.data/recovery-images/` with its SHA-256 in the
baseline file. It has not been pushed to a registry or approved as a rollback baseline.

Project `iip-recovery-20260917-a2` used its own network, volumes and generated credentials.
API/Worker/dispatcher/reconciler all used this exact image, without source bind mounts.
The database schema reached `f8c0d2e4a579`. Job leases were 15 seconds and heartbeats
3 seconds in this exercise. No retry due time or lease timestamp was manually rewritten.

Ten logical checks passed:

1. Fresh database migration, registration/login, authenticated PDF ingestion.
2. SIGKILL of the real Celery Worker after an ingestion checkpoint; same Job recovery.
3. Redis container stop/restart with the original Outbox retained and delivered.
4. MinIO container stop/restart with source-object integrity retained.
5. Elasticsearch container stop/restart with same-version ingestion recovery.
6. Milvus container stop/restart with same-version ingestion recovery.
7. Natural retry exhaustion followed by owner-authorized, idempotent dead-letter replay.
8. Drop of the entire disposable collection/index, then reconstruction of all ready versions.
9. Two duplicate messages through the real Redis broker, without new Job lifecycle events.
10. Backup/restore of the populated database with matching all-public-table state digests.

The **Research Worker checkpoint-resume check failed**. The original Run was
`6199b4ee-6cf8-5007-9509-2b26423c047e`, Job `fe89385e-6e90-45c5-837a-94fc07bf231a`.
Three durable checkpoints survived (`clarify_scope`, `write_research_brief`, `plan`),
but after lease reconciliation the legacy orphan terminalizer marked the Run and Job
failed with `job_execution_abandoned`. Its query treats a running Research Run whose
Job enters retry wait/dispatched as abandoned, despite the Research loader supporting
checkpoint resume. This is a production recovery gap, not a model-quality result.
Fixing the terminalizer must preserve cancellation, exhausted-job and non-resumable
Run cleanup, and must be followed by a rebuilt candidate image and repeated exercise.

The Research check configured the existing live model provider and frozen SEC fixture
sources; it did not establish a successful live model completion, live SEC access,
browser E2E, Monitor/Case recovery, or an external notification integration. External
SEC 429/backoff and historical-image rollback were not executed by this batch.

Dynamic evidence is under `.data/isolated-recovery/iip-recovery-20260917-a2/`:
`report.json`, `research-durable-facts.json`, sanitized process logs and `artifacts.json`.
Earlier diagnostic attempts are retained separately. The report also retains an initial
Research API-startup probe failure; it is not counted as another logical capability.
`checks_passed=false` and `release_accepted=false` must remain false for this baseline.
Do not upload `runtime.env`, `account.json` or `compose.json`: they contain local test
credentials, and the runtime environment may contain the existing model API key.

## Reproduction entry points

Use a new directory named `iip-recovery-<hex-or-date-suffix>` inside `.data/isolated-recovery`.
Build the Dockerfile from a Git archive of the stated revision before `prepare`; a tag
alone is insufficient identity. Dependencies must already be present locally, and the
selected loopback ports 25432/26379/28000/29000/29200/29530 must be free.

```powershell
uv run --locked python infra/recovery/exercise.py prepare --directory .data/isolated-recovery/iip-recovery-20260917-c1 --image sec-filing-verification-agent:recovery-dd8ca16-3a7e2aa8a42c --revision dd8ca1672ea92e4111b0d261c379120bb170effd
uv run --locked python infra/recovery/exercise.py start --directory .data/isolated-recovery/iip-recovery-20260917-c1
uv run --locked python infra/recovery/exercise.py exercise --directory .data/isolated-recovery/iip-recovery-20260917-c1
uv run --locked python infra/recovery/exercise.py configure-model --directory .data/isolated-recovery/iip-recovery-20260917-c1
uv run --locked python infra/recovery/exercise.py research --directory .data/isolated-recovery/iip-recovery-20260917-c1
uv run --locked python infra/recovery/exercise.py backup --directory .data/isolated-recovery/iip-recovery-20260917-c1
uv run --locked python infra/recovery/exercise.py summarize --directory .data/isolated-recovery/iip-recovery-20260917-c1
uv run --locked python infra/recovery/exercise.py collect --directory .data/isolated-recovery/iip-recovery-20260917-c1
uv run --locked python infra/recovery/exercise.py stop --directory .data/isolated-recovery/iip-recovery-20260917-c1
```

`configure-model` copies only the configured model/SEC settings into this isolated runtime;
`research` exercises the live-provider-configured Research interruption; `collect` records
durable facts and sanitized logs; `stop` stops only validated project containers. It never
deletes volumes or changes `.env`. Do not run fault actions against a shared environment.

`build` freezes the current tracked source plus explicitly inventoried untracked source files
under `.data/recovery-images/<project>/source`. The base commit, tracked patch, added-file
digests and Dockerfile digest are retained alongside `build.json`. Use the resulting tag with
`prepare --image`; the environment pins its immutable image ID, not the mutable tag. Never
include `.env` in the build context. The reproduction above uses the already archived candidate.

## Corrected candidate: recovery engineering closeout

The candidate is [baseline-research-recovery.json](../infra/recovery/baseline-research-recovery.json):
`sec-filing-verification-agent:recovery-dd8ca16-3a7e2aa8a42c`, immutable local image ID
`sha256:e2cace07a9e8749f939094843f98d5f7f187b6fe3dc9e3a29821ccf403deef92`.
Project `iip-recovery-20260917-b2` used new independent volumes, network and credentials.
All four application actors ran that image without source mounts. The same pinned
infrastructure versions and schema `f8c0d2e4a579` were used.

Two production gaps were fixed:

- Orphan reconciliation preserves non-cancelled, checkpoint-backed Research retry candidates.
  Filtering occurs before the batch limit; true orphans cannot be starved. Cancellation,
  terminal Jobs and non-resumable Runs still converge to terminal state.
- The loader and workflow contract accept validated model-attempt events after the latest
  graph checkpoint, rather than requiring the checkpoint to be the final event. An interrupted
  model Step is retained as `failed/worker_interrupted`; recovery uses the next bounded model
  slot and preserves committed usage, Step counts, original budgets and event history. Unknown
  provider usage is explicitly marked, not reported as a known zero charge. Uncheckpointed Tool
  effects remain fail-closed; this is not permission to replay an unknown external write.

All **11 required logical checks have passing latest verified results**: the ten container/data
checks listed above plus actual Research Worker interruption during a live model request.
The final Research Run was `1bae25f9-34e9-52d1-9f15-6037eab5137e`, original Job
`d1d7df93-b368-47fb-ad82-6f218625bc11`. It resumed once, completed with ten retained/appended
checkpoints and exactly one assistant message, and exposed its result through the authenticated
result-view API. The Job kept its ID, attempt count increased from 1 to 2, and fence from 1 to 3.
Its interrupted model Step was durably failed; no Step remained running at completion.

The populated backup/restore check was repeated **after** Research recovery. All public-table
state digests matched, including the new Research facts:
`00ceeb548abb35088ece9a8821e5182b5b3a896a8cc3fbdbdddd4d04cbe0bc99`.

Evidence is in `.data/isolated-recovery/iip-recovery-20260917-b2/`:

- `verification-summary.json` requires every named check and records its latest attempt;
  `checks_passed=true`, `release_accepted=false`.
- `report.json` retains every attempt. An earlier Research postcondition probe used the wrong
  message table name, even though that Run had already recovered successfully. The corrected
  probe was rerun through a fresh complete Research interruption and passed. Therefore the
  attempt ledger remains `checks_passed=false`, and the summary explicitly records
  `single_failure_free_batch=false`; this was not a failure-free first attempt.
- Earlier `a2`/`b1` evidence is retained. `b1` also exposed a harness identity-hash bug: SQL row
  order was incorrectly included. Hashing now sorts stable IDs, and `b2` passed the actual whole
  index-loss/rebuild check. These failures were not removed or relabelled as successes.
- `research-durable-facts.json`, sanitized actor logs and `artifacts.json` retain supporting
  facts and hashes. Sensitive local environment/account/Compose files are excluded.

Validation: **1,779 full backend tests passed, zero failures/skips**. A subsequent 53-test
Research subset added seven boundary tests; aggregate branch coverage is **80.71% overall,
90.18% core**, checked at two-decimal precision. Harness contract tests (five), Ruff,
formatting, mypy and the scoped Semgrep scan also passed. Full JUnit, supplemental JUnit and
coverage are under `.data/recovery-images/`. Supplemental changes after the image build were
test/harness-only: all 327 application Python files matched the frozen image source export.

This closes the agreed recovery implementation and independent-environment exercise work.
It does **not** approve the frozen twelve-scenario release gate: owner-deferred historical-image
rollback, clean committed/published image provenance, remote CI and release owner approval remain
separate. SEC 429/shared-budget and migration round-trip checks passed in the regression suite;
the live model used frozen SEC fixtures. No live SEC outage, Monitor/Case recovery, browser E2E
or actual notification channel is claimed by this exercise.

Closeout: the exercise projects have been stopped; their data volumes, image archives and
evidence remain available. No development service was stopped, and the developer `.env` was
not changed. At this original closeout the changes were local and uncommitted;
PR #25 subsequently merged them as `bfe9f742c344be9fe579f944d4df74056a5ef952`.

## Owner-selected clean baseline (2026-09-17)

The owner selected the current merged version as the baseline. Its
[baseline record](../infra/recovery/baseline-bfe9f74.json) pins the clean Git archive,
Dockerfile, local immutable image ID, exported archive and verification summary.
Both PR #25 checks and [merged-commit CI](https://github.com/hrw991009/sec-filing-verification-agent/actions/runs/35202733652)
passed. No source patch or untracked source was present in the frozen build context.
The first build encountered an OpenCV wheel download timeout; retrying the **same**
context and Dockerfile succeeded. This is retained as a build attempt, not an exercise failure.

- Tag: `sec-filing-verification-agent:recovery-bfe9f74-d85a8b85fcba`.
- Local image ID: `sha256:f3cb5da408fd8751d4db3397daaecb98aa51f839233c256ad702aa0440377394`.
- Archive: `.data/recovery-images/sec-filing-verification-agent-recovery-bfe9f74-d85a8b85fcba.tar`.
- Fresh isolated project: `iip-recovery-20260917-c1`, schema `f8c0d2e4a579`.
- All **11 logical recovery checks plus actual image rollback passed without a failed exercise attempt**.
  The populated backup was repeated after successful live-model Research recovery.
- Authenticated SEC filing reads, completed Research results and two retained Evidence
  citations resolved successfully. Monitor/Case collection reads returned zero rows;
  this proves endpoint reads, not populated Monitor/Case recovery.

The application image uses clean merged production source. Subsequent changes are to
the external exercise harness/tests/documentation, not the application runtime.
All 327 application Python files match the earlier verified candidate snapshot.
The archive is local, not a published registry image; publishing is not required for
this local recovery baseline. A local image ID must not be advertised as a remote pull address.

The previous verified candidate `recovery-dd8ca16-3a7e2aa8a42c` was used for an actual
**same-schema image rollback** rehearsal. It has the same schema and application source
as this baseline; this is not proof of cross-schema downgrade or a materially different
application-version rollback. The unpatched `dd8ca16` image has known Research recovery
failures and was not used. The owner approved the current clean baseline; this rehearsal
does not designate the older candidate as an owner-approved long-term historical release.

The opt-in runner `infra/recovery/rollback.py` checks immutable, distinct image IDs,
all four running actors and compatible schema heads. It stops only the scoped actors,
restores a complete snapshot into a fresh database, switches all actors to the previous
image, verifies retained reads/citations, and exercises real Research interruption on
that image. Its cleanup returns to the original baseline/configuration. It never
downgrades or overwrites the source database. Unit checks cover fail-closed image
validation and configuration restoration after a failed old-image smoke.
**The runner's existence does not count as an executed rollback.** In this case it was
executed successfully: all four actors used the previous immutable image, retained
Research results and two Evidence citations resolved, and a fresh live-model Research
Worker interruption recovered on the restored database. The previous-image part took
78.921 seconds. All four actors then returned to the clean current image and passed
the retained-result smoke again.

The source and restored pre-smoke all-public-table digests were both
`0ce107533e3706706f9e3759c166f512fb6451e1662f7e61b20042f4277d4ef0`.
The original database remained unchanged throughout the previous-image execution.
The restored database's final digest is
`1caa36c3bd5fe31156e09e1073bf69eefb75d958c26ede7c658ab3677694739c`;
it legitimately differs because the smoke logged in and completed a **new** Research Run.

The rollback-required summary passed **12/12 scoped logical checks**,
`single_failure_free_batch=true`, SHA-256
`5dca428c632645f2ecc75fcf25586da8589618531cc33e1f22553ea9a6f436ba`.
This closes the recovery engineering and local independent-environment acceptance work.

To repeat this scoped rehearsal against the retained c1 environment:

```powershell
uv run --locked python infra/recovery/exercise.py start --directory .data/isolated-recovery/iip-recovery-20260917-c1
uv run --locked python infra/recovery/rollback.py --directory .data/isolated-recovery/iip-recovery-20260917-c1 --previous-image sha256:e2cace07a9e8749f939094843f98d5f7f187b6fe3dc9e3a29821ccf403deef92
uv run --locked python infra/recovery/exercise.py summarize --directory .data/isolated-recovery/iip-recovery-20260917-c1 --require-rollback
uv run --locked python infra/recovery/exercise.py collect --directory .data/isolated-recovery/iip-recovery-20260917-c1
uv run --locked python infra/recovery/exercise.py stop --directory .data/isolated-recovery/iip-recovery-20260917-c1
```

The optional rollback-required summary is explicitly scoped to the eleven container
checks plus actual image rollback. It does not replace or mark passed the different
frozen twelve-scenario release gate. Overall release approval remains separate.

Closeout: only c1 containers were stopped; its volumes, restored database and evidence
were retained. Shared development containers and `.env` were untouched. Harness
guardrails and recovery/report regression: 45 tests passed; Ruff and mypy passed.
These final harness/documentation changes are local and not committed or pushed.
