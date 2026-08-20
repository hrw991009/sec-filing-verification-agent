# Agent Harness v1：Day 3 L1/L2、行业采集与 Text2SQL 切片

> 更新日期：2026-08-20
>
> 计划基线：`docs/master-plan.md` 1.7.0 Day 3
>
> 当前状态：五个可验收步骤、全量本地门禁、PR 合并与 `main` 合并提交的干净 CI 均已通过；D3-01～D3-11 已复核为 `complete`。

## 1. 今天的五个可验收步骤

1. 完成正式 L1 单 Tool 纵向切片：typed Tool contract、Registry/Executor、静态策略、Action→Observation→最终回答、Context Compiler v1、Event/Trace/PostgreSQL 审计、Fake Scenario、安全校验与学习复盘。
2. 在同一 Runtime 上增加 L2 有界循环，补齐 max steps、no-progress、重复 Observation、跨轮预算、deadline、取消、Tool timeout/failure 和确定性 fault injection 轨迹。
3. 完成当前行业上下文与真实外部能力切片：四个预设行业的搜索/切换、持久化和越权拒绝；一个合规 Web/行业 Tool 的来源快照/摘要与 Citation；新闻、政策、招投标、股票、手动/定时采集和 Schedule/Occurrence→Job/Outbox 正式链路；验收 SSRF/跳转/响应上限、来源追踪、去重、未配置语义、IANA timezone、停机补跑、misfire 上限和多 Beat 收敛。
4. 完成数据库能力切片：数据库浏览、安全 Text2SQL、只读 AST、schema/table/column allowlist、查询预算，以及受校验的表格/图表 Artifact 和错误旅程。
5. 完成 Tool Inspector 与行业/数据库/图表正式页面和 E2E，输出 L0/L1/L2 trajectory report、累计数据集，并逐项关闭 Day 3 Definition of Done 与学习门禁。

第 5 步已经完成：正式页面、Tool Inspector、生产 Web Tool 用户旅程、累计数据集、trajectory report、安全复核、运行手册和全量本地门禁均有可执行证据。Day 3 门禁已经关闭；后续按 Day 4 执行计划推进，不再在本文扩张 Day 3 范围。

## 2. 第一步的正式执行链

```text
ToolL1Profile 选择恰好一个版本化 Tool
  → UnifiedAgentRuntime 创建 Action Model Step
  → Model 只返回受 response schema 约束的 ToolAction
  → Runtime 记录 TOOL_REQUESTED
  → ToolRegistry 用可信定义校验 name/version、typed arguments、capability 和静态 approval policy，并核对可信 Runtime Context 的 WorkspaceScope
  → allow：创建 Tool Step，并由 ToolExecutor 调用 Adapter
  → deny / approval_required：记录稳定 Event 和 stop reason，绝不创建虚假的执行 Step
  → Adapter 输出先归一化为有界 ToolObservation
  → ContextCompilerV1 把 Observation 作为不可信 USER data 注入第二个 Model Step
  → Runtime 生成正式 Markdown final，并以唯一终态结束
```

生产 L0、生产 Web L2 与 Harness L1/L2 都经 `UnifiedAgentRuntime` dispatch；Harness 只通过正式 Port/构造边界注入 Model、Tool、manifest/Event store、控制与时间等 test doubles，并由服务端物化可信执行身份，不实现第二套 loop。真实 PostgreSQL 集成用例既直接验证内部 L1/L2 Runtime 与 SQL ports，也从 Conversation Application Service 创建 Web Turn，经持久 Job、正式 Loader 与统一 Runtime 调用 `industry.web_search:v1`。采集仍走独立的 Schedule/Occurrence→Job/Outbox→Worker Application Service；数据库页则调用同一安全 Text2SQL Application/Artifact 边界。L1/L2 使用 provider-neutral 的严格 JSON response schema，当前没有扩展成特定 Provider 的原生 `tool_calls`。

