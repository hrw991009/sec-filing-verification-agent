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

## 6. 单独验证真实模型产品链路

只验证产品链路、不启动 50/150 Run 评测和恢复矩阵时，在 Compose 依赖健康且 `.env` 模型配置完成后运行：

```powershell
$env:SEC_BROWSER_LIVE_MODEL = 'true'
try {
    pnpm run test:e2e:sec-real
} finally {
    Remove-Item Env:SEC_BROWSER_LIVE_MODEL
}
```

这会实际调用配置的模型并产生费用。API、Worker 和迁移统一使用 Settings 加载 `.env`，不要额外用 `uv --env-file` 重复解析 JSON 配置。测试不复用已有 API/Web 进程，运行前应释放 8000/5173 端口。

真实模型模式检查申报入库、财务计算、Verification Report、Calculation Evidence 反查、刷新后的审批恢复和新申报 Monitor Case。申报来源仍是固定的 `sec-browser-v1` 受控衍生快照，不代表实时 SEC 全量入库；官方 SEC 连通性检查属于独立证据。检索使用项目现有索引与 embedding 实现，不额外接入 embedding API。

结果写入 `test-results/sec-live-model-runtime/`，清单区分 `configured_live` 模型与 `controlled_derivative` 来源，并记录 HEAD、工作树差异 hash 和测试退出结果。允许脏工作树做本地诊断，但不能作为干净提交的正式发布证明。未设置该变量时，原有受控模型测试行为不变。

### 2026-09-14 本地验证状态

- 受控模型 + 真实服务浏览器链路：通过（1/1），包括审批前刷新、恢复执行、Monitor Case 持久化和移动端宽度检查。
- 官方 SEC 身份 smoke：通过，记录在 `test-results/sec-live-model-runtime/sec-identity.json`；不代表官方全文入库验收。
- 配置的 `deepseek-v4-flash-0731`、`reasoning_enabled=false`：连通，但完整产品链路未通过。最后一轮 Run `7a09cced-0701-5ce6-88b0-85101588f833` 实际调用了 `sec.search_filing`，随后提前 final，未执行要求的计算和监控申请。历史尝试还出现输出截断、提前拒答和监控参数校验失败。
- 经用户同意，将同一模型的 `reasoning_enabled` 仅在测试进程中设为 `true`，其他模型配置和预算保持不变；Run `85b6e720-bea3-54b6-8ae8-a93b8db32466` 仍未通过（浏览器 1/1 失败）。实际发生两次模型调用、一次 `sec.search_filing`，没有 XBRL、Calculation 或 Monitor 申请，随后 final。正文声称没有用户问题，但两次 Context Manifest 均记录 `current-user-question` 为 included，不能据此认定问题被截断或遗漏。报告仍为 `verified`，须进一步检查任务完成判定及 Claim 核验覆盖。独立产物保存在 `test-results/sec-live-reasoning-runtime/`，未覆盖关闭 reasoning 的记录；测试前后 `.env` SHA-256 一致，测试 API/Web/Worker/Dispatcher 已退出。
- `Verification Report = verified` 仅表示已提交 Claim 的核验结果，不证明用户要求的所有操作完成；浏览器测试另行要求计算、审批和 Case，不能降低这些断言来通过测试。
- 因此“真实模型中文产品链路”仍待验收，不能将上述受控通过记录替代真实模型成功记录。此次未执行 50/150 Run 正式评测、12 项恢复演练或发布治理签字。

### 2026-09-14 运行中修复与复测

本轮修复保持统一 Runtime 和原有 Research LangGraph，没有另建模型循环：

