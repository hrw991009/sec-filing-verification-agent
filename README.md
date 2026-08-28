# SEC Disclosure Financial Verification Agent

面向中文研究、企业战略、IR、财务和咨询团队的 SEC 公开披露监控与财务事实核验工作台。

当前状态：Day 1～Day 4 已完成；Day 5 五步已由 [PR #9](https://github.com/hrw991009/industry-intelligence-platform/pull/9) 合入提交 [`a38d0ae`](https://github.com/hrw991009/industry-intelligence-platform/commit/a38d0aee101b66d9c6601a01b426ffd1ec0dcb34)，分支 push CI [`32920879147`](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/32920879147)、PR CI [`32924323618`](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/32924323618) 和 main CI [`32924732755`](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/32924732755) 均通过。D5-01～D5-07 为 `complete`；D5-08/D5-09 因缺 ready SEC fixture 的 Dense/calculation Evidence 与暂停/审批/resume/刷新浏览器全链，保持 `implemented_pending_verification`。

Day 6 已由 [PR #10](https://github.com/hrw991009/industry-intelligence-platform/pull/10) 合入 `main`，功能 head [`7a4766b`](https://github.com/hrw991009/industry-intelligence-platform/commit/7a4766b6d4c4ad764b9e095b2d0f03d8ec96c143) 的 push CI [`33053621106`](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/33053621106)、PR CI [`33053623731`](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/33053623731) 和合并提交 [`84a7945`](https://github.com/hrw991009/industry-intelligence-platform/commit/84a7945ed769d63974602b5c20984e2f4ebf0e93) 的 main CI [`33054136204`](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/33054136204) 均通过。`sec-source-v1` 仍为 `22/24`，D6-02/D6-06 的 bulk snapshot/watermark/post-gap 与 live SEC 债务没有关闭；项目所有者随后明确开始 Day 7 Step 1，因此这些未完成项改为 Day 10 发布前硬门，不从原评测分母删除。Day 7 Step 1 已实现 `hybrid-v1`、真实 PostgreSQL/Milvus/Elasticsearch 重载链和 filing text/XBRL fact Evidence locator；本地统一 Python 套件为 `1121 passed`，总体分支覆盖率 `80.62%`、Memory/Evidence/Research 核心合集 `86%`。D7-01 为 `implemented_pending_verification`，D7-02 因 table/cell locator 与 Citation 可解析率评测未关闭保持 `thin_slice`。D5-08/D5-09 浏览器 DoD、D1-09 的 6 组参考仓凭据候选和 Day 4 核心合集 90% 覆盖率债务继续保留。

## 文档入口

- [Day 1～Day 10 主计划 v2.1.1（当前权威执行基线）](docs/master-plan.md)
- [产品范围说明](docs/product-scope.md)
- [Day 1～Day 10 目标能力矩阵](docs/feature-matrix.md)
- [系统架构说明与 ADR 索引](docs/architecture.md)
- [ADR 0007：SEC 披露财务事实核验边界](docs/adr/0007-sec-disclosure-financial-fact-verification.md)
- [SEC Agent 评测计划](docs/sec-agent-evaluation.md)
- [SEC Filing Retrieval 与财务计算设计](docs/sec-retrieval-design.md)
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
- [Day 5 Knowledge 与 SEC Fixture L4 执行日志](docs/learning-log/day-5.md)
- [Day 6 SEC 官方披露与 Point-in-Time 五步执行计划](docs/learning-log/day-6.md)
- [Day 7 Filing Hybrid Retrieval、财务计算与核对五步计划](docs/learning-log/day-7.md)
- [Research L4 Checkpoint 与 HITL 合同](docs/research-checkpoint-contract.md)
- [Day 5 Research L4 运行与回滚手册](docs/runbooks/day-5-research-l4.md)
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

Day 2 的 Agent Runtime/Harness、L0 聊天、附件、可恢复 SSE、Learning Workbench、故障收敛和版本化 Eval 已经完成仓库内实现，并通过全量本地门禁、提交 [`bf4feaff`](https://github.com/hrw991009/industry-intelligence-platform/commit/bf4feaff2e0fa5487a6f01ed0fd4cd63f5b4f659) 的 [干净 CI](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/31922391846) 与学习者职责复盘；D2-01～D2-09 均为 `complete`。Day 3 的五个切片已经完成仓库内实现与全量本地验收：同一 `UnifiedAgentRuntime` 执行 L0/L1/L2，生产 Conversation/Job 可物化行业限定的 Web L2 command；Tool Inspector、行业页、数据库浏览、安全 Text2SQL、受校验表格/图表、陈旧 QueryRun 对账、24 条累计 Scenario 和 trajectory report 均已落地。真实 PostgreSQL/Redis/MinIO、4 条浏览器旅程、依赖/许可证/来源/隐私与 Secret 门禁均通过；[PR #5](https://github.com/hrw991009/industry-intelligence-platform/pull/5) 已合并，合并提交 [`6968c63f`](https://github.com/hrw991009/industry-intelligence-platform/commit/6968c63f3330f3079e3e1cc2db0b29488d7502a2) 的 [干净 CI](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/32112639811) 全绿，D3-01～D3-11 已复核为 `complete`。Day 10 前仍须完成参考仓 D1-09 外部凭据处置，以及显式物理 Run purge 与隔离备份恢复演练。

Day 4 实现已串起 Conversation/Memory/Context manifest、Observation→Evidence→Claim、显式 ResearchBrief、唯一 typed Research L3 graph 与正式 Workbench。保留 Day 2/3 的 24 条基线，Day 4 新增 26 条独立 Scenario，累计 50 条；Memory、Memory off/on、Evidence 与 Research 使用独立规则 Scorer，并提供同题 L0/L2/L3 的步骤、Token、费用、延迟和 Evidence/Claim/uncertain 对照。本地门禁为 pytest 946、Vitest 75、Playwright 6，真实 PostgreSQL/Redis/MinIO 无 skip；后端总体覆盖率 82.12%，前端关键状态分支 100%。分支 CI 已再次验证 Python/Web 质量、真实 PostgreSQL/Redis/MinIO、Browser E2E、依赖审计和完整历史 Secret 扫描。Day 4 核心 Domain/Application/Research workflow 合集为 85%，低于 90% 目标，具体原因、风险、CI 85% 不退化缓解和复核人已记录在 Day 4 学习日志；它是必须在 Day 10 总门禁前清偿的已登记例外，不因本次分支 CI 通过而消失。

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

如果关闭终端后 Vite 进程仍在占用 `5173` 端口，可在 PowerShell 中按监听端口查找并停止残留进程：

```powershell
Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { Stop-Process -Id $_ }
```

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

Day 4 的五个实现步骤和收口文档已通过功能分支 CI；[PR #7](https://github.com/hrw991009/industry-intelligence-platform/pull/7) 随后合入 `main`。合并提交 [`c0b854e`](https://github.com/hrw991009/industry-intelligence-platform/commit/c0b854e64ef1966b76cdcc38c41a507959c836cb) 对应的 [CI 32549438592](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/32549438592) 已通过 Browser E2E、Python quality、PostgreSQL integration、Web quality、Python/Node dependency audit 和 Secret history 共 7 个适用 Job。正式 Trace、50 条累计 Scenario、四套独立 Scorer、真实浏览器旅程、DoD 与项目所有者授权收口均已复核，D4-01～D4-07 已为 `complete`，可以进入 Day 5。

D1-09 仍为 `thin_slice`，6 组参考仓凭据候选全部保持 `open`。该外部治理尾项不阻断 Day 2～Day 9 Agent 学习，但在 Provider 侧吊销/轮换、登记非敏感证据并完成复扫前，不得复制或启用相关配置，也不得创建 Day 10 发布标签。后续开发以 [Day 1～Day 10 主计划 v2.0.6](docs/master-plan.md) 为权威执行基线。

## 常见问题

- uv 不要求激活虚拟环境；若 Conda 干扰 PATH，可先退出 Conda，再直接运行 `uv ...`。
- Playwright 缺 Chromium 时运行 `pnpm exec playwright install chromium`。
- 分路径运行的 `gitleaks dir` 检查当前受控源码与配置，`gitleaks git --log-opts='--all'` 检查完整历史，两者不能互相替代。不要对仓库根目录直接运行 `gitleaks dir ... .`，因为它会扫描被 Git 忽略且本来就应包含本地密钥的真实 `.env`，从而产生无意义告警并把敏感文件带入扫描输出。
- 不要用 `git reset --hard` 或删除整个工作区处理未知改动；先用 `git status --short` 和 `git diff` 确认归属。