L2 在同一状态机上重复 `决策 Model → Action → Registry/策略 → Tool → Observation`，每次决策只能返回严格的 `tool_call` 或 `final` 分支。每轮 Context 都包含此前全部有界 Observation；Runtime 在继续前统一核对剩余 Step、Token、费用、deadline 与取消。相同 name/version/参数摘要的 Action 在再次执行前以 `no_progress` 拒绝；不同参数若产生相同的规范化内容，第二次已发生的 Tool/Step 审计事实会被原子保存，然后以 `no_progress` 停止。确定性 L2 用例在同一精确 allowlist 内实际选择并执行两个不同 typed Tool；生产 Web 用户旅程使用 `industry.web_search:v1`，数据库页面和 Registry 合同另行验证 `database.text2sql:v1` 与可追溯 Artifact。

## 3. Tool、Skill、Application Service 与 Harness

| 组件 | 负责什么 | 明确不负责什么 |
|---|---|---|
| Tool | 一次有 name/version、typed input/output、所需 capability、timeout、成本、副作用和审批策略的能力调用；具体 WorkspaceScope 来自可信 Runtime Context | 不携带某次调用的具体租户授权，不自行扩大预算或决定循环 |
| Skill | Instructions、允许的 Tools、Context 策略和输出合同组成的版本化 Harness 配置 | 不是可执行任意代码的隐藏插件 |
| Application Service | 持有业务事实、业务授权、事务和幂等规则；由 Tool Adapter 调用 | 不读取模型文本决定权限，不实现 model/tool loop |
| Agent Harness | 组合 profile、Context、Tool surface、Fake/Replay/Fault 和 Scorer，并调用正式 Runtime | 不复制 Runtime 状态机，不把测试 Fake 作为生产 fallback |

Runtime 决定 Step/Event/State/Budget/stop reason；ToolExecutor 是 Runtime Port；Registry 和 Adapter 是该 Port 的正式实现边界。这个依赖方向使生产与评测共享同一执行语义。

## 4. Typed contract 与持久事实

主要合同位于 `modules/tools/domain.py`、`registry.py` 和 `agent_runtime/tool_runtime_contracts.py`：

- `ToolDefinition` 冻结 name/version/description、输入输出 Schema 版本、capability、timeout、成本上限、副作用类别、approval policy 和 retry classification；retry classification 只描述边界，L1/L2 均禁止隐式重试；
- `ToolAction` 只能携带模型建议的 name/version/arguments，不能携带可信 policy、WorkspaceScope、Budget 或审批结果；
- `ToolCall` 是通过 Registry 校验后的执行合同；写操作的原始副作用幂等键只在受控内存中保留、从 `repr` 隐藏并传给 Adapter，Event/Trace/PostgreSQL 只能保存其 SHA-256；
- `ToolObservation` 必须有来源类型/版本、时间、locator、content SHA-256 和 normalizer version，并受条数、文本与 locator 上限约束；locator fail-closed 拒绝 userinfo、query、fragment 和控制字符，完整 model-visible envelope digest 还会绑定正文与 provenance；
- `ApprovalRequest/Decision` 的 typed contract 已冻结；Day 3 第一步只静态 allow/deny，或发出 `approval_required` 后停止，不实现持久 resume。

PostgreSQL 新增一对一的 `tool_calls` 与 `tool_runs`；二者是归属于 AgentRun 的 operational audit projection，后者不是独立状态机或独立 security audit 事实源。请求 Tool 的 Model Step 始终存在；只有真正 allow 后才绑定可空且唯一的 Tool execution Step。Runtime 把 Event batch 与对应 Tool/Step/Run 投影放在同一事务原子提交，并校验 call/run/workspace、请求/执行 Step 与 trace 的关联；不完整或不一致的 batch 整体失败。两表只保存参数/幂等键 hash、有界脱敏摘要、策略/Schema 快照、规范化 Observation、来源、耗时、实际成本和稳定错误码，不保存 raw arguments、原始 Provider/Tool 大响应、Secret、Runtime Context 或 chain-of-thought。

当前 Tool 外键以 `RESTRICT` 阻止普通 AgentStep/Run 删除隐式清空这些投影，`ToolRun` actor 还通过复合外键绑定对应 Run 的 workspace/user。普通 Conversation 删除是逻辑删除，真实 PostgreSQL 已验证 Tool audit 继续保留；queued-cancel/unrecoverable terminalizer 与陈旧 QueryRun 也会推进权威终态/revision。面向用户的物理 Run purge 尚不开放：显式授权 purge、最小 security audit 留存和隔离备份恢复演练继续作为 Day 7 对外发布门禁，而不是让普通删除隐式级联。

