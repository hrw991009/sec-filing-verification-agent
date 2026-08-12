# ADR 0005：LangGraph 仅作为 Deep Research 内部编排适配器

> 状态：已接受
>
> 日期：2026-08-03
>
> 修订日期：2026-08-12
>
> 依据：`docs/master-plan.md` v1.7.0 第 3.1、3.3、5.1～5.2、6.4、Day 4～Day 6 与 17.1 节

## 背景

项目中的普通回答、Tool Use 与 Deep Research 必须共用统一的 `AgentRuntime`、Run/Step/Event/Budget、Context、ToolExecutor、Checkpoint 和 Trace 语义。Celery 只决定任务在哪个进程可靠执行；Agent Harness 负责组合 Instructions、Tool、Memory、Knowledge/RAG、Approval 与 Eval；Application Service 负责业务事实和权限。

Deep Research 还需要显式表达澄清、ResearchBrief、计划、多源研究、Evidence/Claim、核验、有限修改、Checkpoint、中断、恢复和人工审批，适合使用 LangGraph 的 typed graph 与 durable execution 能力。LangGraph 本身可以作为低层 orchestration runtime，但在本项目中只能位于统一 Agent Runtime 之后，不能形成第二套公共执行协议。

普通 CRUD、身份、普通聊天、文档入库和简单 Tool loop 已有更直接的事务、Runtime 或 Job 状态机。把这些能力全部放入 LangGraph 会隐藏所有权、重复 model/tool loop，并造成图内与图外两套正式状态。

## 决定

LangGraph 仅用于 Deep Research 的内部 typed graph，不渗透普通 CRUD、身份、普通聊天、知识库管理或文档入库。它必须把原生 graph state、checkpoint 和 interrupt 映射到项目统一的 `AgentRun/Event/Checkpoint`；外部 API、SSE、Trace、Harness 和 Workbench 只认统一语义。

`research_runs` 是 `agent_runs` 的领域扩展，不另建 `research_steps`、`research_events` 或 `research_checkpoints` 第二套执行历史。LangGraph 节点只能调用 Agent Runtime 或 Domain/Application Port，不得直接导入 Provider SDK、Tool Adapter、Milvus、Elasticsearch、MinIO 或数据库连接。

### 演进层级

#### L3：Evidence Research

```text
clarify_scope → write_research_brief → plan → research_loop
→ normalize_evidence → synthesize_claims → outline → draft
```

- `research_loop` 复用统一 Runtime 的有界 model/tool loop，不复制一套 Planner/Tool Executor。
- Observation 只有经过授权、规范化、去重和 locator 校验后才能成为 Evidence。
- Claim 必须标记 support、refute 或 uncertain，并记录 coverage 与 conflict。
- L3 交付可解释草稿，不冒充已经完成 durable recovery、HITL 或 Verifier。

#### L4：Durable Research

在 L3 基础上增加版本化 Checkpoint、interrupt/resume、取消、持久 `ApprovalRequest/Decision` 与副作用账本：

```text
typed graph state
→ CheckpointStore.save(expected_revision)
→ interrupt / approve | deny | timeout
→ resume token + idempotency ledger
→ continue from last committed node
```

- Checkpoint 使用 PostgreSQL，包含 schema version、revision 和恢复所需资源引用。
- 保存必须使用 optimistic CAS；旧 revision、重复 resume 和不兼容 schema 必须明确拒绝或执行已评审迁移。
- 外部副作用遵循“持久化意图/幂等键 → 执行 → 持久化结果”；恢复时先检查已有结果。
- Agent Checkpoint 与文档入库 stage checkpoint 分开建模，Trace 不得作为恢复事实源。

#### L5：Verified Research

在 L4 基础上增加 Evidence-aware Verifier 和有限修改：

```text
draft → verify → revise（最多 N 次）/ human_review → finalize
```

- Verifier 按 Claim 支持度、引用可解析性、coverage、conflict 和未决问题评分，不能只评价文风。
- revise 受次数、Token、费用、deadline 和取消限制。
- 最终 Report 明确标记 `complete`、`partial` 或 `uncertain`，界面和导出不得把后两种伪装成完成。

L6 specialist/handoff 或 orchestrator-workers 不是七天硬指标。Planner、Retriever、Analyst、Writer、Verifier 首先是同一图中的节点职责，不自动等于独立 Agent；只有 Eval 证明相对单图基线存在显著净收益时，才进入单独 ADR 和 time-boxed 实验。

### 统一执行约束

