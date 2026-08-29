# Day 8 执行计划：SEC Verified Agent L5、监控与 Durable HITL

> 制定日期：2026-08-28
>
> 计划基线：[Day 1～Day 10 主计划](../master-plan.md) 2.1.8 Day 8
>
> 能力边界：[Day 1～Day 10 目标能力矩阵](../feature-matrix.md) D8-01～D8-08
>
> 架构决策：[ADR 0003](../adr/0003-unified-evidence-model.md)、[ADR 0007](../adr/0007-sec-disclosure-financial-fact-verification.md)
>
> 详细设计：[SEC Verifier、Monitor 与恢复设计](../sec-verification-monitor-design.md)
>
> 当前状态（2026-08-29）：Step 1～3 已在 `feat/day-8` 工作树实现，D8-01～D8-05 为 `implemented_pending_verification`；Step 4～5 与 D8-06～D8-08 仍为 `planned`。Step 3 新增正式 Monitor/rule/watermark/run/Case/Evidence 模型、迁移、Schedule/Worker 接线和幂等完成短路。本机 CI 等价测试主运行 `1186 passed, 3 failed`，三项失败均由本机 Compose Elasticsearch 端口配置错误导致，改为实际映射端口后 `3 passed`；合并覆盖数据达到总体分支覆盖率 `80.15%`、核心合集 `85%`，完整迁移往返/drift、Web 格式/lint/typecheck/test/build 均通过。此前远端 PostgreSQL CI 的真实根因是 Elasticsearch `refresh=wait_for` 首次写入可能无限等待，当前工作树已把实际 bulk 写/删与 readiness probe 改为 `refresh=true`，尚无修复后的远端 CI。D8 专属 Workbench、frozen eval、完整故障演练、PR/main CI 与所有者复核仍缺，不能把 Step 3 或 Day 8 写成完成。

## 1. 进入基线与本日边界

