# Industry Intelligence Platform

面向行业研究与企业知识工作的多模态行业智能工作台。

当前状态：Day 1 的新仓工程执行门禁以及 Day 2/3 Agent 门禁已经通过，D1-01～D1-08、D1-10～D1-12、D2-01～D2-09 与 D3-01～D3-11 均为 `complete`。Day 4 的五个步骤已完成仓库内实现和统一的本地验收，D4-01～D4-07 均为 `implemented_pending_verification`；本次没有 commit/push 或新的干净 GitHub CI，项目所有者最终复盘也尚未记录，因此不得提前标为 `complete` 或进入 Day 5。参考仓的 6 组凭据候选仍为 `open`，D1-09 保持 `thin_slice`；该外部治理尾项不阻断 Day 4 Agent 学习，但在 Provider 侧吊销/轮换并复扫前不得复制或启用相关配置，也不得打 Day 7 发布标签。

## 文档入口

- [七天主计划 v1.7.0（当前权威执行基线）](docs/master-plan.md)
- [产品范围说明](docs/product-scope.md)
- [七天目标能力矩阵](docs/feature-matrix.md)
- [系统架构说明与 ADR 索引](docs/architecture.md)
- [Day 1 学习日志](docs/learning-log/day-1.md)
- [Day 2 Agent Runtime v0](docs/agent-runtime.md)
- [Day 2 学习日志](docs/learning-log/day-2.md)
- [Day 2 运行与故障手册](docs/runbooks/day-2-agent-runtime.md)
- [Day 2 第三方依赖与使用边界复核](docs/security/day-2-third-party-review.md)
- [Day 3 Agent Harness v1：L1/L2 与行业采集切片](docs/agent-harness.md)
- [Day 3 学习日志](docs/learning-log/day-3.md)
- [Day 3 真实来源、使用边界与安全复核](docs/security/day-3-source-review.md)
- [Day 3 Text2SQL 安全复核](docs/security/day-3-text2sql-review.md)
- [Day 3 前端、Tool Inspector 与 ECharts 安全复核](docs/security/day-3-ui-review.md)
- [Day 3 Agent Tool 运行与回滚手册](docs/runbooks/day-3-agent-tools.md)
- [Day 4 五步执行计划](docs/learning-log/day-4.md)
- [Day 4 Memory 策略](docs/memory-policy.md)
- [Day 4 Evidence/Claim 策略](docs/evidence-policy.md)
- [Day 4 Research L3 状态机](docs/research-state-machine.md)
- [Day 4 安全与隐私复核](docs/security/day-4-memory-research-review.md)
- [Day 4 运行与回滚手册](docs/runbooks/day-4-memory-research.md)
- [参考仓凭据暴露审计](docs/security/credential-exposure-audit.md)

## 已实现的 Day 1 范围

- FastAPI、Pydantic Settings、真实 `/health/live` 与 `/health/ready`；
- PostgreSQL、Redis、私有 MinIO 默认 Compose，以及 tools、vector、search、observability profiles；
- Alembic 身份、Workspace、Job、Outbox、Schedule 与 ScheduleOccurrence 迁移；
- 注册、登录、`me`、修改密码、Logout、Ed25519 Access Token、Refresh/CSRF 轮换与恢复、登录限流；
- owner/admin/member/viewer 服务端权限矩阵、跨 Workspace 拒绝与最后 owner 保护；
- React 登录/注册/受保护首页/修改密码旅程，Access Token 只保存在内存；
- FastAPI OpenAPI 生成 TypeScript 契约与统一 Web API Client；
- PostgreSQL Job/JobEvent/Outbox、Dispatcher、Celery Worker、lease/heartbeat/fencing、Reconciler，以及数据库驱动的 Schedule/Beat；
- Python、Web、PostgreSQL/Redis 集成、浏览器 E2E、依赖审计、Gitleaks 与 GitHub Actions 门禁。

