# SEC Verifier、Monitor 与恢复设计

> 版本：`0.3.0`
>
> 日期：2026-08-29
>
> 状态：Step 1～2 已本地实现并等待新远端验证；Step 3～5 仍为规划
>
> 适用范围：D8-01～D8-08

## 1. 设计目标

Day 8 把 Day 7 的可解释 L4 draft 提升为两条可验收链路：

```text
SEC L4 draft
→ claim-level verify
→ optional targeted retrieve/recalculate (max 1)
→ finalize with business verification status

approved monitor
→ scheduled SEC sync from watermark
→ filing/amendment diff
→ idempotent Case
→ optional notification side effect
```

两条链共用 `UnifiedAgentRuntime`、PostgreSQL Event/Checkpoint、Evidence/Claim/Citation、Calculation、Approval/Decision、side-effect ledger、Schedule/Occurrence/Job/Outbox 和 SEC sync/diff。新增代码应落入既有 bounded context；只有 Monitor/Case 缺少业务所有者时才在 `disclosures` 内增加聚合，不创建横跨所有模块的 Day 8 service。

## 2. 聚合与事实所有权

| 事实 | 所有者 | 不变量 |
|---|---|---|
| Verification report/issue | `research` | 绑定 run、graph/checker version、FinancialScope、Claim/Evidence/Calculation refs；append-only revision |
| 业务核验状态 | `research` | 只能由确定性聚合规则产生；不覆盖 Runtime terminal/stop reason |
| Monitor/rule/watermark | `disclosures` | Workspace scoped；规则和 schedule versioned；watermark 单调且可解释 |
| Disclosure Case | `disclosures` | 绑定 monitor、source version、base/current accession、diff/Evidence；稳定幂等键唯一 |
| Approval/Decision | 既有 `research` 审批链 | 当前用户决策；一个请求一个有效终态；模型不拥有 decision |
| Schedule/Occurrence/Job/Outbox | 既有 `jobs` | 创建与业务投影同事务；lease/fencing 控制并发 Worker |
| Notification receipt | side-effect ledger/对应 Adapter | request/destination/payload hash 唯一；迟到结果不能覆盖终态 |

Milvus、Elasticsearch、Redis、浏览器状态和 OpenTelemetry 都不是上述业务事实源。

Step 1 已按上述所有权落入既有 `research` bounded context：正式 PostgreSQL 保存 report/Claim/issue 的 append-only revision，Verifier 通过现有 Evidence service 重载实际 availability，并通过只读 API 暴露最新报告。当前只注册 verification Event/Trace contract；Event 发射和 graph/checkpoint 连接属于 Step 2，不能由 API 手动伪造。

## 3. Verifier contract

### 3.1 输入

Verifier 只接受服务端从 PostgreSQL 组装的 typed snapshot：

- `run_id`、graph/checkpoint revision、runtime/prompt/tool/context/retrieval/calculator versions；
- `FinancialScope`：Workspace、CIK、accession、form、report period、`as_of`、amendment policy；
- draft Claim 列表和每条 Claim 的 Evidence/Citation/Calculation refs；
- required coverage、budget/deadline/cancel snapshot；
- 允许的 read/recalculate action 集合。

draft 文本、Evidence content、filing/table/web 内容均标记为 untrusted data。模型输出必须经 schema 校验，且不能覆盖以上 trusted 字段。

### 3.2 规则与 issue

每条关键 Claim 至少执行：

1. scope identity：company/CIK/accession/form/period/`as_of` 一致；
2. support：Evidence 明确支持、反驳或不足，不能仅靠语义相似；
3. source/Citation：locator 可解析，source snapshot/version/hash 可重载；
4. calculation：派生数字可重算，operand、operator、unit/scale/rounding 一致；
5. temporal/authorization：无 future leakage，Workspace/current import 仍授权；
6. coverage/conflict：required sub-question 已覆盖，互斥 Evidence 未被静默丢弃。

`VerificationIssue` 至少包含：

```text
issue_id, code, severity, claim_id,
expected_refs, observed_refs,
repairability, allowed_action,
details_digest
```

`details_digest` 只保存可审计摘要，不复制敏感原文或 chain-of-thought。issue code 是版本化枚举；未知 code fail closed，不能被当成可修复问题。

