# Day 3 学习日志：前四步有界 Tool Use、真实采集与安全 Text2SQL

> 更新日期：2026-08-17
>
> 计划基线：`docs/master-plan.md` 1.7.0 Day 3
>
> 当前结论：五步计划中的第 1～4 步已通过本地验收，本轮严格停在第 4 步。D3-01～D3-11 仍是 `thin_slice`，尚无对应干净 CI，Day 3 尚未完成。

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

L2 把继续权留在 Runtime，而不是交给模型自由自省：每轮模型只能返回严格结构化的 `tool_call` 或 `final`。只有新 Action 通过可信校验、未命中 no-progress guard，且剩余 Step、Token、费用、deadline 与取消仍允许下一轮时才继续。完全相同的 Action 在第二次执行前拒绝；不同 Action 若得到相同规范化内容，会先保存第二次真实执行事实，再以 `no_progress` 停止。这样既不伪造“没有执行”，也不把重复结果继续喂给模型制造无限循环。

在当前 L1/L2 的已实现 safe point，参数无效、未知/越权 Tool、capability 不足、需要审批、Tool 失败、Provider 失败、max steps、无进展、预算耗尽、deadline 或取消都必须收敛为稳定 Event 和 stop reason；Runtime 不让模型换个名字绕过，也不做隐式重试。Tool 完成、取消和硬超时竞争时只允许一个结算结果；非零实际 Tool 成本在 completed Event、Tool Step、Run state 与 Tool 投影间保持同一数值且只计一次。当前 L2 只用一个可信 Tool 的不同参数证明多轮控制，不能替代 Day 3 后续“至少两个不同 Tool”的验收。

## 5. 为什么生产与 Harness 必须共用 Runtime

如果 Harness 自己执行 Action→Tool→Observation，测试可能通过，但生产的 Event、预算、取消、持久化和错误语义仍是另一套。前两步把 L0 生产链与 L1/L2 Harness 都放进同一个 `UnifiedAgentRuntime` dispatch；单元/Harness 只通过正式 Port/构造边界注入 Provider、Tool、manifest/Event store、控制与时间等 test doubles，并由服务端物化可信身份。真实 PostgreSQL 用例直接调用同一个内部 `ToolL1Runtime` 或 `ToolL2Runtime` 并使用 SQL ports，只验证 Runtime/持久化合同，不冒充生产 Application/Job/Worker 入口。

第 3 步增加了真实 `industry.web_search:v1` Adapter，并证明它经过正式 Registry/Executor 后生成带 `[S1]` Citation 标识与 provenance 的 Observation；采集 Job 也已进入固定 Worker registry。它仍不等于聊天的生产 `web` 模式已启用：Conversation/Agent Job 尚未物化 L1/L2 Tool command，前端页面与错误交互也留在第 5 步。

## 6. 安全复盘

- Event、Trace 和 Tool 表不保存 raw arguments、原始 Tool/Provider body、原始副作用幂等键、Secret、Runtime Context 或 chain-of-thought；原始幂等键只在受控内存合同中从 `repr` 隐藏并传给 Adapter，服务端持久边界只保留完整性摘要，普通 Trace 不返回参数或幂等键 digest；
- 参数只保存 SHA-256 和结构大小摘要。未知 Tool 的参数键也由模型控制，所以连键名都不写入审计摘要；
- ToolCall/ToolRun 使用 Workspace/run 复合外键，ToolRun actor 还必须匹配 AgentRun 的 workspace/user，执行 Step 可空且唯一；Event batch 与 Tool/Step/Run 投影在同一事务原子提交，并校验 call/run/workspace/actor/Step/trace/Observation correlation；
- Observation 有硬大小、来源数量、时间、scope 和 digest 校验；locator 拒绝 userinfo、query、fragment 和控制字符，放不进 Context budget 时 fail-closed；
- Tool 完成/取消/硬超时竞态、非零成本守恒、稳定 error code、Observation envelope digest 与幂等键 hash 都有 fail-closed 合同；
- ToolCall/ToolRun 是 Run-owned operational audit projection，当前 `RESTRICT` 阻止普通删除隐式清空；显式 Run purge、最小 security audit 留存期限及恢复/备份测试，以及旧 queued-cancel/unrecoverable terminalizer 的最终 revision 投影仍未实现，是生产 L1 前置 open 项；
- approval_required 只停止并记录请求；持久 interrupt/resume 与重复审批幂等属于 Day 5；
- Fake Tool 无网络、Shell、数据库和 Secret 能力，不会伪装真实来源；真实 Adapter 只接受固定 host/字段和服务端 terms approval，不接受模型提供 URL、key 或许可开关。

## 7. 验收记录