- SEC Brief 增加可选的“必做步骤”，按确认顺序执行。步骤保存在 `research_briefs.required_tool_names`，参与请求幂等指纹，并在审批恢复后继续使用；本地已应用迁移 `c4f6a8b0d135`。只读 Skill 不能要求监控工具。未选择必做步骤时仍为开放式工具循环，不从问题中的关键词猜测授权。
- 工具决策同时提供完整参数约束和兼容提供方的响应 schema；待完成步骤只暴露下一项工具，未完成时拒绝提前 final。无来源的读取结果不计为完成，不绕过参数、权限或审批校验。
- 财务 Context 必须保留最近的可用工具结果，旧结果按从新到旧的优先级纳入预算；原始问题放在来源之后，同角色来源发送为一个保留全部内容的消息。多步骤浏览器用例显式确认 100000 Token 预算。
- XBRL 数字事实提供与计算工具共用类型的 `calculation_operand`，绑定数值、Evidence ID 和 Fact ID；支持序列化回读并拒绝不一致绑定。计算仍重新授权并进行期间、单位核对。
- Claim 只关联回答实际引用的来源，使用稳定的 `[TnSn]` 标签；无引用或无效引用不再自动绑定全部 Evidence。该修复不等于通用语义蕴含验证，`verified` 也不能替代完整任务验收。

复测结果：

- 相关后端测试 **557 项通过**；Research 前端测试 **8 项通过**；源代码 mypy 与 TypeScript 类型检查通过。
- 当前代码的受控模型 + 真实基础设施浏览器链路 **1/1 通过**（35.5 秒），包括刷新后的审批恢复、新申报导入、Monitor Case 和移动端检查。
- 非推理真实模型 Run `94731b47-148a-5017-aca0-f193340276d0` 完成检索、XBRL、`finance.calculate`，结果 **44.13%**；经过一次审批暂停、允许和恢复，随后在生成报告时 `provider_timeout`。这一轮的请求上限为 90 秒，不能算完整通过。
- 最新真实模型 Run `f43fba98-e431-52e8-b76b-4544d964d16f` 保持 `reasoning_enabled=false`，测试进程请求上限为 180 秒；检索和 XBRL 后以 `invalid_provider_response` 结束，完整浏览器验收仍失败。同模型的推理模式也未取得完整通过记录。
- `.env`、模型路由和密钥未修改；90/180 秒超时及 reasoning 对照仅影响各自测试进程。不能把增大超时视为已解决模型可靠性问题。

最新清单位于 `test-results/sec-live-model-runtime/runtime-manifest.json` 和 `test-results/sec-real-runtime/runtime-manifest.json`。新清单记录实际模型配置，并通过 `playwright_artifacts` 指向独立时间戳目录，避免把旧失败截图混入后续成功产物。历史运行以 Run ID 和数据库事件为准，旧根目录产物可能已被后续复测替换。全部仍是脏工作树上的本地诊断，来源为受控衍生快照，不能作为正式发布证明。

### 2026-09-14 临时模型路由对照

经用户同意，仅在测试进程中切换模型，使用同一 OpenRouter 账户密钥、相同的完整浏览器用例和 180 秒请求上限。未修改 `.env`，未降低计算、引用反查、审批恢复或 Monitor Case 的断言。价格表取自本次 OpenRouter `/api/v1/models` 返回值，按项目 micro-USD 单位转换；未更改工具权限或申报来源。

- `anthropic/claude-sonnet-4.6`：Run `543e19ee-74e8-5c87-8801-bf01cff5e5b0` 首次模型调用即以 `provider_permission_denied` 失败，未执行工具。独立清单为 `test-results/sec-live-sonnet-runtime/runtime-manifest.json`。
- `google/gemini-2.5-flash`：Run `3cb18b00-f3fe-552c-8263-b2171d277d08` 同样在首次模型调用被拒绝。独立清单为 `test-results/sec-live-gemini-runtime/runtime-manifest.json`。使用同一配置端点和密钥的最小文本请求也返回 HTTP 403，错误消息为 `The request is prohibited due to a violation of provider Terms Of Service.`，因此该拒绝不依赖 Research Context 或工具 schema。没有尝试绕过限制；响应本身不能确定是账户、区域还是其他服务策略导致。
- 当前后端相关回归测试重新运行，**557 项通过**；`git diff --check` 通过。测试前后 `.env` SHA-256 一致，API/Web 的 8000/5173 端口已无监听。

