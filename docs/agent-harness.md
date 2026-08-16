# Agent Harness v1：Day 3 L1 薄切片

> 更新日期：2026-08-16
>
> 计划基线：`docs/master-plan.md` 1.7.0 Day 3
>
> 当前状态：当天第 1 个可验收步骤已通过本地验收；D3-02 仍为 `thin_slice`，尚无对应干净 CI，Day 3 门禁尚未通过。

## 1. 今天的五个可验收步骤

1. 完成正式 L1 单 Tool 纵向切片：typed Tool contract、Registry/Executor、静态策略、Action→Observation→最终回答、Context Compiler v1、Event/Trace/PostgreSQL 审计、Fake Scenario、安全校验与学习复盘。
2. 在同一 Runtime 上增加 L2 有界循环，补齐 max steps、no-progress、重复 Observation、跨轮预算、deadline、取消、Tool timeout/failure 和确定性 fault injection 轨迹。
3. 完成当前行业上下文与真实外部能力切片：四个预设行业的搜索/切换、持久化和越权拒绝；一个合规 Web/行业 Tool 的来源快照/摘要与 Citation；新闻、政策、招投标、股票、手动/定时采集和 Schedule/Occurrence→Job/Outbox 正式链路；验收 SSRF/跳转/响应上限、来源追踪、去重、未配置语义、IANA timezone、停机补跑、misfire 上限和多 Beat 收敛。
4. 完成数据库能力切片：数据库浏览、安全 Text2SQL、只读 AST、schema/table/column allowlist、查询预算，以及受校验的表格/图表 Artifact 和错误旅程。
5. 完成 Tool Inspector 与行业/数据库/图表正式页面和 E2E，输出 L0/L1/L2 trajectory report、累计数据集，并逐项关闭 Day 3 Definition of Done 与学习门禁。

本轮严格停在第 1 步。第 2～5 步未开始，不能把 Fake Tool 写成真实 Web 能力，也不能提前宣称 Day 3 完成。

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

生产 L0 与 Harness L1 都经 `UnifiedAgentRuntime` dispatch；Harness 只通过正式 Port/构造边界注入 Model、Tool、manifest/Event store、控制与时间等 test doubles，并由服务端物化可信执行身份，不实现第二套 loop。真实 PostgreSQL 集成用例则直接调用同一个内部 `ToolL1Runtime`，把 manifest、Event 和控制边界替换为 SQL ports，以验证 Runtime/持久化合同；它没有经过生产 L1 Application/Job/Worker 入口。当前生产 composition 只注入 L0，Conversation/Job/Worker 尚未物化 `ToolL1RunCommand`，所以这一步不是可供用户启用的生产 L1 旅程。L1 使用 provider-neutral 的严格 JSON response schema，当前没有扩展成特定 Provider 的原生 `tool_calls`。

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

- `ToolDefinition` 冻结 name/version/description、输入输出 Schema 版本、capability、timeout、成本上限、副作用类别、approval policy 和 retry classification；retry classification 只描述边界，L1 仍禁止隐式重试；
- `ToolAction` 只能携带模型建议的 name/version/arguments，不能携带可信 policy、WorkspaceScope、Budget 或审批结果；
- `ToolCall` 是通过 Registry 校验后的执行合同；写操作的原始副作用幂等键只在受控内存中保留、从 `repr` 隐藏并传给 Adapter，Event/Trace/PostgreSQL 只能保存其 SHA-256；
- `ToolObservation` 必须有来源类型/版本、时间、locator、content SHA-256 和 normalizer version，并受条数、文本与 locator 上限约束；locator fail-closed 拒绝 userinfo、query、fragment 和控制字符，完整 model-visible envelope digest 还会绑定正文与 provenance；
- `ApprovalRequest/Decision` 的 typed contract 已冻结；Day 3 第一步只静态 allow/deny，或发出 `approval_required` 后停止，不实现持久 resume。

PostgreSQL 新增一对一的 `tool_calls` 与 `tool_runs`；二者是归属于 AgentRun 的 operational audit projection，后者不是独立状态机或独立 security audit 事实源。请求 Tool 的 Model Step 始终存在；只有真正 allow 后才绑定可空且唯一的 Tool execution Step。Runtime 把 Event batch 与对应 Tool/Step/Run 投影放在同一事务原子提交，并校验 call/run/workspace、请求/执行 Step 与 trace 的关联；不完整或不一致的 batch 整体失败。两表只保存参数/幂等键 hash、有界脱敏摘要、策略/Schema 快照、规范化 Observation、来源、耗时、实际成本和稳定错误码，不保存 raw arguments、原始 Provider/Tool 大响应、Secret、Runtime Context 或 chain-of-thought。

当前 Tool 外键以 `RESTRICT` 阻止普通 AgentStep/Run 删除隐式清空这些投影，`ToolRun` actor 还通过复合外键绑定对应 Run 的 workspace/user。生产 L1 启用前还必须实现显式、授权、幂等的 Run purge：按依赖顺序清理 Run-owned operational facts，同时按冻结期限保留最小 security audit 记录，并完成隐私擦除、恢复和备份测试；旧 queued-cancel/unrecoverable terminalizer 也要补齐最终 revision 投影。这些项目仍为 open，不能因为第一步写入迁移而标成已完成。

