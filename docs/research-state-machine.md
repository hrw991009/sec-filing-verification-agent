# Research L3 状态机与运行边界

> 版本：1.3（Day 4 主分支关闭）
>
> 更新日期：2026-08-22
> 当前能力：Evidence Research L3  
> 明确不含：durable resume、HITL、Verifier、bounded revise、多 Agent

## 1. 用户入口与唯一执行链

用户通过 `POST /api/v1/workspaces/{workspace_id}/research-runs` 提交显式
ResearchBrief。请求必须带 `Idempotency-Key`，成功返回 `202 Accepted`；HTTP 线程只建立
业务事实和可靠任务，不同步执行 Research。

```text
Research API
→ ResearchSubmissionService
→ ConversationApplicationService
→ 同一 PostgreSQL 事务
   ResearchRun + ResearchBrief + AgentRun + Job + Outbox + queued Event
→ 既有 Outbox Dispatcher / Worker
→ SqlAlchemyDirectAnswerRunLoader
→ UnifiedAgentRuntime
→ ResearchL3Runtime（LangGraph 外层）
→ 既有 bounded model/tool loop（research_loop 内层）
→ EvidenceApplicationService / ResearchWorkflowStore
→ 同一 AgentRun / Step / Event / Trace
```

`research_runs` 只是 `agent_runs` 的领域扩展。系统不建立 `research_steps`、
`research_events`、`research_checkpoints`、第二个 Worker loop 或第二条 Tool loop。
Research 和 L2 共用 ContextCompiler、ModelProvider、ToolRegistry、ToolExecutor、Budget、
Event committer、CancellationProbe 与 Trace 语义。

## 2. ResearchBrief 与状态事实

ResearchBrief revision 1 与 Run 原子创建，保存：

- 用户原始问题 `original_question`；
- 用户确认的 `confirmed_scope`；
- 明确排除项 `exclusions`；
- 完成标准 `completion_criteria`；
- 服务端可信 Budget 快照；
- `confirmed_by_user_id`、`confirmed_at` 与 revision。

Planner 不得改写原始问题或确认范围。当前 API 把提交视为用户已确认的 revision 1；未来如支持
澄清修改，必须新增 Brief revision，不能覆盖已确认事实。

`research_runs.state` 是 JSON-safe 的 L3 审计状态快照，并在接受请求时与其他事实原子创建：

| 字段组 | 字段 | 语义 |
|---|---|---|
| 版本与归属 | `schema_version`、`graph_version`、`research_run_id`、`run_id`、`workspace_id` | 拒绝跨版本、跨 Run、跨 Workspace 混用 |
| Brief/Plan | `brief_revision`、`plan_id`、`current_node`、`pending_actions` | 记录用户确认版本和当前 graph 位置 |
| 可解释资源 | `evidence_refs`、`claim_refs`、`artifact_refs` | 只引用正式持久化资源，不嵌入第二份事实 |
| Runtime 用量 | `status`、`step_count`、`input_tokens_used`、`output_tokens_used`、`cost_micro_usd` | 从统一 Agent Runtime 投影，不由 graph 自行计费 |
| L3 边界 | `revise_count=0`、`approval_status` | Day 4 不执行 revise；需要持久审批时明确终止 |
| 终止控制 | `cancel_requested`、`stop_reason`、`error_summary` | 保存取消、统一停止原因和脱敏错误码 |

该状态不是 Checkpoint，不能用于进程重启后从节点恢复。节点完成时保存业务快照；统一 terminal
Event 在同一事务中把最终状态、Step 数和用量投影回该快照。

## 3. 唯一 L3 graph

正式 graph 版本固定为 `research-l3-graph-v1`，节点和顺序只能是：

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

| 节点 | 输入与职责 | 允许调用 | 明确禁止 |
|---|---|---|---|
| `clarify_scope` | 比较原始问题、Brief revision 与 Run | 纯领域校验 | 静默改写问题或 scope |
| `write_research_brief` | 验证确认者就是 Run owner | 已持久化 Brief | 模型伪造用户确认 |
| `plan` | 从可信 Tool allowlist 生成版本化 Plan | ResearchWorkflowStore | Provider SDK、任意 Tool 名称 |
| `research_loop` | 获取有界 Observation 和 final decision | 统一 Runtime 内唯一 model/tool loop | 节点复制循环或直连 ToolExecutor |
| `normalize_evidence` | 对 Observation 重新授权和规范化 | EvidenceApplicationService | 把 URL、模型摘要或 `[S1]` 直接当 Evidence |
| `synthesize_claims` | 建立 Claim 与 supports 关系；缺证据则 uncertain | EvidenceApplicationService | 用文案伪造 supported |
| `outline` | 形成固定的可解释草稿结构 | 纯领域逻辑 | Verifier 或 revise |
| `draft` | 保存 L3 草稿及 Evidence/Claim refs | ResearchWorkflowStore | 标记为 verified final Report |

LangGraph 只持有顺序和条件边。每个节点推进前检查 deadline 与取消；节点开始、成功或失败分别写
统一 `agent.research.node_started/completed/failed` Event。模型和 Tool 仍只产生既有
`MODEL`、`TOOL`、`FINAL` Step，因此 sequence、Budget 和 Trace 没有分叉。

## 4. Evidence、Claim 与草稿语义

Tool Observation 仍是不可信输入。只有通过步骤 3 的 Workspace、locator、版本、hash、许可和
依赖检查后，才会进入 `evidence_refs`。没有合格 Evidence 不是伪成功：系统创建 `uncertain`
Claim，并生成 `uncertain_draft`，明确显示“不是已核验的最终报告”。有 active supporting
Evidence 时可生成 `explainable_draft`；Claim conflicted/uncertain 同样必须保留限制说明。

