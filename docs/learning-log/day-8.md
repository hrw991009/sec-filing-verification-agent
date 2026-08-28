# Day 8 执行计划：SEC Verified Agent L5、监控与 Durable HITL

> 制定日期：2026-08-28
>
> 计划基线：[Day 1～Day 10 主计划](../master-plan.md) 2.1.6 Day 8
>
> 能力边界：[Day 1～Day 10 目标能力矩阵](../feature-matrix.md) D8-01～D8-08
>
> 架构决策：[ADR 0003](../adr/0003-unified-evidence-model.md)、[ADR 0007](../adr/0007-sec-disclosure-financial-fact-verification.md)
>
> 详细设计：[SEC Verifier、Monitor 与恢复设计](../sec-verification-monitor-design.md)
>
> 当前状态：Day 8 仅完成规划，D8-01～D8-08 均为 `planned`，尚未修改 Day 8 代码。Day 7 已由 PR #11 合入 `main`；两组 PR 检查通过，但合并提交 CI `33156337673` 最终为 6/7 Job 通过、Browser E2E 失败，因此不能把 Day 7 写成已完成或已通过 main 门禁。项目所有者明确先完成 Day 8～Day 9 代码、Day 10 再统一查漏补缺；该排期只允许继续实施，不删除任何既有评测分母、失败记录或发布硬门。

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

## 5. Day 8 完成定义

Day 8 只有同时满足以下条件才可关闭：

- D8-01～D8-08 均达到矩阵定义的 `complete`，不是仅有代码或页面；
- deterministic gate 中 fabricated source/accession/number/formula、future leakage、跨 Workspace、未授权写和重复副作用均为 0；
- `verified` false support 为 0，Citation resolvability 为 100%，recovery scenarios 成功率为 100%；
- A3 相对 A2 的收益/成本决定和 A4 的恢复结果有机器可比报告，不满足条件的策略已回退；
- 分支、PR、合并提交 CI 均通过，正式浏览器旅程和所有者复核完成；
- Day 5～7 遗留项仍在 Day 10 台账中逐项可见，没有被 Day 8 报告删除、改分母或伪写完成。

当前只满足“计划已冻结”，不满足上述代码、评测和验收条件。下一步从 Step 1 开始，不提前实现 Monitor 或 UI 占位。