Day 2 的 Agent Runtime/Harness、L0 聊天、附件、可恢复 SSE、Learning Workbench、故障收敛和版本化 Eval 已经完成仓库内实现，并通过全量本地门禁、提交 [`bf4feaff`](https://github.com/hrw991009/industry-intelligence-platform/commit/bf4feaff2e0fa5487a6f01ed0fd4cd63f5b4f659) 的 [干净 CI](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/31922391846) 与学习者职责复盘；D2-01～D2-09 均为 `complete`。Day 3 的五个切片已经完成仓库内实现与全量本地验收：同一 `UnifiedAgentRuntime` 执行 L0/L1/L2，生产 Conversation/Job 可物化行业限定的 Web L2 command；Tool Inspector、行业页、数据库浏览、安全 Text2SQL、受校验表格/图表、陈旧 QueryRun 对账、24 条累计 Scenario 和 trajectory report 均已落地。真实 PostgreSQL/Redis/MinIO、4 条浏览器旅程、依赖/许可证/来源/隐私与 Secret 门禁均通过；[PR #5](https://github.com/hrw991009/industry-intelligence-platform/pull/5) 已合并，合并提交 [`6968c63f`](https://github.com/hrw991009/industry-intelligence-platform/commit/6968c63f3330f3079e3e1cc2db0b29488d7502a2) 的 [干净 CI](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/32112639811) 全绿，D3-01～D3-11 已复核为 `complete`。Day 7 前仍须完成参考仓 D1-09 外部凭据处置，以及显式物理 Run purge 与隔离备份恢复演练。

Day 4 本地实现已串起 Conversation/Memory/Context manifest、Observation→Evidence→Claim、显式 ResearchBrief、唯一 typed Research L3 graph 与正式 Workbench。保留 Day 2/3 的 24 条基线，Day 4 新增 26 条独立 Scenario，累计 50 条；Memory、Memory off/on、Evidence 与 Research 使用独立规则 Scorer，并提供同题 L0/L2/L3 的步骤、Token、费用、延迟和 Evidence/Claim/uncertain 对照。当前本地门禁为 pytest 946、Vitest 75、Playwright 6，真实 PostgreSQL/Redis/MinIO 无 skip；后端总体覆盖率 82.12%，前端关键状态分支 100%。Day 4 核心 Domain/Application/Research workflow 合集为 85%，低于 90% 目标，具体原因、风险、CI 不退化缓解和复核人已记录在 Day 4 学习日志；它必须在 Day 7 总门禁前补齐，且当前仍需提交后的干净 CI 才能关闭 Day 4。

## 执行基线与安装

- Python 3.13.14
- Node.js 24.16.0
- uv 0.11.32
- pnpm 10.10.0

在仓库根目录执行：

```powershell
uv --version
node --version
pnpm --version
docker --version
docker compose version

uv sync --locked --all-packages
pnpm install --frozen-lockfile
pnpm exec playwright install chromium
uv run --locked python --version
```

`uv run --locked python --version` 应显示 `Python 3.13.14`。项目解释器由 uv 管理，不以 Conda 或系统 PATH 中的全局 `python` 为准。

## 配置本地环境

先复制模板；`.env` 已被 Git 忽略，不要提交它：

```powershell
Copy-Item -LiteralPath '.env.example' -Destination '.env'
git check-ignore -v -- '.env'
```

模板中的本地数据库密码也应改成只用于本机的随机值。下面的命令只把 5 个相互独立的 32 字节 base64url 密钥和一组 Ed25519 密钥打印到终端；请逐项复制到 `.env`，不要把输出提交、粘贴到日志或聊天中，也不要在不同用途之间复用密钥：

```powershell
@'
import json
import secrets
from base64 import urlsafe_b64encode

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

def encode(value: bytes) -> str:
    return urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

for name in (
    "REFRESH_TOKEN_HMAC_KEY_B64",
    "CSRF_TOKEN_HMAC_KEY_B64",
    "DEVICE_TOKEN_HMAC_KEY_B64",
    "LOGIN_RATE_LIMIT_HMAC_KEY_B64",
    "REFRESH_RECOVERY_AEAD_KEY_B64",
):
    print(f"{name}={encode(secrets.token_bytes(32))}")

private_key = Ed25519PrivateKey.generate()
private_value = private_key.private_bytes(
    Encoding.Raw,
    PrivateFormat.Raw,
    NoEncryption(),
)
public_value = private_key.public_key().public_bytes(
    Encoding.Raw,
    PublicFormat.Raw,
)
key_id = "local-development-1"
print(f"ACCESS_TOKEN_CURRENT_KID={key_id}")
print(f"ACCESS_TOKEN_PRIVATE_KEY_B64={encode(private_value)}")
print(
    "ACCESS_TOKEN_PUBLIC_KEYS_JSON="
    + json.dumps({key_id: encode(public_value)}, separators=(",", ":"))
)
'@ | uv run --locked --package industry-platform-backend python -
```

保留 `.env.example` 中的精确 HTTPS origins；若改变 Web host 或 port，应同步更新 `BROWSER_TRUSTED_ORIGINS_JSON`，不能改成通配来源。

## 启动基础设施与迁移

默认只启动 PostgreSQL、Redis 和 MinIO，并且端口只绑定 `127.0.0.1`：

```powershell
$composeFile = 'infra/compose/compose.yaml'
docker compose --env-file '.env' -f $composeFile config --quiet
docker compose --env-file '.env' -f $composeFile up -d --wait postgres redis minio
docker compose --env-file '.env' -f $composeFile run --rm --no-deps minio-init
docker compose --env-file '.env' -f $composeFile ps
```

`minio-init` 是一次性初始化任务：它创建私有附件桶并配置 `staging/` 清理规则，成功后显示 `Exited (0)` 属于正常完成，不是服务启动失败。命令可以重复执行，不会重复创建桶。

需要时按职责启用可选 profile：

```powershell
docker compose --env-file '.env' -f $composeFile --profile tools up -d --wait
docker compose --env-file '.env' -f $composeFile --profile vector up -d --wait
docker compose --env-file '.env' -f $composeFile --profile search up -d --wait
docker compose --env-file '.env' -f $composeFile --profile observability up -d --wait
```

创建或升级正式表结构只能使用 Alembic：

```powershell
uv run --env-file '.env' --locked --package industry-platform-backend alembic -c apps/backend/alembic.ini heads
uv run --env-file '.env' --locked --package industry-platform-backend alembic -c apps/backend/alembic.ini upgrade head
uv run --env-file '.env' --locked --package industry-platform-backend alembic -c apps/backend/alembic.ini current
```

正常停止使用 `docker compose --env-file '.env' -f $composeFile down`。不要随意加 `--volumes`，它会删除本地持久数据。

## 启动应用与后台进程

本地开发默认使用一个受控的 Python 进程管理器启动完整后端。它先检查 PostgreSQL、Redis、MinIO 私有桶和 Alembic 版本，然后分别启动 API、Outbox Dispatcher、Celery Worker、Job Reconciler 与 Celery Beat；这些仍是五个独立子进程，不会把生产职责合并到同一个 Runtime：

```powershell
uv run --locked industry-platform-backend-dev
```

请从仓库根目录执行该命令；后端 Settings 会自动读取根目录的 `.env`，因此日常启动不需要重复填写 `--env-file` 或 `--package`。如果依赖未启动，命令会直接给出 Compose 修复命令，而不是让 Worker 无限打印连接重试。如果数据库没有到最新 Alembic head，命令只提示正式迁移命令，不会在每次启动时静默修改数据库。Windows 本地 Worker 默认使用 `solo`、单并发；Linux 的独立 Worker 仍保留 Celery 默认进程池。按 `Ctrl+C` 会统一停止这一组开发进程。

需要单独排障或模拟生产进程边界时，仍可让每条长运行命令各占一个 PowerShell 终端：

```powershell
uv run --env-file '.env' --locked --package industry-platform-backend industry-platform-api
```

```powershell
uv run --env-file '.env' --locked --package industry-platform-backend industry-platform-outbox-dispatcher
```

```powershell
uv run --env-file '.env' --locked --package industry-platform-backend industry-platform-celery-worker
```

```powershell
uv run --env-file '.env' --locked --package industry-platform-backend industry-platform-job-reconciler
```

```powershell
uv run --env-file '.env' --locked --package industry-platform-backend industry-platform-celery-beat
```

Beat 只通过 PostgreSQL 创建持久 ScheduleOccurrence、Job 和 Outbox；真正发布由 Dispatcher 完成，Worker 执行任务，Reconciler 修复未启动或 lease 过期的 Job。

启动 Web：

```powershell
pnpm run dev:web
```

浏览器打开 `https://localhost:5173`。本地证书由 Vite 生成，浏览器首次访问会提示确认自签名证书。Web 将 `/api` 代理到 `http://127.0.0.1:8000`。

API 基本检查：

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/health/live'
Invoke-RestMethod 'http://127.0.0.1:8000/health/ready'
```

交互式 API 文档位于 `http://127.0.0.1:8000/docs`。

## OpenAPI 契约

OpenAPI 是后端与前端的唯一 DTO 来源。生成物是 `packages/api-contract/openapi.json` 和 `packages/api-contract/src/schema.d.ts`：

```powershell
pnpm run api:generate
pnpm run api:check
```

修改 FastAPI schema 后先运行 `api:generate` 并评审生成 diff；CI 使用 `api:check` 阻止生成物漂移。

## 统一验证

先保持 PostgreSQL、Redis 和 MinIO 运行。以下是一组完整的本地收口命令；不要只挑绿色的子集代替完整验证：

```powershell
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$env:POSTGRES_TESTS_REQUIRED = '1'
$env:REDIS_TESTS_REQUIRED = '1'
$env:MINIO_TESTS_REQUIRED = '1'

try {
    uv sync --locked --all-packages
    uv run --locked --all-packages ruff format --check --config pyproject.toml apps/backend
    uv run --locked --all-packages ruff check --config pyproject.toml apps/backend
    uv run --locked --all-packages mypy --config-file pyproject.toml --no-incremental
    uv run --env-file '.env' --locked --all-packages pytest --cov=industry_platform --cov-branch --cov-report=term --cov-fail-under=80
    uv build --package industry-platform-backend
    uv audit --locked

    pnpm install --frozen-lockfile
    pnpm run api:check
    pnpm run format:check
    pnpm run lint
    pnpm run typecheck
    pnpm run test
    pnpm run test:coverage:web
    pnpm run build
    pnpm audit --audit-level high
    pnpm run test:e2e

    gitleaks dir --redact --verbose apps
    gitleaks dir --redact --verbose packages
    gitleaks dir --redact --verbose docs
    gitleaks dir --redact --verbose evals
    gitleaks dir --redact --verbose .github
    gitleaks dir --redact --verbose infra
    gitleaks dir --redact --verbose tests
    gitleaks dir --redact --verbose .env.example
    gitleaks dir --redact --verbose package.json
    gitleaks dir --redact --verbose pnpm-workspace.yaml
    gitleaks dir --redact --verbose pyproject.toml
    gitleaks dir --redact --verbose playwright.config.ts
    gitleaks git --redact --verbose --log-opts='--all' .
    git diff --check
    git diff --cached --check
    git status --short
}
finally {
    Remove-Item Env:POSTGRES_TESTS_REQUIRED -ErrorAction SilentlyContinue
    Remove-Item Env:REDIS_TESTS_REQUIRED -ErrorAction SilentlyContinue
    Remove-Item Env:MINIO_TESTS_REQUIRED -ErrorAction SilentlyContinue
}
```

上述命令仍是后续变更必须重复执行的统一验证方法。Day 1 当前基线已在本地完整执行，并由提交 [`2c4e6e9`](https://github.com/hrw991009/industry-intelligence-platform/commit/2c4e6e92237584bbac2816577e1509286f08b14b) 的 [CI 31578083339](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/31578083339) 在干净环境通过；D1-01～D1-08、D1-10～D1-12 已按能力矩阵复核为 `complete`。Day 2 的本地证据见 [Agent Runtime v0](docs/agent-runtime.md) 和 [Day 2 学习日志](docs/learning-log/day-2.md)，提交 [`bf4feaff`](https://github.com/hrw991009/industry-intelligence-platform/commit/bf4feaff2e0fa5487a6f01ed0fd4cd63f5b4f659) 的 [CI 31922391846](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/31922391846) 已在干净环境通过，D2-01～D2-09 已复核为 `complete`。

Day 3 已实际执行同一套统一门禁：Python 898、Vitest 54、Playwright 4 条均通过，真实 PostgreSQL/Redis/MinIO 无 skip，migration 往返、OpenAPI `api:check`、Python/Web build 与 audit、受控路径和 44-commit Gitleaks 也通过。证据和限制见 [Day 3 学习日志](docs/learning-log/day-3.md)。[PR #5](https://github.com/hrw991009/industry-intelligence-platform/pull/5) 的 head 已合入 `main`，合并提交 [`6968c63f`](https://github.com/hrw991009/industry-intelligence-platform/commit/6968c63f3330f3079e3e1cc2db0b29488d7502a2) 对应的 [CI 32112639811](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/32112639811) 在干净环境通过全部 7 个适用 Job；D3-01～D3-11 已复核为 `complete`，可以进入 Day 4。

D1-09 仍为 `thin_slice`，6 组参考仓凭据候选全部保持 `open`。该外部治理尾项不阻断 Day 2/Day 3 Agent 学习，但在 Provider 侧吊销/轮换、登记非敏感证据并完成复扫前，不得复制或启用相关配置，也不得创建 Day 7 发布标签。后续开发以 [七天主计划 v1.7.0](docs/master-plan.md) 为权威执行基线。

## 常见问题

- uv 不要求激活虚拟环境；若 Conda 干扰 PATH，可先退出 Conda，再直接运行 `uv ...`。
- Playwright 缺 Chromium 时运行 `pnpm exec playwright install chromium`。
- 分路径运行的 `gitleaks dir` 检查当前受控源码与配置，`gitleaks git --log-opts='--all'` 检查完整历史，两者不能互相替代。不要对仓库根目录直接运行 `gitleaks dir ... .`，因为它会扫描被 Git 忽略且本来就应包含本地密钥的真实 `.env`，从而产生无意义告警并把敏感文件带入扫描输出。
- 不要用 `git reset --hard` 或删除整个工作区处理未知改动；先用 `git status --short` 和 `git diff` 确认归属。