### 3.3 业务状态决策表

| 状态 | 必要条件 | 禁止条件 |
|---|---|---|
| `verified` | 所有关键 Claim supported；Citation/Calculation 可重载；coverage 满足 | 任一 unresolved conflict/refute/insufficient、future/permission/identity 错误 |
| `partial` | 至少一个关键 Claim supported，且明确列出未覆盖部分 | 把未覆盖 Claim 隐藏或表述为整体 verified |
| `conflict` | 存在两个仍有效且不能按规则裁决的 Evidence/Claim | 静默选择更符合 draft 的来源 |
| `insufficient_evidence` | 无足够证据形成关键结论，或可信依赖缺失 | 用模型常识、当前数据或扩大范围补成 verified |

Runtime `failed/cancelled/budget_exhausted/dependency_failed` 可以与最后一个业务状态同时存在。API、Event、Trace、导出和 UI 分别提供 `verification_status` 与 `runtime_stop_reason`。

## 4. Bounded revise 状态机

```text
draft_ready
→ verify(revision=0)
  ├─ no repairable issue → finalize
  ├─ repairable + budget/deadline/cancel allow
  │  → targeted retrieve or recalculate
  │  → revise(revision=1)
  │  → verify(revision=1)
  │  → finalize
  └─ unsafe/no-progress/dependency/runtime stop → finalize non-verified
```

Guard 必须同时检查：

- `revision_count < 1`；
- issue code 在 allowlist，action 参数由 issue refs 推导而不是自由生成；
- Workspace、CIK/accession、`as_of`、amendment policy、toolset 不变；
- 剩余 step/token/cost/deadline 足够，且未取消；
- action/result digest 未在本轮执行，避免相同 Observation 循环；
- write Tool 永不属于 revise action。

Checkpoint 在每个成功节点后保存 graph version、verification revision、issue/action digest 和 side-effect refs。旧 schema 显式迁移或拒绝恢复，不能猜字段。

Step 2 已按此状态机落入唯一 Research graph：`research-l5-graph-v1` 使用同一个条件路由函数生成运行时 successor 与 Checkpoint `next_node`，避免恢复路径另写一套顺序。Verifier report、issue/action/observation digest 进入 state schema 2；Draft revision append-only，Claim ID 稳定且 repository 支持一致重试。旧 L4 run ledger 仍可读取，但旧 Checkpoint 因缺少 L5 verification block 被明确拒绝恢复。

targeted retrieve 的 query 只由可信原问题、issue code 和 typed refs 组成，不读取 filing 指令或模型自由生成的 Claim 文本；recalculate 只从 issue 指向且重新授权加载的 `FinancialCalculationLocatorV1` 重建 operator、operand Evidence、decimal 与 rounding。模型返回的 action 必须与服务端 action digest 完全相同，并且只允许一个附加 Tool call；无新 Evidence、重复 Observation、预算不足或取消时不创建 revision 2，也不提升业务状态。

## 5. 不可信输入与 Tool 安全

- Context Compiler 保持 system/trusted runtime/untrusted data 分层；filing 内的“忽略前文”“调用工具”“修改订阅”等内容只作为 Evidence content。
- Tool 参数 DTO 不接受 host、URL、Workspace、Budget、ApprovalDecision 或任意代码；这些值由可信 Runtime Context 注入。
- read Tool 仍执行 accession/`as_of`/Workspace 重载；`monitor.subscribe@v1` 未获 allow 时不得创建 Monitor、Schedule 或 Outbox。
- 恶意超长表格先经 token/row/cell/response 大小限制，截断进入 manifest；不能通过内容扩大预算。
- Trace 保存规则结果、hash 和稳定错误，不保存原始 chain-of-thought、Secret 或不必要的 filing 原文。

安全 suite 同时报告 benign utility 与 attack success。单纯拒绝所有外部内容虽然可降低攻击率，但不能作为合格实现。

## 6. Monitor、watermark 与 Case

### 6.1 Monitor

Monitor 至少保存：

```text
workspace_id, owner_user_id, filer_id,
allowed_forms, rule_set_version,
schedule_id, timezone, status,
watermark_source_version, watermark_accepted_at,
created_from_approval_id, optimistic_revision
```