两次对照均未获得模型决策，不能用于证明其他模型可完成链路，也不能据此认定原 DeepSeek 路由的超时/无效响应根因已解决。真实模型完整产品链路仍未验收通过；继续对照需要先由用户确认一个获准且可实际调用的模型路由。

### 2026-09-15 新网关配置与认证检查

用户授权切换至 `https://ai.iisbo.com/v1`，候选模型为 `gpt-5.6-sol` / `glm-5.3`，本轮请求额度上限为 700 次。已将本地 `.env` 的地址、GLM 路由和 180 秒超时更新，未改动密钥。按次计费的实际单价未提供，不能沿用原 OpenRouter token 价格；路由使用 `iisbo-call-metered-token-unpriced-v1` 和零 token 单价，**仅表示当前 token 账本未定价，不表示真实调用免费**。

- 新网关的 `/models` GET 和一次 `glm-5.3` 最小 Chat Completions POST 均返回 HTTP 401；后者错误为 `Invalid token`。共发起 2 次网关请求，其中 1 次生成请求，是否计费以服务商账单为准。
- Settings 实际读取的密钥与根目录 `.env` 一致，无进程变量覆盖、重复定义、首尾空格或多余 `Bearer` 前缀。未记录密钥内容，也未继续重复认证失败请求；已请求用户保存该网关的有效 Key。
- 后端相关回归 **557 项通过**，Research 前端 **8 项通过**。受控模型 + 真实服务浏览器链路 **1/1 通过**（35.6 秒），包括审批恢复与新申报 Monitor Case；Research Run 为 `15cc0abb-cd37-5242-82d1-cd0296cd7ce7`，清单为 `test-results/sec-real-runtime/runtime-manifest.json`，产物目录为 `playwright-20260915T021202114770Z`。

当前新路由尚未取得有效模型响应，认证成功、响应模型名称和完整真实模型链路均待验证。受控通过不能代替这些验收，也不能证明原 DeepSeek 超时/无效响应已解决。

同日补测用户指定的 `gpt-5.6-sol`：同一新网关和本地 Key 的最小 Chat Completions 请求仍返回 HTTP 401 `Invalid token`，没有模型输出或 usage。新网关累计已发起 3 次请求（其中 2 次生成请求）；不能只用 GLM 的失败推断另一模型结果，此处为 GPT 路由的独立实测。已将 `.env` 当前模型切换为 `gpt-5.6-sol`，保留新地址和 180 秒超时，不发送未经确认的 reasoning、temperature 或 seed 参数，Key 未修改。两条指定路由的最小请求现均被认证拒绝，尚无法进入真实工作流。

随后按用户指定切换至 `gemini-3.8-flash`，独立最小请求仍返回 HTTP 401 `Invalid token`，上游 request id 为 `202609150226515428004058268d9d6i3KRJrIB`，没有模型输出或 usage。累计新网关请求为 4 次（3 次生成请求）。已确认当前 Settings 使用 `https://ai.iisbo.com/v1`、根目录 `.env` 的同一 Key；无进程 Key 覆盖、代理环境变量、空白或非 ASCII 字符。`.env` 当前路由已改为 Gemini，Key 未修改。该结果仅证明本项目使用的凭据和请求组合被拒绝，不否定用户在其他请求配置中测试成功；需对齐成功请求的凭据/认证方式后继续完整链路。

### 2026-09-15 新 Key 后的修复与真实链路验收

用户更新 Key 后，Gemini 和 GPT 最小请求均返回 HTTP 200，认证阻塞解除。Gemini 的实际响应模型名为 `gemini-3.8-flash-tiered`，但完整链路中多次生成非法筛选参数或重复无结果查询，未通过验收。使用用户此前授权的 `gpt-5.6-sol` 对照后，定位并修复了后续真实后端问题。**最终 `.env` 使用 `https://ai.iisbo.com/v1`、`gpt-5.6-sol`、180 秒请求超时；Key 未由代理修改。** 按次计费仍使用上述未定价 token 账本，不将账本中的零值解释成免费调用。

本轮修复：