## 5. Context Compiler v1 与信任边界

Action 决策和最终回答各自生成独立 Context manifest。第一次调用把 structured response schema 的保守 UTF-8 上界计入输入 Token 预留；第二次模型调用在原问题之后加入恰好一个 `tool_observation` 来源。manifest 只保存版本、引用、完整 model-visible Observation envelope hash、决策和 Token 估算，不保存 Observation 正文。

Observation 即使来自已授权 Tool 仍是不可信数据：网页、数据库字段或外部系统都可能包含 prompt injection。Compiler 因此把它放入明确标注的 USER data envelope；它不能改变 system instructions、Tool allowlist、WorkspaceScope、Budget 或 approval。必需 Observation 超过单条/总条数/Token 预算、跨 Workspace、时间或 digest 校验失败时，编译 fail-closed，不静默截断后继续。

Observation 还不是 Evidence。它只证明“某个 Tool 在某个时间返回并被规范化了什么”；来源许可、locator 有效性、授权、去重和 Evidence Normalizer 尚未完成。Observation→Evidence 的可信提升属于 Day 4。

## 6. Harness Scenario 与停止语义

`evals/scenarios/day3-l1-v1.json` 登记 5 个 L1 版本化用例；`evals/scenarios/day3-l2-v1.json` 再登记 10 个 L2 用例：两轮成功、重复 Action、重复 Observation、max steps、跨轮 Token/费用预算、取消、deadline、第二轮 Tool timeout 与 Tool failure。10 条 L2 case 全部通过 `HarnessRunner`、L2 materializer 和 `UnifiedAgentRuntime` 执行。加上 Day 2 的 9 条，当前累计 24 条。

L1 的继续条件非常窄：结构化 Action 通过可信 Registry/策略后，只执行一次 Tool，并只把一次归一化 Observation 送入最终回答。L2 只有在决策要求一个未重复的新 Action、所有静态策略通过且仍为下一次决策与 Final Step 留出预算时继续；模型直接返回 final、任一预算/控制边界触发、Action 重复或 Observation 内容重复时停止。L1/L2 都必须在每个已实现 safe point 对校验失败、deny、approval_required、Tool/Provider failure、Run deadline、预算或取消给出稳定终态，且没有隐式 Tool retry。Tool 完成、取消和硬超时同时竞争时 fail-closed，只允许一个结算结果；非零实际 Tool 成本必须不超过定义上限，并在 completed Event、Tool Step、Run state 与 Tool 投影之间数值一致、只计一次。

Fake Industry Lookup 不访问网络、Shell、数据库或 Secret，只用于可重复验证 Runtime 合同。真实行业 Tool 走固定 Provider registry 和受控公网 egress；CI 用冻结的官方响应形状验证 parser/normalizer，不把网络波动混入确定性 Scenario。实时 readiness 与冻结合同证据分开记录。

## 7. 第一步的验收边界

本步必须同时满足：

- 正常链：两个 Model Step、一个 Tool Step、一个 Final Step，共用唯一 Runtime；
- 负向链：参数错误、未知/越权 Tool、capability 拒绝、approval_required、Tool failure 和恶意 Observation 不得绕过边界；
- 持久化：fresh migration、upgrade/downgrade、复合 Workspace FK、原子 Event batch，以及 Tool/Step/Run 投影的一致提交；
- 竞态与成本：Tool 完成/取消/硬超时竞争 fail-closed，非零实际成本在 Event、Step、Run 与投影间守恒且只计一次；
- Trace/API：只暴露 allowlist 内的安全标量且不返回参数/幂等键 digest，前端对 Observation envelope hash 缺失 fail-closed，call/run/workspace/actor/Step/trace/Observation correlation 不一致即拒绝；
- 安全：不保存 raw arguments/result、原始幂等键、Secret/Runtime Context、Provider body 或模型控制的未知参数键；error code、locator、digest 和幂等键 hash 均执行稳定的 fail-closed 校验；
- 学习：能够解释四类职责、Observation/Evidence 边界和继续/停止规则。

