# SEC release acceptance runbook

> 适用范围：SEC 披露核验候选的最终工程取证，不等同于发布批准。  
> 写入边界：动态证据只写入 Git 忽略的 `.data/evals` 与 `test-results`。  
> 安全边界：流程不会删除 Compose volume，也不会改写 checked gold/observation/report。

## 1. 一次性外部配置

在仓库根目录的 `.env` 配置可用的 OpenAI-compatible Provider：

```ini
AGENT_MODEL_PROVIDER_BASE_URL=https://你的-provider/v1
AGENT_MODEL_PROVIDER_API_KEY=你的密钥
AGENT_MODEL_ROUTE_JSON={"model":"openai-compatible/你的模型","upstream_model":"你的模型","response_models":["你的模型"],"pricing_version":"你的价格版本","input_micro_usd_per_million":实际值,"cached_input_micro_usd_per_million":实际值,"output_micro_usd_per_million":实际值,"supports_image_input":false}
```

SEC EDGAR 不需要 API key，但必须保留已配置的应用身份：

```ini
SEC_USER_AGENT_APP=SecFilingVerificationAgent/0.1
SEC_USER_AGENT_EMAIL=有人查看的真实邮箱
SEC_REQUESTS_PER_SECOND=8
```

在当前 PowerShell 会话提供最后一个已批准镜像的不可变 digest：

```powershell
$env:PREVIOUS_IMAGE_DIGEST = '你的-registry/sec-filing-verification-agent@sha256:64位摘要'
```

本机还需安装并启动 Docker Desktop，且 `git`、`pnpm`、`uv` 可用。验收必须在已提交的干净工作树执行；真实 Secret 和动态证据不得提交。

## 2. 预检

尚无上一版本镜像时，只验证核心真实链路前置条件：

```powershell
pnpm run acceptance:sec:core:preflight
```

该命令只检查核心链路所需的外部配置、清单、工具与 SEC 身份，因此可在开发工作区存在未提交改动时运行；
报告会提示必须在执行核心链路前提交改动。该模式不要求 `PREVIOUS_IMAGE_DIGEST`，但会明确将恢复演练和最终发布判定记为 `skipped`，不能据此
声明工程发布证据完整。准备好回滚镜像后，执行正式发布预检：

```powershell
pnpm run acceptance:sec:preflight
```

预检会校验 `.env`、冻结 manifest/source hash、Provider 配置、SEC identity、不可变回滚镜像格式、工具链和干净 source commit。失败时其余阶段保持 `blocked`/`skipped`，不会生成完成声明。

## 3. 核心链路与正式验收

核心真实链路固定执行 10 case × A0-A4、每格一次，共 50 个生产 Runtime Run：

```powershell
pnpm run acceptance:sec:core
```

它自动完成依赖启动、受控数据准备、中文浏览器链、Run/Evidence 取证和 live SEC smoke，跳过 12 项
恢复演练与最终 readiness。动态证据单独写入 `.data/evals/sec-core-validation-v1`。

准备好不可变回滚镜像后，执行完整工程验收：

固定 10 case × A0-A4、每格一次，共 50 个生产 Runtime Run：

```powershell
pnpm run acceptance:sec
```

需要 live 三次重复时执行 10 case × A0-A4 × 3，共 150 个 Run：

```powershell
pnpm run acceptance:sec:live
```

命令自动完成：

1. 用仓库 Compose 配置启动并等待 PostgreSQL、Redis、MinIO、Milvus、Elasticsearch；
2. 执行 migration、受控两期 SEC 数据入库，以及无 API interception 的中文浏览器链；
3. 通过正式 submission/Worker/Runtime 执行 A0-A4 Run；
4. 从 PostgreSQL 的 Run、Trace、Tool Observation、最终 Draft、Evidence 与 Calculation 自动生成 ranked candidates、Citation 和 runtime binding；
5. 计算 case accuracy、Recall@5、Citation resolvability、拒答、运行绑定和隔离/时序安全指标；
6. 从实际 Run 自动绑定并执行 12 个隔离恢复场景，保存脱敏日志和前后状态 hash；
7. 执行带联系身份的 live SEC smoke，并生成最终 readiness report。

流程会保持 Compose 服务处于健康状态，便于查看和重跑；不会执行 `down --volumes`。

## 4. 失败定位

总报告：

```text
.data/evals/sec-release-acceptance-v1/acceptance-report.json
```

七个阶段按顺序为 `preflight`、`browser-data-preparation`、`release-run-execution`、`release-run-evidence`、`recovery-exercises`、`live-sec-identity`、`final-readiness`。只有全部为 `passed` 时 `engineering_evidence_complete` 才能为 `true`。

主要动态证据：

```text
test-results/sec-real-runtime/runtime-manifest.json
test-results/sec-real-runtime/playwright/
.data/evals/sec-release-acceptance-v1/sec-release-execution-v1.json
.data/evals/sec-release-acceptance-v1/sec-release-collection-v1.json
.data/evals/sec-release-acceptance-v1/sec-release-evidence-report-v1.json
.data/evals/sec-release-acceptance-v1/sec-release-evidence-report-v1.md
.data/evals/sec-release-acceptance-v1/sec-release-recovery-report-v1.json
.data/evals/sec-release-acceptance-v1/recovery-evidence/
.data/evals/sec-release-acceptance-v1/sec-live-identity-v1.json
.data/evals/sec-release-acceptance-v1/final-readiness-report.json
```

每次恢复执行使用唯一 state 目录，因此失败后可直接重跑，不需要人工删除旧状态。日志会脱敏 password、secret、token、API key、Authorization/Bearer 和 PostgreSQL DSN 密码。

## 5. 人工打开与治理确认

工程报告全绿后，人工只处理不能由代码代签的事项：

1. 打开桌面/移动截图与 Playwright trace，确认中文显示、交互、状态恢复和 Evidence drilldown；
2. 打开 evidence/recovery/final-readiness 报告，确认阈值、50 或 150 Run 分母、12/12 恢复和 source commit；
3. 对冻结中文财务样本完成领域抽样并签字；
4. 对计划公开发布的外部数据或 benchmark 完成权利决定；
5. 记录该 source commit 的 branch、PR、merge-to-main CI URL，并由项目所有者作最终发布决定。

上述人工项不能由本地测试或生成器替代；在完成前只能声明工程证据已生成，不能声明正式发布已批准。