规则初始冻结为 form match、new filing/amendment、选定 fact material change 和选定 section change。阈值、unit/scale、period comparator 和 diff version 显式保存；模型描述不能直接成为可执行规则。

### 6.2 执行与水位

```text
Beat tick
→ ScheduleOccurrence + Job + Outbox (one transaction)
→ Dispatcher lease/fence
→ Worker syncs official source from prior watermark
→ point-in-time/source-version validation
→ diff + rule evaluation
→ Case + side-effect/outbox (one transaction)
→ advance watermark only through proven coverage
```

部分覆盖、429、dependency failure 或 dead-letter 时不越过缺口推进 watermark。misfire 按既有 Schedule policy 补跑或合并 occurrence，但不得以当前 snapshot 冒充错过时点的数据。

### 6.3 Case 幂等

Case 幂等键至少由 `workspace + monitor + trigger source version + rule version + comparison pair` 组成。Case 保存 base/current accession、source snapshot/version、diff artifact、matched rule、Evidence refs、verification status 和通知状态。重复 Worker、手动重放或 amendment 重现返回现有 Case；不同规则或新 source version 可以产生新 Case。

## 7. HITL 与恢复

`monitor.subscribe@v1` 的执行协议：

1. Tool call 经 Registry/Executor 校验后创建 ApprovalRequest，并把 run 暂停在 Checkpoint；
2. API 以当前 Access Token、Session 和最新 Workspace membership 校验 allow/deny；
3. `allow` 在一个业务事务中创建或返回幂等 Monitor、Schedule、side-effect ledger 和 resume Job；
4. `deny/timeout` 写 Decision 和终态 Event，不创建 Monitor/Schedule/Outbox；
5. 重复 decision 返回既有结果，冲突 decision 明确拒绝；迟到 Worker 受 checkpoint revision/run epoch/fence 阻止。

恢复测试必须覆盖 API 刷新、Worker hard stop、lease 过期、决定与取消竞态、Tool/Calculation 已完成但 Event 未投影、Case 已写但通知未知等阶段。恢复前先读账本和最终业务表，不能依据内存或 Trace 猜测。

## 8. API、Event 与 Workbench

正式 API 至少提供：

- verification report/issue/revise diff 的列表与详情读取；
- ApprovalRequest/Decision 读取和当前用户决策；
- Monitor 创建结果、列表、详情、暂停/恢复/删除的明确权限合同；
- Case 列表、详情及 base/current filing、diff、Evidence/Citation 导航。

SSE/Event 增加版本化 verification/approval/monitor/case 事件，但继续使用统一 envelope 和单调 sequence。Workbench 只从正式 API/Event/Trace 重建，刷新后能恢复相同状态；四种业务状态使用文字与语义图标，不只依赖颜色。

## 9. `sec-verification-v1` 与门禁

冻结集至少覆盖：support、refute、conflict、insufficient、missing Citation、wrong accession/period/unit、不可重算数字、one-revise success/no-progress、indirect injection、approval allow/deny/timeout、重复 decision/resume/tick、Worker hard stop、amendment Case 和重复通知。

每个 case 固定 Scope、source/fixture hash、Evidence/Calculation gold、allowed/forbidden actions、expected verification status、Runtime stop reason、最终数据库状态和预算。A2/A3/A4 使用同一 case/data/scope/budget；A4 的主要指标是审批、状态恢复和副作用正确性，不与问答准确率平均。

Day 8 硬门：

- Citation/source identity resolvability 100%；
- fabricated source/accession/number/formula、future leakage、跨 Workspace、未授权写、重复副作用均为 0；
- verified Claim false support 为 0；
- recovery scenarios success 为 100%；
- A3 简单题相对 A2 退化不超过 2pp，并在复杂场景有净收益；
- deterministic、offline、live/model 证据继续分报，Day 8 不借公开 benchmark 或单次 live run 替代合同门禁。

## 10. 实施顺序

实现严格按 [Day 8 五步计划](learning-log/day-8.md) 推进。Step 1～2 已完成本地实现、真实依赖全量测试与 PostgreSQL/迁移验证；修复后的远端 CI、正式 security frozen set 和 owner review 尚缺。下一步只实现 Monitor、watermark 与幂等 Case，不提前创建订阅 HITL 或页面。每步结束同步主计划、能力矩阵、评测报告边界和实际 CI/验证证据。