真实来源、行业上下文和正式采集后端已在第 3 步验收；第 4 步完成只读样例库、数据库浏览、完整 AST allowlist、计划/结果预算、QueryRun 审计和受校验 Artifact；第 5 步又关闭多 Tool 选择、Tool Inspector、行业/数据库/图表 UI、陈旧 QueryRun 对账、trajectory report 和浏览器用户门禁。显式物理 Run purge与隔离备份恢复演练不属于 Day 3 冻结产物，但已作为 Day 7 发布门禁保留，不能被当前逻辑删除策略冒充完成。

## 8. 代码与验证入口

- Runtime/Context：`apps/backend/src/industry_platform/modules/agent_runtime/tool_runtime.py`、`tool_runtime_contracts.py`、`context_compiler.py`；
- Tool contract/Registry/Executor：`apps/backend/src/industry_platform/modules/tools/`；
- Harness/Fake：`apps/backend/src/industry_platform/modules/agent_harness/tool_use.py`、`tool_fakes.py`、`profiles.py`；
- PostgreSQL：`apps/backend/migrations/versions/4d9b8f6c2a10_create_tool_execution_facts.py` 与 Agent Event committer；
- 单元/Scenario：`apps/backend/tests/modules/agent_runtime/test_tool_l1_runtime.py`、`test_tool_l2_runtime.py`、`test_context_v1.py`、`apps/backend/tests/modules/tools/`；
- 真实 PostgreSQL：`apps/backend/tests/integration/test_tool_l1_postgres.py`、`test_tool_l2_postgres.py` 与 `test_migration_smoke.py`；
- Web contract：`apps/web/src/chat/chat-api.test.ts`、`chat-workbench-model.test.ts`。

第 3 步的代码与证据入口：

- 行业合同、Provider 与 Tool：`apps/backend/src/industry_platform/modules/industry/domain.py`、`providers.py`、`tool.py`；
- 采集 Application/SQL/API：同模块的 `service.py`、`adapters/sqlalchemy.py`、`router.py` 与 migration `77c3f51a9d20`；
- 正式 Schedule/Worker 组合：`modules/jobs/adapters/sqlalchemy.py`、`workers/runtime.py`、`workers/beat.py` 与 `workers/celery_app.py`；
- 测试：`apps/backend/tests/modules/industry/`、`apps/backend/tests/integration/test_industry_collection_postgres.py` 与既有 Scheduler/Job/egress 安全集；
- 来源条款与安全复核：[Day 3 真实来源复核](security/day-3-source-review.md)。

第 4 步的代码与证据入口：

- 数据库合同、AST 与 Artifact：`apps/backend/src/industry_platform/modules/data_explorer/domain.py`、`sql_validator.py` 与 `artifacts.py`；
- 只读 Adapter、Application Service、Tool 与 API：同模块的 `adapters/postgresql.py`、`service.py`、`tool.py` 和 `router.py`；
- PostgreSQL：migration `c6a8e1d4f290`，保存固定合成样例、DataConnection、SchemaSnapshot、QueryRun、QueryResult 和 ChartSpec；
- 测试：`apps/backend/tests/modules/data_explorer/` 与 `apps/backend/tests/integration/test_data_explorer_postgres.py`；
- 依赖与威胁复核：[Day 3 安全 Text2SQL 复核](security/day-3-text2sql-review.md)。

第 5 步新增入口还包括 `apps/web/src/industry/`、`apps/web/src/data-explorer/`、`TracePanel.tsx`、`apps/backend/tests/integration/test_conversation_web_tool_postgres.py`、`tests/e2e/app-shell.spec.ts`、`evals/reports/day3-v1.json`、[前端安全复核](security/day-3-ui-review.md)与 [Day 3 运行手册](runbooks/day-3-agent-tools.md)。最终本地门禁见 [Day 3 学习日志](learning-log/day-3.md)；[PR #5](https://github.com/hrw991009/industry-intelligence-platform/pull/5) 与合并提交 [`6968c63f`](https://github.com/hrw991009/industry-intelligence-platform/commit/6968c63f3330f3079e3e1cc2db0b29488d7502a2) 的 [干净 CI](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/32112639811) 已关闭最后的验证条件。
