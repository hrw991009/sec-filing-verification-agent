# Day 2 Agent Runtime 运行手册

> 适用范围：Day 2 的 Direct Answer L0、Conversation、附件、Job/Outbox、SSE、Trace 与前端工作台。
>
> 不适用范围：Day 3 Tool loop、Day 4 Memory/Evidence、Day 5 LangGraph resume/Knowledge、Day 6 RAG。

## 1. 日常启动

在仓库根目录执行。基础设施只需在容器未运行时启动；Alembic 只在首次建库或代码新增迁移后执行，不需要每次启动都执行。

```powershell
docker compose --env-file '.env' -f infra/compose/compose.yaml up -d --wait postgres redis minio
docker compose --env-file '.env' -f infra/compose/compose.yaml run --rm --no-deps minio-init

uv run --env-file '.env' --locked --package industry-platform-backend alembic -c apps/backend/alembic.ini upgrade head
uv run --env-file '.env' --locked industry-platform-backend-dev
```

另开一个终端启动 Web：

```powershell
pnpm run dev:web
```

浏览器使用 `https://localhost:5173`。开发证书由本地 Vite 生成，首次访问出现浏览器的自签名证书提示是开发环境现象；不要把这套证书用于生产。

## 2. 启动后检查

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health/live
Invoke-RestMethod http://127.0.0.1:8000/health/ready

