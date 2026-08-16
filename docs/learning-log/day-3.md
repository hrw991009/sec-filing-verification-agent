# Day 3 学习日志：第一步 L1 Tool Use

> 更新日期：2026-08-16
>
> 计划基线：`docs/master-plan.md` 1.7.0 Day 3
>
> 当前结论：今天只执行五步计划中的第 1 步；该步已通过本地验收。D3-02 仍是 `thin_slice`，尚无对应干净 CI，Day 3 尚未完成。

## 1. Tool、Skill、Application Service 和 Harness 各自负责什么

Tool 是一次 typed capability：它必须声明输入输出、所需 capability、超时、成本、副作用和审批策略；某次调用的具体 `WorkspaceScope` 来自可信 Runtime Context，而不是注册到全局 Tool Definition。Skill 是一套可版本化配置，把 Instructions、可用 Tools、Context 策略和输出合同组合起来；它不是一段可以任意执行的隐藏代码。Application Service 管业务事实、事务、授权和幂等，Tool Adapter 只能在这些正式边界上调用它。Harness 选择本次 Run 的 profile 和测试替身，再把它交给同一个 Runtime；Harness 自己不能写 loop。

我用一个判断方法区分它们：如果代码在决定“业务数据是否允许改”，它属于 Application Service；如果它在执行一个已授权的 typed capability，它属于 Tool/Adapter；如果它在选择本次允许哪些能力和 Context，它属于 Harness；如果它在推进 Step、Event、Budget 和停止原因，它属于 Runtime。

## 2. Action 不是授权

模型返回的 `ToolAction` 只是一项建议。name/version 即使匹配 Prompt，也必须重新经过可信 Registry；arguments 必须经过 typed Schema；capability、WorkspaceScope、Budget、deadline、cost、side-effect 和 approval 都从服务端 Runtime Context 与 Tool Definition 派生。

因此未知 Tool 仍可留下“曾被请求”的审计事实，但不会得到执行 Step。deny 或 approval_required 也不会伪造一个“执行过”的 Tool Step。只有静态 allow 后，Runtime 才创建 Tool Step 并交给 Executor。

## 3. Observation 为什么还不是 Evidence

Observation 是 Tool 返回值的有界、可追溯、归一化表示；它带来源版本、时间、locator、content hash 和 normalizer version。这只能回答“Tool 当时返回了什么”，不能证明来源许可有效、locator 可访问、授权仍成立、内容已去重或 Claim 得到支持。

所以 Context Compiler v1 仍把 Observation 当不可信 USER data。它可以包含 prompt injection，但不能修改 system instructions、权限或 Tool surface。真正的 Evidence 还需 Day 4 的授权、规范化、去重、locator 校验与 Evidence ledger。

## 4. 什么时候继续，什么时候停止

L1 只允许一次 Action、一次 Tool、一次 Observation 和一次最终回答。Action 通过 Schema/scope/policy 才继续；执行成功且 Observation 通过边界校验，才进入最终 Model Step。

在当前 L1 的已实现 safe point，参数无效、未知/越权 Tool、capability 不足、需要审批、Tool 失败、Provider 失败、预算耗尽、deadline 或取消都必须收敛为稳定 Event 和 stop reason；Runtime 不让模型换个名字绕过，也不做隐式重试。Tool 完成、取消和硬超时竞争时只允许一个结算结果，迟到结果不能覆盖已提交终态；非零实际 Tool 成本在 completed Event、Tool Step、Run state 与 Tool 投影间保持同一数值且只计一次。L2 的多轮继续还要增加 max steps、no-progress、重复 Observation 与跨轮预算轨迹，这是今天第 2 步，当前没有提前实现。

## 5. 为什么生产与 Harness 必须共用 Runtime

如果 Harness 自己执行 Action→Tool→Observation，测试可能通过，但生产的 Event、预算、取消、持久化和错误语义仍是另一套。第一步把 L0 生产链与 L1 Harness 都放进同一个 `UnifiedAgentRuntime` dispatch；单元/Harness 只通过正式 Port/构造边界注入 Provider、Tool、manifest/Event store、控制与时间等 test doubles，并由服务端物化可信身份。真实 PostgreSQL 用例直接调用同一个内部 `ToolL1Runtime` 并使用 SQL ports，只验证 Runtime/持久化合同，不冒充生产 Application/Job/Worker 入口。