1. 生产只有一张正式 Research graph，不能在图外手工执行同一 Research。
2. 普通回答、Tool Use 和 Research 使用同一 `AgentRuntime`、ToolExecutor、Budget、stop reason 与 Trace。
3. `ResearchState` 至少包含 schema version、run/scope、ResearchBrief、plan/current node、pending actions、Evidence/Claim/Artifact refs、预算使用量、step/revise 计数、审批状态、取消标记、stop reason 与脱敏错误摘要。
4. LangGraph state 的每个可恢复边界映射到统一 Checkpoint；`agent_events` 记录关键 `agent.*` 业务事件和 sequence。
5. 节点角色不能绕过 Harness profile 中的 Tool capability、WorkspaceScope、Schema、预算、deadline 或 approval policy。
6. `ApprovalPolicy` 使用可信 policy context；模型只能请求审批，不能生成或扩大审批结果。
7. 取消必须传播到 graph、Runtime 和底层协作式任务；取消后不能继续提交正式 Artifact。
8. 最大步骤、并发、Token、费用、运行时间、revise 次数和 Tool allowlist 均为持久预算事实。
9. 不保存原始 chain-of-thought，只保存用户可见结果、Evidence、Artifact、结构化决策和简短 reasoning summary。
10. Harness 的 Fake/Replay/Fault/Scorer 运行同一 Runtime/graph；Replay 只冻结外部边界结果，不宣称模型本身确定。

## 结果

### 收益

- 普通回答、Tool Use 和 Research 共享一套执行、恢复、事件和评测语言；
- LangGraph 的 durable state/HITL 能力得到利用，但不会泄漏为第二套公共 Runtime；
- L3、L4、L5 的复杂度和收益可以分别评测，而不是一次性堆成“多 Agent”；
- Report、Claim、Evidence、Checkpoint 和 Approval 能在 Agent Learning Workbench 中沿同一 Run 反查；
- Worker 中断、重复 resume 和审批恢复可以证明零重复副作用。

### 代价与风险

- Graph state 与统一 Agent State/Checkpoint 之间需要明确的版本化映射；
- 节点输入、输出和副作用必须满足幂等与恢复约束；
- 图升级需要处理已有 Run 的兼容、迁移或明确拒绝；
- interrupt、resume、取消传播和组合故障会增加状态测试量；
- 如果节点直接调用 Provider/Tool Adapter，或图外存在第二条 Research 链路，状态、预算、审计和 Eval 会发生漂移。

## 否决方案

### 所有业务都使用 LangGraph

否决原因：CRUD、身份、入库和简单聊天已有正式事务/Runtime/Job 语义，强行进入图会增加复杂度并隐藏边界。

### Research 绕过统一 Agent Runtime

否决原因：会复制 model/tool loop、Budget、Event、Checkpoint 与 Trace，使 Conversation、Harness 和 Research 无法共享场景与评测。

### 定义状态图，但在线上手工调用节点

否决原因：会形成名义编排和正式手工链路并存，Checkpoint、取消和恢复均不可信。

### Research 仅使用临时内存状态

否决原因：Worker 重启后无法恢复，也不能提供持久审计、预算和用户可见进度。

### 一开始采用多 Agent

否决原因：角色数量不等于能力提升；在没有 L0/L2/L3/L4/L5 对照前，无法证明额外延迟、Token、合并冲突和调试成本合理。

## 验证

- L3：确定 Fake 下 ResearchBrief、Event、Observation→Evidence、Claim 和 coverage 可复现；
- L4：主要节点 hard stop 后从最后成功 Checkpoint 恢复，重复 resume/decision 不重复副作用；
- L5：Verifier、bounded revise 和 complete/partial/uncertain 判定可重复；
- 生产、Harness 与 Workbench 使用同一个 run_id、sequence、Checkpoint 和 Trace；
- 未授权 Tool、其他 Workspace Run/Checkpoint/Report/Evidence 均不可访问；
- 取消、预算耗尽、最大步骤和最大 revise 真实生效；
- Report 的关键 Claim 均能解析到真实 Evidence，数据库中不存在原始 chain-of-thought；
- Day 7 比较 L0/L2/L3/L4/L5，只有存在净收益时才提出 L6 ADR。

## 变更与回滚

Research graph、State、Checkpoint schema、节点顺序或 Runtime 映射变化必须更新本 ADR，并提供版本兼容、已有 Run 迁移和回滚方案。

如果新版图无法继续处理旧 Run，必须明确标记为不可恢复并给出用户可见错误，不能静默从头执行或重复副作用。若 LangGraph Adapter 回滚，统一 `AgentRun/Event/Checkpoint` 事实仍保留，新的 Research Run 暂停受理，现有 Run 按版本明确继续、迁移或拒绝。
