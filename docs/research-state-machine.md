# Research L3/L4 状态机与恢复边界

> 版本：1.4（Day 5 Step 5 本地实现）
>
> 更新日期：2026-08-25
>
> 当前能力：Evidence Research L3 已完成；Durable Research L4 为
> `implemented_pending_verification`
>
> 明确不含：live SEC、Verifier、bounded revise、多 Agent，以及跨浏览器刷新与 Worker
> 重启的组合验收

## 1. 用户入口与唯一执行链

用户通过 `POST /api/v1/workspaces/{workspace_id}/research-runs` 提交显式
`ResearchBrief`。请求带 `Idempotency-Key`，成功返回 `202 Accepted`；HTTP 线程只建立
业务事实和可靠任务，不同步执行 Research。

```text
Research API
→ ResearchSubmissionService
→ ConversationApplicationService
→ PostgreSQL: ResearchRun + Brief + AgentRun + Job + Outbox + queued Event
→ Dispatcher / Worker
→ SqlAlchemyDirectAnswerRunLoader
→ UnifiedAgentRuntime
→ ResearchL3Runtime（兼容名称，当前可执行 L3/L4）
→ 同一 bounded model/tool loop
→ EvidenceApplicationService / ResearchWorkflowStore
→ CheckpointStore / ResearchDurabilityService
→ 同一 AgentRun / Step / Event / Trace
```

`research_runs` 是 `agent_runs` 的领域扩展。系统不建立第二套 Research Step/Event、
Worker loop 或 Tool loop。L4 复用 Day 2 的 `CheckpointEnvelope`/CAS、Day 3 的可靠
Job/Outbox 和 Day 4 的 Research graph/Evidence/Claim。

## 2. ResearchBrief、FinancialScope 与图状态

Brief revision 1 与 Run 原子创建，保存原始问题、确认范围、排除项、完成标准、可信
Budget、确认人和可选 `FinancialScope`。本地 SEC fixture 请求可设置
`company_or_period_ambiguity`；该原因必须同时具有 `FinancialScope`，并在 `plan` 节点后
暂停。

图版本为 `research-l4-graph-v1`，状态包含：

- Run/Workspace/Brief/Plan/graph version 与当前节点；
- pending action、Evidence/Claim/Artifact refs；
- Step、Token、费用、取消、审批和 stop metadata；
- 脱敏错误摘要，不含 Secret、完整 filing 或模型原始 chain-of-thought。

`FinancialScope` 的权威事实保存在不可变 Brief revision 中；每个 L4 Checkpoint 同时保存
规范化 scope 快照。恢复时 Loader 重新读取当前 Brief、当前 Workspace membership、Budget
与 Tool policy，并要求 Checkpoint scope 与 Brief scope 完全一致，不能只信创建时权限快照。

## 3. 唯一 Research graph

节点顺序保持不变：

```text
clarify_scope
→ write_research_brief
→ plan
→ research_loop
→ normalize_evidence
→ synthesize_claims
→ outline
→ draft
```

| 节点 | 职责 | 允许调用 | 禁止 |
|---|---|---|---|
| `clarify_scope` | 校验问题、Brief 与 Run | 领域校验 | 静默改题 |
| `write_research_brief` | 校验确认者与 revision | 已持久化 Brief | 模型伪造确认 |
| `plan` | 生成版本化 Plan；必要时触发歧义审批 | ResearchWorkflowStore、DurabilityService | Provider SDK、模型决定审批 |
| `research_loop` | 获取有界 Observation/final decision | 统一 Runtime/ToolExecutor | 第二套 loop、节点直连 Tool |
| `normalize_evidence` | 重授权并规范化 Observation | EvidenceApplicationService | 把 URL/摘要直接当 Evidence |
| `synthesize_claims` | 建立支持关系或 uncertain Claim | EvidenceApplicationService | 无证据标记 supported |
| `outline` | 形成可解释草稿结构 | 领域逻辑 | Verifier/revise |
| `draft` | 保存 L3 草稿与 refs | ResearchWorkflowStore | 标记 verified report |

每个节点写统一 started/completed/failed Event。成功节点随后保存 Checkpoint 和
`agent.checkpoint.saved`；只有 Checkpoint/Event 均已提交后，才允许故障注入或进入下一节点。

## 4. Checkpoint 与恢复合同

PostgreSQL `agent_checkpoints` 是恢复事实，`research_runs.state` 和 Trace 不是。L4 payload
固定为 `research_l4_v1`，包含 graph/state schema、Research Run、`FinancialScope`、当前/下一
节点、graph state，以及恢复必要的 Step、Observation、final decision/response、outline 和草稿
引用。Checkpoint revision 独立单调递增，Store 使用 expected revision 做 CAS。

恢复分两类：