这并不等于真实 Web 已接通。Fake Tool 只证明调用边界、轨迹和错误可重复；正式 `web` 模式仍须等第 3 步的真实 Adapter 和 SSRF/egress 合同通过。

## 6. 安全复盘

- Event、Trace 和 Tool 表不保存 raw arguments、原始 Tool/Provider body、原始副作用幂等键、Secret、Runtime Context 或 chain-of-thought；原始幂等键只在受控内存合同中从 `repr` 隐藏并传给 Adapter，服务端持久边界只保留完整性摘要，普通 Trace 不返回参数或幂等键 digest；
- 参数只保存 SHA-256 和结构大小摘要。未知 Tool 的参数键也由模型控制，所以连键名都不写入审计摘要；
- ToolCall/ToolRun 使用 Workspace/run 复合外键，ToolRun actor 还必须匹配 AgentRun 的 workspace/user，执行 Step 可空且唯一；Event batch 与 Tool/Step/Run 投影在同一事务原子提交，并校验 call/run/workspace/actor/Step/trace/Observation correlation；
- Observation 有硬大小、来源数量、时间、scope 和 digest 校验；locator 拒绝 userinfo、query、fragment 和控制字符，放不进 Context budget 时 fail-closed；
- Tool 完成/取消/硬超时竞态、非零成本守恒、稳定 error code、Observation envelope digest 与幂等键 hash 都有 fail-closed 合同；
- ToolCall/ToolRun 是 Run-owned operational audit projection，当前 `RESTRICT` 阻止普通删除隐式清空；显式 Run purge、最小 security audit 留存期限及恢复/备份测试，以及旧 queued-cancel/unrecoverable terminalizer 的最终 revision 投影仍未实现，是生产 L1 前置 open 项；
- approval_required 只停止并记录请求；持久 interrupt/resume 与重复审批幂等属于 Day 5；
- Fake Tool 无网络、Shell、数据库和 Secret 能力，不会伪装真实来源。

## 7. 验收记录

第一步已完整执行根 [README 的统一验证](../../README.md#统一验证)，没有用定向绿色子集替代：

- 锁定依赖安装通过；Python 262 个文件的 format check、Ruff、mypy 256 个源文件、wheel/sdist build 均通过；`uv audit --locked` 检查 72 个包，没有已知漏洞或 adverse status；
- 打开 PostgreSQL、Redis、MinIO 强制开关后，全量 Python 测试为 `808 passed`，没有 skip/xfail；最终安全、原子性与可靠性定向集另有 `99 passed`；
- disposable PostgreSQL 上的 fresh Alembic upgrade/check/downgrade/upgrade 与真实 L1 往返通过；真实 PostgreSQL 用例覆盖成功投影、跨 actor 拒绝、成本篡改整批回滚、Observation/Trace 篡改拒绝和最终 revision 一致；
- OpenAPI 二次生成一致，API contract typecheck 通过；Web format/lint/typecheck/production build 通过，Vitest 为 `43 passed`，`pnpm audit --audit-level high` 无漏洞；
- Playwright 的会话、停止与刷新恢复三条真实浏览器旅程为 `3 passed`；
- 受控工作树路径与完整 41-commit 历史的 Gitleaks 扫描均无泄漏，`git diff --check` 与暂存区复核通过；本步没有新增第三方运行时依赖或许可证例外。

失败修复没有靠删测试、放宽 Schema 或把适用项写成 `N/A` 绕过。实际关闭了执行中取消/deadline/硬超时竞态、已知 Model/Tool 成本守恒、确定转移的 Event batch 原子性、PostgreSQL `CHECK` 的三值逻辑绕过、Tool/Step/Run 投影关联、Observation→Context→Trace 关联，以及写副作用结果未知时的保守终态。

残余边界也不隐藏：生产 Application/Job/Worker 尚未启用 L1，因此还没有正式 L1 用户旅程和干净 CI 证据；显式 Run purge、最小 security audit 留存/恢复/备份、旧 queued-cancel/unrecoverable terminalizer 的 revision 投影，以及真实 Web locator 的语义级 token/PII 清洗和 SSRF/egress 防护，都是相应后续入口前的硬门禁。

当前阶段结论：只完成第 1 步的本地验收；第 2～5 步未开始，D3-02 仍为 `thin_slice`，Day 3 总门禁仍未关闭。