## 5. Context Compiler v1 与信任边界

Action 决策和最终回答各自生成独立 Context manifest。第一次调用把 structured response schema 的保守 UTF-8 上界计入输入 Token 预留；第二次模型调用在原问题之后加入恰好一个 `tool_observation` 来源。manifest 只保存版本、引用、完整 model-visible Observation envelope hash、决策和 Token 估算，不保存 Observation 正文。

Observation 即使来自已授权 Tool 仍是不可信数据：网页、数据库字段或外部系统都可能包含 prompt injection。Compiler 因此把它放入明确标注的 USER data envelope；它不能改变 system instructions、Tool allowlist、WorkspaceScope、Budget 或 approval。必需 Observation 超过单条/总条数/Token 预算、跨 Workspace、时间或 digest 校验失败时，编译 fail-closed，不静默截断后继续。

Observation 还不是 Evidence。它只证明“某个 Tool 在某个时间返回并被规范化了什么”；来源许可、locator 有效性、授权、去重和 Evidence Normalizer 尚未完成。Observation→Evidence 的可信提升属于 Day 4。

## 6. Harness Scenario 与停止语义

`evals/scenarios/day3-l1-v1.json` 新增 5 个版本化用例：成功、参数 Schema 错误、capability 拒绝、需要审批和 Tool 失败。加上 Day 2 的 9 条，累计 14 条。

L1 的继续条件非常窄：结构化 Action 通过可信 Registry/策略后，只执行一次 Tool，并只把一次归一化 Observation 送入最终回答。当前 L1 必须在每个已实现 safe point 对校验失败、deny、approval_required、Tool/Provider failure、Run deadline、预算或取消给出稳定终态，且没有隐式 Tool retry。Tool 完成、取消和硬超时同时竞争时 fail-closed，只允许一个结算结果，迟到结果不能覆盖已提交终态；非零实际 Tool 成本必须不超过定义上限，并在 completed Event、Tool Step、Run state 与 Tool 投影之间数值一致、只计一次。第 2 步还要把这些边界扩展成 L2 多轮轨迹，并新增 max steps、no-progress、重复 Observation 和跨轮预算的完整验收。

Fake Industry Lookup 不访问网络、Shell、数据库或 Secret，只用于可重复验证合同。它不能证明真实搜索质量，也不能让正式 `web` 模式从 readiness 变为可用。

## 7. 第一步的验收边界

本步必须同时满足：

- 正常链：两个 Model Step、一个 Tool Step、一个 Final Step，共用唯一 Runtime；
- 负向链：参数错误、未知/越权 Tool、capability 拒绝、approval_required、Tool failure 和恶意 Observation 不得绕过边界；
- 持久化：fresh migration、upgrade/downgrade、复合 Workspace FK、原子 Event batch，以及 Tool/Step/Run 投影的一致提交；
- 竞态与成本：Tool 完成/取消/硬超时竞争 fail-closed，非零实际成本在 Event、Step、Run 与投影间守恒且只计一次；
- Trace/API：只暴露 allowlist 内的安全标量且不返回参数/幂等键 digest，前端对 Observation envelope hash 缺失 fail-closed，call/run/workspace/actor/Step/trace/Observation correlation 不一致即拒绝；
- 安全：不保存 raw arguments/result、原始幂等键、Secret/Runtime Context、Provider body 或模型控制的未知参数键；error code、locator、digest 和幂等键 hash 均执行稳定的 fail-closed 校验；
- 学习：能够解释四类职责、Observation/Evidence 边界和继续/停止规则。

L2、真实来源、Text2SQL、Artifact、Tool Inspector、完整 trajectory scorer 和 Day 3 用户门禁在第 2～5 步验收；显式 Run purge、最小 security audit 留存与恢复/备份测试也仍为生产 L1 前置 open 项。本步不将这些义务标为 `N/A` 或提前完成。

## 8. 代码与验证入口

- Runtime/Context：`apps/backend/src/industry_platform/modules/agent_runtime/tool_runtime.py`、`tool_runtime_contracts.py`、`context_compiler.py`；
- Tool contract/Registry/Executor：`apps/backend/src/industry_platform/modules/tools/`；
- Harness/Fake：`apps/backend/src/industry_platform/modules/agent_harness/tool_use.py`、`tool_fakes.py`、`profiles.py`；
- PostgreSQL：`apps/backend/migrations/versions/4d9b8f6c2a10_create_tool_execution_facts.py` 与 Agent Event committer；
- 单元/Scenario：`apps/backend/tests/modules/agent_runtime/test_tool_l1_runtime.py`、`test_context_v1.py`、`apps/backend/tests/modules/tools/`；
- 真实 PostgreSQL：`apps/backend/tests/integration/test_tool_l1_postgres.py` 与 `test_migration_smoke.py`；
- Web contract：`apps/web/src/chat/chat-api.test.ts`、`chat-workbench-model.test.ts`。

最终本地门禁结果已记录在 [Day 3 学习日志](learning-log/day-3.md)；它只关闭今天第 1 步的本地验收，不代表 D3-02 `complete`、Day 3 执行门禁通过或生产 L1 用户旅程可用。