docker compose --env-file '.env' -f infra/compose/compose.yaml ps -a postgres redis minio minio-init
```

预期结果：

- `live` 返回 `status=ok`；
- `ready` 返回 200，PostgreSQL 与 Redis 均通过；
- PostgreSQL、Redis、MinIO 为 `healthy`；
- `minio-init` 是一次性初始化任务，`Exited (0)` 表示成功，不需要常驻。

统一后端命令会启动 API、Outbox Dispatcher、Celery Worker、Job Reconciler 和 Celery Beat。Windows 下 Worker 默认使用 `solo`、并发 1；Linux/macOS 保留 Celery 的正式进程池。按一次 `Ctrl+C` 让统一启动器回收整组进程。

## 3. 常见故障

### Run 一直停在 queued

先检查 Worker 启动日志是否同时显示 `default` 与 `agents` 队列，再检查 Redis 和 Dispatcher：

```powershell
docker compose --env-file '.env' -f infra/compose/compose.yaml ps redis
uv run --env-file '.env' --locked --package industry-platform-backend python -c "from industry_platform.core.config import get_settings; from industry_platform.workers.celery_app import create_worker_celery_app; app=create_worker_celery_app(get_settings()); print(app.control.inspect(timeout=2).active_queues()); app.close()"
```

不要直接修改数据库状态，也不要把 `agents` 消息手工搬到另一个队列。正式修复路径是恢复 Redis、Dispatcher 或 Worker，让 PostgreSQL Job/Outbox 对账继续推进。

### 点击停止后暂时没有终态

取消接口返回 202 只表示取消请求已持久化，不代表已经完成。前端继续读取 SSE，并用只读 Trace 核对最终事实。排队中的 Run 会与 Job 在同一事务中收敛为 `cancelled`；运行中的 Run 由 Runtime 在安全点关闭 Provider stream 并提交唯一终态。

如果 UI 显示“停止请求已提交，但尚未确认终态”，可以再次点击停止；请求是幂等的。不要把浏览器断开当作取消，也不要在前端伪造 `cancelled`。

### Provider 未配置

这是允许的显式状态，不会回退到 Fake。Run 应以稳定错误 `provider_not_configured` 和 `provider_error` stop reason 结束，用户消息、Trace 和已提交事件仍保留。

要启用真实 Provider，必须同时配置：

- `AGENT_MODEL_PROVIDER_BASE_URL`；
- `AGENT_MODEL_PROVIDER_API_KEY`；
- `AGENT_MODEL_ROUTE_JSON`。

三项全部不配置是允许的，此时正式 Run 以 `provider_not_configured` 结束；一旦配置其中任意一项，三项就必须完整，否则 Settings 启动校验会拒绝这组半配置。不要把参考仓历史凭据复制到本项目。

### SSE 断线或游标错误

浏览器重连只读取 PostgreSQL 已提交事件，不会再次执行模型步骤。`Last-Event-ID` 必须是当前 Run 内的非负十进制 sequence。游标超前或格式错误返回稳定 Problem；游标早于当前 256 条回放窗口时，服务端优先发送与最新已提交 sequence 对齐的权威 snapshot，只有无法构造一致 snapshot 时才返回 reset-required。客户端不能猜测缺失 delta。

### 附件不可用

确认 MinIO 为 healthy、`minio-init` 为 `Exited (0)`，且 `.env` 中四项 MinIO 配置完整。`staging` 只表示浏览器上传完成前的意图；只有经过服务端实际大小、hash、MIME/magic bytes 和解析检查后进入 `ready` 的文件才能关联消息和进入 Context。

## 4. 数据备份与恢复

PostgreSQL 是 Conversation、Run、Step、Event、Job、Outbox、manifest 和附件元数据的唯一业务事实源；MinIO 保存私有文件字节。两者必须作为同一个恢复点管理。

变更迁移或做破坏性演练前，先生成仓库外的独立备份目录：

```powershell
$backupRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("industry-platform-day2-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
New-Item -ItemType Directory -Force $backupRoot | Out-Null

docker compose --env-file '.env' -f infra/compose/compose.yaml exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > (Join-Path $backupRoot 'postgres.dump')

docker compose --env-file '.env' -f infra/compose/compose.yaml run --rm --no-deps --entrypoint /bin/sh -v "${backupRoot}:/backup" minio-init -c 'set -eu; mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"; mc mirror --overwrite "local/$MINIO_BUCKET" /backup/minio'
```

正式环境应把 `$backupRoot` 替换为组织批准的加密备份目标，并设置保留期和访问控制。恢复必须先在隔离环境执行，再核对数据库 migration head、对象 hash、匿名访问拒绝和一条附件旅程，不能直接覆盖正在运行的生产数据。这里记录的是 Day 2 的备份/恢复策略；在隔离环境真正完成一次恢复演练并保存证据前，不得把它描述成“已演练”。

## 5. 回滚

1. 停止统一后端和 Web，保留 PostgreSQL、Redis、MinIO 数据卷；
2. 保存当前日志、Run ID、Job ID、Trace ID、migration head 和失败时间；
3. 回退到上一份已通过 CI 的应用提交；
4. 默认不执行 `alembic downgrade`。只有迁移文件明确证明 downgrade 不丢数据，并已在备份副本演练后才可执行；
5. 启动旧版本，检查 `live/ready`、Job/Outbox backlog、唯一 Run 终态和附件读取；
6. 若 Redis 数据丢失，以 PostgreSQL Job/Outbox 对账重投，不从 Redis 猜测业务状态。

Day 2 不承诺进程中断后继续同一次模型调用；不可安全恢复的执行会收敛为明确失败终态。Checkpoint envelope 与 CAS 已冻结，真正的 LangGraph state resume 留到 Day 5。

## 6. 可靠性 Scenario

Provider 回放场景在 `evals/scenarios/day2-v2.json`；预算、Worker 中断和重复请求在 `evals/scenarios/day2-reliability-v1.json`。后一个文件只登记版本、输入故障、预期事实、Scorer 和实际 pytest 引用，不是另一套 Runtime，也不能单独宣称测试通过。

先校验两个场景集和报告合同：

```powershell
uv run --locked --package industry-platform-backend industry-platform-agent-harness validate --dataset evals/scenarios/day2-v2.json
uv run --locked --all-packages pytest apps/backend/tests/modules/agent_harness/test_day2_replay.py -q
```

随后执行数据集引用的正式边界。PostgreSQL 场景必须显式打开真实依赖门禁；不要把 skip 当作通过：

```powershell
uv run --locked --all-packages pytest apps/backend/tests/modules/agent_runtime/test_runtime.py -q

$env:POSTGRES_TESTS_REQUIRED='1'
uv run --env-file '.env' --locked --all-packages pytest apps/backend/tests/integration/test_agent_fake_success_postgres.py apps/backend/tests/integration/test_agent_run_reliability_postgres.py apps/backend/tests/integration/test_conversation_agent_postgres.py -q
Remove-Item Env:POSTGRES_TESTS_REQUIRED
```

Tool 失败在 Day 2 L0 阶段性不适用，因为本日没有 Tool Action/Observation；它从 Day 3 L1/L2 起必须有正式 Scenario。Day 2 的不可恢复中断必须测试并收敛为一个 failed 终态；只有 LangGraph durable resume 按主计划留到 Day 5。

## 7. 验收命令

完整命令和环境开关见根 [README 的统一验证](../../README.md#统一验证)，测试范围和 Day 2 证据说明见 [Agent Runtime v0](../agent-runtime.md)。任何失败不得通过重跑掩盖；先定位到 Runtime、Provider、Job/lease、SSE、PostgreSQL 或 MinIO 的哪一层，再修复对应事实源。