| 类型 | 权威前置事实 | 恢复位置 |
|---|---|---|
| `approval` | Run 为 `paused`；同一 Checkpoint 的 allowed Decision 已提交且 resume proof 已原子领取 | 从 `next_node` 继续同一 Run |
| `recovery` | Run 为 `running`；最后已提交 Event 必须是指向最新 Checkpoint 的 `checkpoint.saved` | 从 `next_node` 继续同一 Run |

Loader 对 Run/Workspace、graph/schema、Checkpoint/Event 尾部、节点顺序、Brief scope、当前权限、
Budget 和恢复所需 payload 做 fail-closed 校验。旧 revision、错误 proof、不兼容 schema、跨
Workspace 和不完整 Tool loop snapshot 都拒绝执行，不从头静默重跑。

当前硬停证据只覆盖“成功节点 Checkpoint 提交后”的安全边界；不声称任意指令中点都可续跑。
跨浏览器刷新与真实 Worker 进程重启的组合旅程保留给 Day 8，Day 10 再做发布回归。

## 5. HITL、resume proof 与副作用账本

`research_approval_requests` 保存 request、reason、checkpoint revision、状态、到期时间和
SHA-256 proof 摘要；原始 proof 不落库。`research_approval_decisions` 每个 request 最多一条
人工 allow/deny Decision。`research_side_effects` 以
`workspace + effect_kind + idempotency_key_hash` 唯一约束记录已完成 Tool/Evidence/Artifact
结果。

正式接口为：

- `GET /research-runs/{id}/durability`：Checkpoint、审批与重复副作用计数；
- `POST /research-runs/{id}/approval-decisions`：原子写 allow/deny；
- `POST /research-runs/{id}/resume`：校验 proof 和当前状态，原子创建 Job + Outbox。

重复相同 Decision 返回已有事实；冲突 Decision 明确拒绝。重复 resume 返回相同 Job，不重复
创建 Outbox。deny 写 `approval_denied` 终态；到期后的 decision 会原子写
`approval_timed_out`，resume 也会拒绝。当前没有独立后台超时扫描器，因此“无人再次访问的 pending
请求自动转终态”不属于本步已证明能力。

resume 前重新检查 Run 是否仍 paused、是否已取消、deadline、max steps、Token 和费用。副作用
账本防止恢复后重复登记 Tool、Evidence 和 Artifact；这提供幂等收敛，不宣称 exactly-once。

## 6. Evidence、终态与安全边界

Observation 通过 Workspace、locator、版本、hash 和许可检查后才可成为 Evidence。没有合格
Evidence 时生成 uncertain Claim/draft；L4 解决耐久执行，不把 L3 草稿升级为 verified Report。

| 条件 | AgentRun 结果 |
|---|---|
| graph 完成 | `completed / final` |
| Tool/Provider/schema/节点失败 | `failed` 与稳定 stop reason |
| Budget/deadline | `failed` 与对应 stop reason |
| 用户取消 | `cancelled` |
| 等待歧义确认 | `paused / approval_required` |
| deny | `failed / approval_denied` |
| 到期后作出决定 | `failed / approval_timed_out` |

WorkspaceScope、actor、Budget、Tool capability 和 Secret reference 都由服务端加载。Checkpoint、
Event、Trace、Plan 与草稿不保存原始 chain-of-thought、Secret 或完整 filing；错误只保存稳定脱敏码。

## 7. 迁移、回滚与证据

迁移 `c2e6f8a0b431` 增加 paused 状态、Brief approval reason、Checkpoint 复合唯一约束、审批
request/decision 和副作用账本。回滚前必须停止新的 Research 和 resume 请求，等待或终止相关
Job，备份 PostgreSQL，并确认允许丢失 L4 审批/恢复事实；随后才可 downgrade 到
`b1d5e7f9a320`。旧 L3 Brief/Plan/Draft/Evidence/Claim 保留。

确定性恢复集为 `evals/scenarios/sec-fixture-l4-v1.json`，报告为
`evals/reports/sec-fixture-l4-v1.{json,md}`。可执行证据覆盖安全节点 hard stop、真实 PostgreSQL
审批/恢复/去重、HTTP 合同和 Workbench 组件。它们不证明 live SEC、生产模型质量或 Day 8
跨刷新/Worker 重启组合门。

| 层级 | 状态 | 能力 |
|---|---|---|
| L3 Evidence Research | Day 4 `complete` | Brief、Plan、唯一 graph、Evidence/Claim、可解释/uncertain 草稿 |
| L4 Durable Research | Day 5 Step 5 `implemented_pending_verification` | 节点 Checkpoint、CAS、HITL、同 Run resume、幂等副作用 |
| L5 Verified Research | `planned`（Day 8） | Evidence-aware Verifier、bounded revise 与最终业务状态 |

本地实现和测试通过不能替代 Step 5 提交/分支 CI、`main` 合并提交 CI、完整 Day 5 DoD 与项目
所有者复核；在这些证据齐全前不得把 Day 5 或 D5-09 写成 `complete`。
