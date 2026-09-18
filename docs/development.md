# 本地开发与质量检查

Docker 完整应用部署见[部署指南](deployment.md)。本页适合需要修改代码、使用热更新和调试器的开发者。



以下步骤以 Windows PowerShell 为准。GitHub Actions 使用 Linux 验证构建和测试，但项目没有单独的
“Linux 版”。

### 1. 环境要求

- Git
- Docker Desktop，支持 `docker compose`
- [uv](https://docs.astral.sh/uv/)
- Node.js `24.16.0`
- pnpm `10.10.0`

Python 版本由仓库中的 `.python-version` 固定为 `3.13.14`。

克隆仓库后，在仓库根目录执行：

```powershell
uv sync --locked --all-packages
pnpm install --frozen-lockfile
pnpm exec playwright install chromium
```

Chromium 只在运行浏览器测试时需要；仅启动应用可以跳过最后一条命令。

### 2. 创建本地配置

复制环境变量模板：

```powershell
Copy-Item -LiteralPath '.env.example' -Destination '.env'
git check-ignore -v -- '.env'
```

`.env` 中的 PostgreSQL、Redis 和 MinIO 密码只用于本地开发，也应替换为本机随机值。身份系统还要求
5 个相互独立的 HMAC/AEAD 密钥和一组 Ed25519 密钥。以下命令只将密钥打印到当前终端，不会写入文件：

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
private_value = private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
public_value = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
key_id = "local-development-1"
print(f"ACCESS_TOKEN_CURRENT_KID={key_id}")
print(f"ACCESS_TOKEN_PRIVATE_KEY_B64={encode(private_value)}")
print(
    "ACCESS_TOKEN_PUBLIC_KEYS_JSON="
    + json.dumps({key_id: encode(public_value)}, separators=(",", ":"))
)
'@ | uv run --locked --package sec-filing-verification-agent-backend python -
```

将输出逐项填写到 `.env` 的同名变量中。不要复用密钥，也不要提交、截图或粘贴真实密钥到 Issue、日志或聊天。

### 3. 配置 SEC 身份

SEC 公共数据接口不需要 API Key，但自动访问必须声明应用名称和真实可联系邮箱。在 `.env` 中填写：

```ini
SEC_USER_AGENT_APP=SecFilingVerificationAgent/0.1
SEC_USER_AGENT_EMAIL=your-monitored-email@example.com
SEC_REQUESTS_PER_SECOND=8
```

未配置这两项时，live SEC 请求会 fail closed；仓库内的受控 fixture 和确定性测试仍可运行。

### 4. 配置模型 Provider（可选）

需要运行真实 Agent 回答时，在 `.env` 中同时配置：

```ini
AGENT_MODEL_PROVIDER_BASE_URL=https://api.example.com/v1
AGENT_MODEL_PROVIDER_API_KEY=replace-with-real-provider-key
AGENT_MODEL_ROUTE_JSON='{"model":"openai-compatible/replace-model","upstream_model":"replace-model","response_models":["replace-model"],"pricing_version":"replace-pricing-v1","input_micro_usd_per_million":1,"cached_input_micro_usd_per_million":1,"output_micro_usd_per_million":1,"supports_image_input":false}'
```

请按实际 Provider 和模型能力填写 URL、模型名及价格。三项均不配置时，生产 Run 会明确返回
`provider_not_configured`，不会静默回退到 Fake Provider。

### 5. 启动依赖

完整 SEC 检索链路需要 PostgreSQL、Redis、MinIO、Milvus 和 Elasticsearch：

```powershell
$composeFile = 'infra/compose/compose.yaml'
docker compose --env-file '.env' -f $composeFile config --quiet
docker compose --env-file '.env' -f $composeFile --profile vector --profile search up -d --wait
docker compose --env-file '.env' -f $composeFile run --rm --no-deps minio-init
docker compose --env-file '.env' -f $composeFile ps
```

`minio-init` 是可重复执行的一次性桶初始化任务，显示 `Exited (0)` 表示成功。

仅开发身份、Workspace 和基础 Agent Runtime 时，可以只启动默认依赖：

```powershell
docker compose --env-file '.env' -f $composeFile up -d --wait postgres redis minio
docker compose --env-file '.env' -f $composeFile run --rm --no-deps minio-init
```

可选管理与观测界面：

```powershell
docker compose --env-file '.env' -f $composeFile --profile tools up -d --wait
docker compose --env-file '.env' -f $composeFile --profile observability up -d --wait
```

### 6. 执行数据库迁移

```powershell
uv run --env-file '.env' --locked --package sec-filing-verification-agent-backend alembic -c apps/backend/alembic.ini upgrade head
uv run --env-file '.env' --locked --package sec-filing-verification-agent-backend alembic -c apps/backend/alembic.ini current
```

### 7. 启动后端与 Web

终端 1 启动 API、Dispatcher、Worker、Reconciler 和 Beat：

```powershell
uv run --locked sec-filing-verification-agent-dev
```

终端 2 启动 Web：

```powershell
pnpm run dev:web
```

打开以下地址：

- Web：`https://localhost:5173`
- API 文档：`http://127.0.0.1:8000/docs`
- 存活检查：`http://127.0.0.1:8000/health/live`
- 依赖就绪检查：`http://127.0.0.1:8000/health/ready`

Vite 使用本地自签名证书，浏览器首次访问时需要确认。Web 会将 `/api` 代理到本地 API。

停止应用进程后，可停止 Compose 服务：

```powershell
docker compose --env-file '.env' -f $composeFile down
```

不要随意添加 `--volumes`，它会删除本地 PostgreSQL、MinIO 和索引数据。

## 首次使用

1. 打开 Web，创建本地账户；系统会自动创建默认 Workspace。
2. 进入“知识库”，创建用于保存 SEC filing 的 Knowledge Base。
3. 进入“SEC”，输入 CIK，选择 Form、报告期和截止时间后查询申报。
4. 选择 accession，执行“锁定并导入”，等待状态变为“可检索”。
5. 在 SEC 工作台检索原文、查看 XBRL facts，或将锁定范围交给 Research Workbench。
6. 查看 Verification Report、Claim、Evidence、Calculation 和 Citation 反查结果。
7. 当 Agent 请求创建 Monitor 时，由当前用户明确允许或拒绝；批准后可在后续新 filing 到达时查看 Case。

真实 Research 需要模型 Provider；live filing 查询需要 SEC 身份。缺少任一配置时，相关链路会显式失败，
不会伪造成功结果。

## 常用验证命令

不依赖外部服务的基础检查：

```powershell
uv run --locked --all-packages ruff format --check --config pyproject.toml apps/backend evals/generators
uv run --locked --all-packages ruff check --config pyproject.toml apps/backend evals/generators
uv run --locked --all-packages mypy --config-file pyproject.toml --no-incremental
uv run --locked --all-packages pytest -q
pnpm run format:check
pnpm run lint
pnpm run typecheck
pnpm run test
pnpm run build
```

验证 OpenAPI 生成物：

```powershell
pnpm run api:generate
pnpm run api:check
```

依赖服务运行后执行浏览器测试：

```powershell
pnpm run test:e2e
pnpm run test:e2e:sec-real
```

`test:e2e:sec-real` 使用受控衍生 SEC fixture、真实本地 HTTP 进程以及 PostgreSQL、Redis、MinIO、
Milvus 和 Elasticsearch，不代表 live SEC 或真实模型质量。

尚未准备上一版本回滚镜像时，可以先执行核心真实链路验证。它覆盖真实浏览器、50 个生产
Runtime Run、Evidence 和 live SEC，但明确不包含恢复演练或最终发布判定：

```powershell
pnpm run acceptance:sec:core:preflight
pnpm run acceptance:sec:core
```

正式发布验收保持 fail closed，额外要求已批准的不可变回滚镜像：

```powershell
pnpm run acceptance:sec:preflight
pnpm run acceptance:sec
```

完整测试矩阵、恢复演练与环境要求见 [SEC 最终工程验收 Runbook](runbooks/sec-release-acceptance.md)。