- Provider usage：支持推理 token 单列的响应，只在总量精确等于输入、可见输出和显式推理量之和时归一化；标准响应不重复计数，非法/无法核对的用量仍被拒绝。
- XBRL Tool：输入 schema 从实际 Pydantic 模型生成，补齐空字符串、标识符、列表长度等约束；可空字段说明在转换到严格响应 schema 时保留。无筛选使用 JSON null，不偷偷把非法参数改成合法参数。
- Evidence 落库：原 XBRL 来源版本与定位键直接拼接超过 `source_resource_version` 的 128 字符上限，导致 PostgreSQL `22001`。改为完整组合的稳定指纹，完整来源和 Fact ID 仍可反查；没有截断身份或扩大数据库字段绕过约束。
- Calculation lineage：计算与引用生成共用 `FinancialOperand.value_in_scope`，引用保存以 FinancialScope 单位/scale 归一化后的操作数，避免美元原值和百万美元公式不一致。
- Verifier：其他有效引用不能掩盖同一必需 Claim 的错误计算引用；存在 error 时不再聚合为 verified。浏览器验收新增检查报告零 error、存在通过核验的 calculation_refs，并通过正常登录后的浏览器请求读取报告。
- 审批恢复：服务器确认的完成结果以 `approved-tool-result-v2` 显式提供批准、执行完成和资源引用；SEC L5 提示区分审批前禁止宣称创建与执行完成后如实报告，仍不允许模型授予审批或虚构角色。

证据与最终结果：

- 早期浏览器 Run `94fcf5e8-5749-5485-b87b-7f819f172882` 虽通过旧断言，但账本存在 `calculation_mismatch`，**不计为最终成功证据**；历史记录未改写。更早的 `normalize_evidence` 失败 Run `d62a2ee2-f6ea-5fc9-953f-3b1827acc39b` 用于事务内诊断，重放后显式回滚，未人工改成成功。
- 加强断言后的首次通过：Agent Run `a1f9f891-a2e3-5b28-986b-13a3df76fd45`，Research Run `b4883c59-5571-569e-81a0-304a6688105e`，浏览器 **1/1 通过（3.3 分钟）**；报告 `c6ea8cae-4dd1-5750-9d3c-d08d882d6372` 为 verified、coverage 1.0、0 issues，存在有效 Calculation Citation。产物见 `test-results/sec-live-model-runtime/runtime-manifest.json` 的 `playwright-20260915T031532532026Z`。
- 修正审批状态提示后的最终确认：Agent Run `a5a9ed83-25a5-5680-be09-26cf062f350d`，Research Run `35f67e29-039b-5e0d-a762-20bf31101b81`，浏览器 **1/1 通过（3.7 分钟）**；报告 `02e241e8-b8f7-56d7-9ece-223e3977d366` 为 verified、coverage 1.0、0 issues。实际计算为 **44.13%**，引用反查、刷新后的审批恢复和新申报 verified Case 均通过。最终回答正确确认监控已完成审批与创建，资源为 `sec-monitor:44f4c35f-21df-58cd-ad94-4d38b786e06f`。独立清单为 `test-results/sec-live-final-confirmation-runtime/runtime-manifest.json`，产物目录 `playwright-20260915T032036776807Z`。
- 当前相关后端回归 **627 项通过**；审批恢复 PostgreSQL 集成测试 **1 项通过**（隔离测试数据库）；Research 前端 **8 项通过**；mypy 308 个源文件、TypeScript、相关 ESLint、Ruff 与差异检查通过。
- 本地发起记录为 49 次工作流模型请求、2 次认证成功后的最小生成请求；加上早先 3 次 401 生成请求和 1 次 models GET，新网关累计 55 次请求，低于用户授权的 700 次上限。此为本地请求记录，不替代服务商计费账单。

结论：原先第一步的**真实模型中文产品链路已在本地验收通过**。来源仍为固定受控衍生申报快照，工作树仍为 dirty；不是官方 SEC 全文实时入库证明，也不是干净提交、50/150 Run 正式评测、12 项恢复矩阵或完整发布签字。未提交或暂存本轮改动。