前四步当前工作树已执行根 [README 的统一验证](../../README.md#统一验证)中适用于未提交工作树的全部门禁，没有用定向绿色子集替代。`api:check` 的最后一步要求生成物与当前 Git 提交完全相同，因本步有预期的 OpenAPI 变更，在提交前必然报告 diff；这里以连续两次独立生成一致、生成 diff 评审和 contract typecheck 验收，仍须由后续干净 CI 执行 `api:check`：

- Python 305 个文件的 format check、Ruff、mypy 297 个源文件、wheel/sdist build 均通过；`uv audit --locked` 解析 75 个包并检查 73 个第三方包，没有已知漏洞或 adverse status；
- 打开 PostgreSQL、Redis、MinIO 强制开关后，全量 Python 测试为 `890 passed`，没有 skip/xfail；Data Explorer 定向单元/真实 PostgreSQL 合同为 `44 passed`；
- disposable PostgreSQL 上的 fresh Alembic upgrade/check/downgrade/upgrade 与真实 L1/L2 往返通过；L2 PostgreSQL 用例持久化两次 ToolCall/ToolRun、六个 Step 与三份递增 Observation manifest，并核对累计费用、最终 revision 和安全 Trace；
- OpenAPI 二次生成一致，API contract typecheck 通过；Web format/lint/typecheck/production build 通过，Vitest 为 `43 passed`，`pnpm audit --audit-level high` 无漏洞；
- Playwright 的会话、停止与刷新恢复三条真实浏览器旅程为 `3 passed`；
- disposable PostgreSQL 证明同一事务物化 ScheduleOccurrence、Job、Outbox 与 CollectionRun，并验证并发手动触发收敛、Workspace 权限、游标、external ID/hash 去重和领域投影；完整 migration upgrade/check/downgrade/upgrade 无漂移；
- disposable PostgreSQL 还证明独立只读账号的连接测试、Schema/主键/索引/分页、安全聚合、QueryRun/Artifact 持久化和 Workspace 隔离；危险 SQL 留下稳定失败事实，账号绕过应用直接 DELETE 仍被数据库拒绝；
- 受控工作树路径与完整 43-commit 历史的 Gitleaks 扫描均无泄漏；新增的 `sqlglot==28.5.0` 为 MIT 许可、不发网也不执行 SQL，来源、威胁模型与负向清单见 [Text2SQL 安全复核](../security/day-3-text2sql-review.md)。

失败修复没有靠删测试、放宽 Schema 或把适用项写成 `N/A` 绕过。实际关闭了执行中取消/deadline/硬超时竞态、已知 Model/Tool 成本守恒、确定转移的 Event batch 原子性、PostgreSQL `CHECK` 的三值逻辑绕过、Tool/Step/Run 投影关联、Observation→Context→Trace 关联，以及写副作用结果未知时的保守终态。

残余边界也不隐藏：生产 Conversation/Agent Job 尚未启用 L1/L2，因此还没有正式聊天 Tool 用户旅程和干净 CI 证据；当前 L2 Scenario 只暴露一个 Fake Tool，真实行业 Tool 与 Text2SQL 尚未组成同一多 Tool 选择面；行业/数据库/图表 UI 与 Tool Inspector 未开始。显式 Run purge、最小 security audit 留存/恢复/备份、进程崩溃后的陈旧 QueryRun 对账和旧 queued-cancel/unrecoverable terminalizer 的 revision 投影仍是相应入口前硬门禁。

## 8. 第三步的知识突破

Provider Adapter 和 Tool Adapter 是两层：Provider Adapter 只负责固定外部协议、网络失败与上游响应归一化；Tool Adapter 再把这些业务结果放进 Tool 的 typed input/output、Workspace capability、预算和 Observation 合同。采集 Worker 可以直接调用同一个行业 Application Service，但不能因此绕过 Tool Runtime 后冒充聊天 Tool 轨迹。

调度原子性也不等于“一次 SQL”。本步最初在真实 PostgreSQL 暴露了 FK 插入顺序问题：ORM 不会因为两个 mapper 之间只有表级 FK 就自动保证 `ScheduleOccurrence` 先于 `CollectionRun`。正确做法是在同一事务内先 flush Job/Outbox/Occurrence 父事实，再加入 CollectionRun，最后仍只 commit 一次；后半段失败时四者会一起回滚。

另一个突破是“公开 API”不等于“可在任何商业产品中使用”。World Bank News 与 Alpha Vantage 的用途条件都要求显式复核，因此 readiness 同时表达技术配置与使用授权；缺批准时在发网前停止。TED 的公开复用、FederalRegister.gov 的非正式法律版本提示也进入来源合同，而不是只写在产品文案里。

## 9. 第四步的知识突破

这一步最重要的认识是：模型能输出“看起来像 SQL”的字符串，不等于系统拥有安全 Text2SQL。真正的路径是 `自然语言问题 → response_schema 约束的 typed ToolAction → generated SQL → AST policy → validated SQL → 数据库只读事务`。其中 response schema 只稳定模型回复形状；SQL 能不能执行仍完全由服务端 SchemaSnapshot、allowlist、预算和数据库权限决定。

第二个突破是 AST parser 也不是安全策略。SQLGlot 负责把 PostgreSQL SQL 变成可遍历结构并限定列，但 parser 本身会接受大量合法却不适合本产品的 SQL。项目必须显式拒绝 DML/DDL/COPY/CALL、递归 CTE、锁、危险函数、系统表和越界列；即使 AST 误放行，独立只读账号和 `SET TRANSACTION READ ONLY` 仍构成第二道硬边界。

第三个突破来自查询预算：聚合根节点可能只输出 4 行，但底层顺序扫描可以读取 250,000 行。只检查根 `Plan Rows` 会产生虚假的“低成本”结论，所以当前实现递归累计完整 EXPLAIN 计划的行数，并把 timeout、最大行数、计划成本和扫描行一起保存到 QueryRun。

Artifact 也不是把模型给的 ECharts JSON 原样透传。表格先做行/列/字节/hash 约束，图表再从可信列映射构造固定 option；模型不能提供 JavaScript、函数、外链或任意 option。Text2SQL Observation 只回传前三行和 Artifact 引用，而且仍是不可信 Context，不在本步冒充 Evidence。

当前阶段结论：完成第 1～4 步的本地验收；第 5 步未开始，D3-01～D3-11 仍为 `thin_slice`，Day 3 总门禁仍未关闭。