Day 4 草稿不等于 Day 6 Report。当前不提供 Message/Report Citation 完整门禁，不声称经过
Verifier，也不把 Evidence 缺失隐藏成确定结论。

## 5. Budget、取消与终态

max steps、Token、费用、deadline 和 Tool allowlist 来自服务端创建的 `RunBudget` 与 Runtime
profile。模型只能在精确 Tool catalog 内请求 Action，不能扩大权限或预算。

| 条件 | AgentRun 结果 | stop reason / 草稿行为 |
|---|---|---|
| graph 完成且 final Step 落库 | `completed` | `final`；保存 L3 草稿 |
| Evidence 缺失 | `completed` | `final`；保存 `uncertain_draft` |
| Evidence 冲突 | `completed` | `final`；保存带 conflict/限制的 uncertain 草稿 |
| Tool/Provider、输出 Schema 或节点持久化失败 | `failed` | 既有稳定 stop reason，或 `runtime_error` |
| max steps、Token、费用耗尽 | `failed` | 对应统一 budget stop reason；不再提交后续 Claim/草稿 |
| deadline 到达 | `failed` | `deadline_exceeded` |
| 用户取消 | `cancelled` | `cancelled`；安全点后不再推进节点 |
| Tool 需要持久审批 | 当前 Run 明确终止 | `approval_required`；Day 4 不假装可 resume |
| 跨 Workspace/无当前 membership | 不创建或不返回 Run | 创建为 `403`，资源查询为不泄漏存在性的 `404` |

Worker hard stop 由既有 Terminalizer/Reconciler 收敛为唯一终态。已经提交的 Evidence/Claim 保留，
但 Day 4 不从普通 state 快照自动续跑，也不静默从头重放 Tool 副作用。

## 6. 安全与隐私边界

- WorkspaceScope、actor、Budget、Tool capability 和 secret reference 都从服务端重新装载；请求体
  不能提交这些可信字段。
- Graph 节点不能导入 Provider SDK、具体 Tool Adapter、数据库连接、MinIO 或任意 HTTP client。
- Tool Observation、网页和模型输出一律是不可信数据，不能改变 Instructions、scope、Tool surface
  或 Budget。
- Event、Trace、Plan 和草稿不保存原始 chain-of-thought；错误只保存稳定脱敏码。
- Brief、Plan、Draft、ResearchRun 查询同时过滤 workspace 与 owner；AgentRun、Evidence、Claim 和
  Trace 继续执行各自的当前授权复核。

## 7. 依赖、迁移与回滚

LangGraph 精确锁定为 `1.2.11`，只出现在 `workflows/research` 适配层。该版本声明 Python
`>=3.10`、包含 Python 3.13 classifier，许可证为 MIT；本地 import probe、mypy、测试和
`uv audit --locked` 已通过。版本依据：[PyPI](https://pypi.org/project/langgraph/1.2.11/)、
[官方 pyproject](https://github.com/langchain-ai/langgraph/blob/1.2.11/libs/langgraph/pyproject.toml)。
本地 wheel metadata 同时确认 LangGraph 子包与 LangChain Core 为 MIT、ormsgpack 为
Apache-2.0 OR MIT、xxhash 为 BSD-2-Clause、uuid-utils 为 BSD-3-Clause；未发现与本项目分发
方式冲突的许可证。

迁移 `a3c5e7f9b021` 扩展 `research_runs`，并创建 `research_briefs`、`research_plans`、
`research_drafts`。fresh PostgreSQL 必须通过 upgrade → Alembic check → downgrade → upgrade。

回滚顺序：

1. 停止接受新的 Research Run，并等待/终止当前 L3 Job；
2. 备份 PostgreSQL，保留统一 AgentRun/Event/Step、Evidence 与 Claim 审计事实；
3. 回滚应用到不读取新 Research 表的版本；
4. 只有明确接受丢失 Brief/Plan/Draft 和 L3 state 扩展时，才 downgrade 到
   `f2a4c6e8b013`；
5. 应用代码不再导入 LangGraph 后才移除依赖，不能让旧 Run 静默重跑。

## 8. L3/L4/L5 边界

| 层级 | 当前状态 | 能力 |
|---|---|---|
| L3 Evidence Research | Day 4 `complete`；PR #7 已合入 `main`，合并提交 CI 与授权复盘通过 | Brief、Plan、唯一 graph、Evidence/Claim、可解释/uncertain 草稿 |
| L4 Durable Research | 未实现（Day 5） | PostgreSQL Checkpoint、CAS、interrupt/resume、持久审批、副作用恢复 |
| L5 Verified Research | 未实现（Day 6） | Evidence-aware Verifier、bounded revise、complete/partial/uncertain Report |

步骤 5 的正式 Research Workbench 通过 Research 资源 API、safe Agent Trace 与 Evidence/Claim API 重建 Brief、Plan、节点/Step、usage、stop reason 和草稿；刷新不会依赖浏览器业务缓存。三个 Research 节点 Event 只暴露 node、graph/state schema version、state revision 和稳定错误码，Trace 安全字段表必须穷举事件枚举。确定性对照与限制见 `evals/reports/day4-research-v1.{json,md}` 和 `evals/reports/day4-v1.{json,md}`。

任何 state 行、节点名称或单次成功演示都不能被表述为 L4/L5。