Day 7 已由 [PR #11](https://github.com/hrw991009/industry-intelligence-platform/pull/11) 合入 `main`，功能 head 为 [`6a25ab2`](https://github.com/hrw991009/industry-intelligence-platform/commit/6a25ab2b2ae93a3bf8c76dfec31c0f70558f0343)，合并提交为 [`ae33b98`](https://github.com/hrw991009/industry-intelligence-platform/commit/ae33b98784b92e88fff6c3f9f808678ea7a70743)。push/PR 对应的 CI [`33155746624`](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/33155746624) 与 [`33155965096`](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/33155965096) 各有 7 个适用 Job 通过；合并提交 CI [`33156337673`](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/33156337673) 最终为 6/7 Job 通过、Browser E2E 失败。失败用例是 `tests/e2e/app-shell.spec.ts:596` 的 Day 2 会话创建/停止/恢复旅程，在点击“重命名会话”按钮时达到 45 秒超时；同一 run 的 SEC Workbench locator 用例通过。该失败保留为待修门禁，不在规划文档中推断成已解决或简单归类为 flaky。

以上事实只证明 Day 7 代码已合并且 PR 检查通过。Day 7 的 `sec-tool-v1` 仍是 frozen deterministic contract，不是 live/model/public benchmark；D7-02 仍有 ranking/table/Citation 缺口，D7 其余项仍待真实依赖、正式浏览器、中英 paired、main CI 与所有者复核。Day 6 的 `22/24`、bulk watermark/post-gap 和 live SEC 缺口，Day 5 的 SEC fixture 浏览器恢复链，以及 Day 4 核心覆盖率 90% 债务均继续登记为 Day 10 发布硬门。

Day 8 只交付两个相连的业务闭环：

1. 对 Day 7 的 SEC L4 draft 做可判定的 Claim 级核验，并在必要时最多执行一次定向修订；
2. 监控新 filing/amendment，经持久审批创建订阅，生成可幂等恢复的差异 Case。

Day 8 不引入第二套 Runtime、Research graph、Evidence/Citation、计算器、审批、调度或通知账本；不实现多 Agent L6，不提前导入 Day 9 公开 benchmark，也不扩展到投资建议、估值、交易或审计意见。

## 2. 复用边界与不变量

| 现有正式能力 | Day 8 复用方式 | 禁止做法 |
|---|---|---|
| `UnifiedAgentRuntime`、Research L4 graph、Checkpoint CAS | 在同一 typed graph 追加 `verify → bounded revise → finalize` 节点和版本映射 | 新建 verifier loop、finance runtime 或图外 Provider 调用 |
| Evidence/Claim/Citation、`FinancialScope`、Calculation Evidence | Verifier 从 PostgreSQL 重载正式 identity、lineage 与授权后逐 Claim 判定 | 直接相信 draft、Context 文本或模型自报的引用/计算 |
| Tool Registry/Executor 与 `sec-l4-v1` | revise 只允许原 Scope、原 allowlist 内的定向 read/recalculate | Verifier 扩大公司、accession、`as_of`、预算或 Tool surface |
| ApprovalRequest/Decision 与 side-effect ledger | `monitor.subscribe@v1` 复用同一持久审批和幂等副作用协议 | 创建 Monitor 专用内存审批或让模型直接写订阅 |
| Schedule/Occurrence/Job/Outbox、Dispatcher、Worker、Beat | Beat 只物化 occurrence/Job/Outbox，Worker 执行同步、diff 和 Case | Beat 直接访问 SEC、执行模型或发送通知 |
| SEC sync、filing diff、Workspace 授权 | Monitor 通过 watermark 发现新 source version，再复用 diff/Evidence | 用当前 live 列表覆盖历史水位或跨 Workspace 复用订阅 |

必须始终成立：

- `verified/partial/conflict/insufficient_evidence` 是业务核验状态；`completed/failed/cancelled/budget_exhausted/...` 属于 Runtime stop reason，两者分列保存、传输和展示。
- `verified` 只在所有关键 Claim 均有可解析 Evidence、scope/计算一致、无未解决冲突且 required coverage 满足时产生；Finalizer 和 UI 都不能提升状态。
- revise 最多一次，只能响应 typed issue 里的允许动作；没有新 Evidence、预算不足、deadline/取消到达或相同 issue 重现时直接停止。
- filing、网页、表格、Tool Observation 和 Memory 都是不可信数据，不能改变 system instructions、Workspace、Scope、Tool allowlist、预算、审批或停止规则。
- Monitor、Case 和通知都有稳定幂等键；重复 tick、Job、decision、resume 或迟到结果不能产生第二个业务副作用。

## 3. 五步实施计划

| 步骤 | 能力映射 | 实现范围 | 完成证据 |
|---|---|---|---|
| 1. Claim Verifier 与四种业务状态 | D8-01、D8-02 | 定义 `VerificationReport`、typed issue/规则和 Claim 判定；从正式 Evidence/Calculation/Citation 重载 company/accession/`as_of`、support、unit/period/context、coverage/conflict | 规则单测、API/Event/Trace contract、四状态决策表；verified false support=0，状态与 Runtime stop reason 不混淆 |
| 2. One-revise L5 与不可信输入防线 | D8-03、D8-04 | 在唯一 Research graph 追加 verify/revise/finalize；最多一次 targeted retrieve/recalculate，冻结 Scope/toolset/budget；覆盖 filing/table/web 间接注入 | max-revise、no-progress、deadline/cancel/budget、injection/fake-tool tests；未授权写 Tool=0，攻击不改变 trusted context |
| 3. Monitor、watermark 与幂等 Case | D8-05 | 建立 Monitor/rule/watermark/Case 正式模型和迁移；复用 Schedule/Occurrence/Job/Outbox、SEC sync 与 filing diff，新 filing/amendment 只生成一个 Case | 时区、misfire、watermark、429、lease、dead-letter、base/amendment 和重复 tick 的 PostgreSQL/Worker 测试；Case 可反查两份 accession/Evidence |
| 4. `monitor.subscribe@v1`、Durable HITL 与 Workbench | D8-06、D8-07、D8-08 部分 | 模型仅创建写请求；当前用户 allow/deny/timeout 后落库。验证跨刷新/Worker 重启、Checkpoint CAS、重复 decision/resume 与取消竞态；UI 读取正式 API/Event/Trace | allow 一次写入、deny/timeout 零写入、重复副作用=0；L4/L5/Monitor 恢复成功 100%；浏览器展示 issue、revise diff、Approval、Monitor、Case 时间线 |
| 5. `sec-verification-v1`、A2/A3/A4 与 Day 8 收口 | D8-08 | 冻结 support/refute/conflict/no-answer/revise/wrong-period/injection/approval/recovery/notification cases；同 manifest、数据、Scope 和预算比较 A2/A3/A4 | deterministic/fault/security JSON+Markdown；A3 复杂题有净收益且简单题退化 ≤2pp，A4 单独报告写入/恢复；三层 CI、所有者复核与遗留债务台账 |

步骤按数据合同和恢复依赖顺序执行。每一步都要同时提交 domain/application、迁移或契约、正式 Adapter 装配、负向测试和可反查的 Workbench 数据；不允许先做展示页再用 Mock trajectory 补业务链。

## 4. 分步验收口径

### Step 1：Claim Verifier 与四种业务状态

Verifier 输出至少包含 report/version、run/scope identity、每个 Claim 的 `supported/refuted/conflicting/insufficient` 判定、Evidence/Citation/Calculation refs、规则 issue、coverage 和最终业务状态。规则负责可判定字段与阈值，模型只能提供受 schema 约束的 Claim 分解或解释，不得自行宣布 `verified`。

### Step 2：One-revise 与安全

每个 issue 明确 `code`、severity、affected claim、expected/current refs 和允许动作。只有 `missing_evidence`、`citation_unresolvable`、`calculation_input_missing` 等可修复问题可以触发定向步骤；错误 scope、未授权来源、依赖失败、冲突无法裁决或安全攻击不能通过“再问一次模型”消失。

### Step 3：Monitor 与 Case

Monitor 由 Workspace、filer、forms、规则版本、schedule/timezone 和 watermark 定义。watermark 只在覆盖完整且 Case/Outbox 原子提交后推进；失败可重试但不跳过 source version。Case 保存 trigger、base/current accession、diff version、Evidence refs、状态和通知幂等键，不能仅保存一段模型摘要。

### Step 4：HITL、恢复与 Workbench

`monitor.subscribe@v1` 是唯一新增写 Tool。请求先生成 ApprovalRequest，服务端以当前认证用户和最新 membership 决策；模型不能提供 approver、role 或 decision。恢复时先读取 Checkpoint、Approval/Decision、Tool/Calculation/Case side-effect ledger，再决定是否继续，迟到 Worker 结果受 run epoch/revision/fencing 拒绝。

### Step 5：评测与收口

`sec-verification-v1` 不复用模型自报标签作为 gold。确定性规则从 frozen Evidence、calculation program、expected state 和最终数据库事实重算。A3 只测 mandatory verifier + one revise 对 A2 的增益；A4 测 Monitor/HITL/恢复正确性，不把简单问答分数作为其主要收益。

## 5. Step 1 实现与证据

当前工作树已经落地 `research` 所有的确定性 `sec-claim-verifier-v1`：从正式 Research draft、Claim relation、Evidence availability、SEC locator/hash、`FinancialScope` 和 Calculation lineage 重新判定，不相信 draft 自报状态。输出包含 append-only report revision、逐 Claim verdict、typed issue、Evidence snapshot、coverage、`verification_status` 与独立的 `runtime_stop_reason`。

PostgreSQL 新增 `research_verification_reports`、`research_verification_claims`、`research_verification_issues` 三张正式表；仓库在同一事务中先落父报告再落 Claim/Issue，并在写入前校验 run/draft/graph、Claim revision、Evidence revision/status/hash。迁移同时修复既有 `research_runs.current_node` 模型为 40、数据库仍为 32 的 schema 漂移，upgrade/downgrade 均有明确类型转换。

正式只读 API 为 `GET /api/v1/workspaces/{workspace_id}/research-runs/{research_run_id}/verification-report`。Event/Trace 注册了安全的 verification 完成投影字段，但本步不伪造 graph 事件；事件产生、one-revise 和 finalize 由 Step 2 接入唯一 Research graph。

本地证据包括 14 条聚焦规则/API 测试、真实 PostgreSQL append-only/stale-revision 测试、完整 Alembic upgrade→downgrade→upgrade 与 autogenerate drift 检查、确定性 OpenAPI 生成，以及 `1089 passed, 85 skipped` 的完整 Python 回归。回归同时暴露并修复 `sec-tool-v1` Markdown 在 Windows 写 CRLF、仓库基线为 LF 的跨平台字节漂移；只固定输出换行，不修改报告内容或评测分母。以上证据只支持 D8-01/D8-02 的 `implemented_pending_verification`，不替代远端 CI、正式浏览器、frozen eval 或 owner review。

## 6. Step 2 实现与证据

唯一 Research graph 已升级为 `research-l5-graph-v1`/state schema 2，并在原 `draft` 后追加 `verify → optional revise → finalize`。普通非金融 Research 走 `verify → finalize` 且不伪造业务核验状态；Financial Research 必须调用生产装配的 `ResearchVerificationService`。Verifier report 通过正式 Event/Trace 发射，业务状态与 Runtime stop reason 继续分离。

revise 只选择一个 repairable typed issue，并优先按 `recalculate`、再按 `targeted_retrieve` 处理。Tool 名称、版本和参数由服务端从可信原问题、issue refs 或已重载的 Calculation locator 推导；模型只看到单 Tool schema 和 exact action，任何名称/版本/参数偏离在 Tool 执行前以 `verification_action_mismatch` 拒绝。原 Workspace、`FinancialScope`、Tool allowlist、Budget、deadline、取消与审批边界不变；一次 Tool 后必须结束，重复 Observation 或无新 Evidence 直接保留 non-verified，不创建 revision 2。

Research Draft 改为 `(research_run_id, revision)` append-only；revision 2 的 Draft/Claim ID 稳定，Claim repository 对相同命令返回已有事实、对冲突重试 fail closed。L5 Checkpoint 保存 report revision/status、issue/action/observation digest，并用同一 graph router 校验下一节点；旧 L4 payload 不猜字段而明确拒绝恢复。迁移降级会先删除 L5 report/draft revision，再把 run 映射回 L4 schema，避免已有 revision 2 时唯一约束失败。

聚焦测试覆盖成功 retrieve→reverify、targeted retrieve/recalculate no-progress、预算不足不 revise、最多一次 revise、filing 注入诱导未注册写 Tool、L4 hard-stop 恢复与 Claim PostgreSQL 重试幂等。CI 查漏同时补齐 `ResearchDraftResponse.revision` 的 Web fixture，并把浏览器 Research 驱动升级为显式校验 L5 graph/state/finalize 与 10 个正常路径节点；现有 8 条 Chromium 旅程全量通过。完整真实依赖门禁、迁移和覆盖率数字见本页顶部；新的远端分支 CI、D8 专属 security frozen set、A2/A3 对照和 owner review 尚缺，因此 D8-03/D8-04 只能是 `implemented_pending_verification`。

## 7. Step 3 实现与证据

`disclosures` 已新增 Workspace scoped 的 Monitor、typed rule、append-only watermark、Schedule occurrence run、幂等 Case 和 Case→Evidence 双侧关联。规则只接受冻结版本和显式 comparator/threshold；模型 prose 不进入执行字段。Evidence 继续使用统一表和 API，但来源约束改为 Agent Tool 三元组或 Monitor Case 二选一，Case 不复制一套私有 citation 数据。

FastAPI 与 Beat 通过同一个 observer 组合器，在既有 Schedule/Occurrence/Job/Outbox 事务内同时支持行业采集与 SEC Monitor 投影。固定 Worker registry 新增 Monitor handler，并复用完整 disclosure resources；官方 point-in-time selection、Workspace filing import、XBRL sync 和 `sec-filing-diff-v1` 完成后才执行 typed rule。部分覆盖、官方源/索引依赖失败不会进入 commit；Case/Evidence 与新 watermark 在一个 PostgreSQL 事务中写入。若业务提交成功而 Job 结算前中断，同一 Job 重投会读取已完成结果，不再次分析或推进 watermark。

本地证据覆盖 amendment/base 选择、覆盖不完整时零 import/diff、缺 baseline fail closed、Worker retryable/permanent 错误映射、真实 PostgreSQL occurrence→run→watermark 原子链和完成后重投幂等。数据库测试还验证 Case 绑定两个 filing，并能通过 `baseline/target` Evidence locator 反查两个 accession。完整 Alembic `upgrade→check→downgrade→upgrade→check` 通过；生成 OpenAPI 连续稳定，Web 因 Agent 来源字段可空而更新 fixture 后格式/lint/typecheck、87 tests、关键状态 coverage 和 build 通过。

CI 修复没有继续放宽业务超时：Elasticsearch bulk 写入和删除从 `refresh=wait_for` 改为 `refresh=true`，因为前者在新索引没有周期 refresh 时可能一直等待；CI readiness probe 使用相同真实写读语义。当前仍缺修复后的远端 CI、合法 live SEC、429/dead-letter/lease 的完整组合故障演练、正式 Monitor API/Workbench 和 Step 4 的持久订阅审批，因此 D8-05 只能是 `implemented_pending_verification`。

## 8. Day 8 完成定义

Day 8 只有同时满足以下条件才可关闭：

- D8-01～D8-08 均达到矩阵定义的 `complete`，不是仅有代码或页面；
- deterministic gate 中 fabricated source/accession/number/formula、future leakage、跨 Workspace、未授权写和重复副作用均为 0；
- `verified` false support 为 0，Citation resolvability 为 100%，recovery scenarios 成功率为 100%；
- A3 相对 A2 的收益/成本决定和 A4 的恢复结果有机器可比报告，不满足条件的策略已回退；
- 分支、PR、合并提交 CI 均通过，正式浏览器旅程和所有者复核完成；
- Day 5～7 遗留项仍在 Day 10 台账中逐项可见，没有被 Day 8 报告删除、改分母或伪写完成。

当前 Step 1～3 达到本地 `implemented_pending_verification`，不满足 Day 8 总完成条件。下一步进入 Step 4，接入 `monitor.subscribe@v1`、持久 HITL、恢复和正式 Workbench；不得用当前数据库模型替代用户审批或 UI/API 验收。
