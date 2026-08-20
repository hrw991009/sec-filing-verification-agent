# Day 4 执行计划：Agent Memory、Evidence 与 Deep Research L3

> 制定日期：2026-08-20
>
> 计划基线：[七天主计划](../master-plan.md) 1.7.0 Day 4
>
> 相关决策：[系统架构](../architecture.md)第 6.5～6.6、10.3、12、15.1、15.4、15.6、18 节，[ADR 0003](../adr/0003-unified-evidence-model.md)、[ADR 0005](../adr/0005-langgraph-research-only.md)
>
> 当前状态：Day 3 门禁已关闭；D4-01～D4-07 尚未实现，以下五步均未开始。

## 1. 执行边界

Day 4 复用 Day 2/3 已保存的 L0/L2 Run、Observation、Context manifest、任务结果、Token、费用、延迟和 24 条累计 Scenario，不重做基线，也不另写 Runtime、Tool loop 或仅供展示的 Workbench 数据链路。每一步都必须沿正式 PostgreSQL、Application Service、统一 `UnifiedAgentRuntime`、Event/Trace/Context manifest、OpenAPI 和真实前端交互形成纵向闭环。

本日终点是可治理的 Short/Long-term Memory、Observation→Evidence→Claim 与可解释 Research L3 草稿。Durable Checkpoint、持久 HITL、Worker hard-stop resume 属于 Day 5，Verifier 与 bounded revise 属于 Day 6；不得用普通状态持久化、节点名称或 Mock 成功提前冒充这些能力。

每一步只有在其正常、边界、失败、权限、刷新恢复、契约、可观测和评测条件全部通过后才能关闭。适用的全局与 Agent Definition of Done 必须逐项复核；任何 `N/A` 都要写明具体理由、复核人和日期。

## 2. 五个可验收步骤

### 步骤 1：可控 Memory 写入纵向闭环

交付从正式会话发起的“保存为记忆”旅程：生成候选摘要，用户确认或编辑后保存；同时建立版本化 `ShortTermMemoryState`、`Memory`、`MemoryRevision`、生命周期、provenance/source ref、scope、confidence、write reason 和 policy/user decision。使用 Alembic、正式 Application Service/API/OpenAPI 和前端交互，不默认永久保存全部聊天，也不把 Short-term Memory、Context compaction、Run State 或 Checkpoint 混写为 Long-term Memory。

验收条件：

- 用户能从真实会话创建候选、编辑并确认，刷新后仍能查看当前投影和修订历史；create/update/merge/reject 都有稳定事实与错误语义。
- 敏感、低置信、无明确价值、来源失效和跨 Workspace 请求被策略或权限边界拒绝；模型不能自行确认写入。
- fresh migration、数据库约束、OpenAPI 生成、API/组件/真实 PostgreSQL 测试和最小浏览器旅程通过，Trace/日志不保存 Secret、敏感原文或原始 chain-of-thought。

### 步骤 2：Memory 召回、治理与 Context manifest 闭环

在同一 Runtime 的 Context Compiler 中接入 Short/Long-term Memory：按当前目标、user/workspace scope、时效、冲突、重复、敏感度和 Token 预算选择，并在 manifest 中记录包含、排除和裁剪原因。完成跨 Thread 召回、使用说明、搜索、反馈、修改、停用、过期和删除；建立独立 Memory Scorer，测量 write accuracy、retrieval precision/utility、污染率、冲突处理、Token 成本、修改生效率和 deletion residual。

验收条件：

- 下一次正式 Agent Run 能说明实际使用了哪条 Memory，并可从回答/Trace 反查 revision、provenance、scope 和 manifest；Harness 不建立第二套调用路径。
- 冲突、过期、无关和超预算 Memory 不会静默注入；用户修改后下一 Run 使用新版本，停用或删除后下一 Run 不再引用。
- deletion residual 为 0、跨 Workspace 召回为 0；重复删除幂等，Short-term/Long-term/State/Checkpoint 分层和预算耗尽均有确定性 Scenario。

### 步骤 3：Observation→Evidence→Claim 账本闭环

复用 Day 3 的 Web/行业与 Text2SQL Observation/EvidenceCandidate，建立统一 Evidence Normalizer、版本化 locator 判别联合、来源/许可与资源重新授权、去重、content hash、lineage、状态和失效语义。建立 `ResearchClaim` 与 `claim_evidence`，分别表达 supports/refutes/context 关系以及 supported/refuted/uncertain/conflicted 状态、coverage 和 conflict；证据图只从已持久化并授权的正式关系派生。

验收条件：

- 至少各有一条 Day 3 Web/行业和 Text2SQL 正式 Observation 经过完整校验后成为 Evidence，并能反查 origin Run/Step/ToolCall、来源版本、normalizer version 和 locator。
- 未授权、locator 无效、来源版本或许可缺失、依赖失败、敏感内容超界和跨 Workspace 候选不得提升为 active Evidence；失效后不再暴露 excerpt、私有对象或签名 URL。
- 每个关键 Claim 关联当前用户可访问的 Evidence，或明确标记 uncertain/conflicted；supports/refutes/context、coverage、conflict 和图节点/边可确定验证，不能由文案或 finalizer 伪造确定性。

### 步骤 4：ResearchBrief 与唯一 Research L3 graph 闭环

实现问题澄清和版本化 `ResearchBrief`，显式保存原始问题、确认范围、排除项、完成标准和预算；在 LangGraph 内建立唯一 typed L3 graph：`clarify_scope → write_research_brief → plan → research_loop → normalize_evidence → synthesize_claims → outline → draft`。Graph 节点只调用统一 Runtime、ToolExecutor 和 Domain/Application Port；若本步引入 LangGraph 依赖，必须锁定版本并完成许可证、依赖和回滚复核。

验收条件：

- 确定 Fake 下 ResearchBrief、typed state、Event 序列、Plan、Observation→Evidence、Claim、coverage/conflict 和可解释草稿可重复；原始问题与确认范围不会被 Planner 静默改写。
- 生产、Harness、Research 和 Workbench 使用同一 run_id、Step/Event sequence、Budget、stop reason 与 Trace；不存在图外 Research、Router/节点直连 Provider 或复制 Tool loop。
- 正常、Tool/Provider 失败、Evidence 缺失/矛盾、max steps、Token/费用、deadline、取消和跨 Workspace 场景均有明确终态；本步只交付 L3，不宣称 durable resume、HITL、Verifier 或 bounded revise。

### 步骤 5：Workbench、对照 Eval 与 Day 4 门禁收口

扩展正式 Agent Learning Workbench 的 Memory、Context manifest、Research 时间线和 Evidence/Claim 图；所有状态从 OpenAPI、Event、Trace、manifest 和正式资源 API 重建。保留 Day 2/3 基线并增加 Day 4 的 Memory 候选/冲突/删除、Research scope、Evidence 缺失/矛盾、预算和权限 Scenario；Memory Scorer 与 Evidence/Research Scorer 分开报告，并完成同题 L0/L2/L3 对照。

验收条件：

- 刷新后仍能回答“什么被写入、为何召回、什么进入 Context、哪些 Claim 由哪些来源支持”；修改、停用、删除和 Evidence 失效会同时反映在界面与下一 Run。
- 累计 Scenario 不少于主计划要求的 20 条且保留已有 24 条基线；报告同时包含结果、轨迹、Evidence、Memory 污染/删除残留、Token、费用和延迟，并由规则 Scorer、确定性夹具和人工抽样支持，LLM judge 不是唯一判据。
- 全量 format/lint/type、fresh Alembic migration、OpenAPI/SSE contract、真实 PostgreSQL/Redis/MinIO、权限负向、组件、关键 E2E、依赖/许可证、Secret 与隐私检查通过；不存在静默 Mock、调试旁路或第二正式链路。
- 完成 `docs/memory-policy.md`、`docs/research-state-machine.md`、本学习日志、能力矩阵和必要 README/运行回滚说明；逐项关闭 Day 4 验收门禁与适用 Definition of Done，干净 CI 通过后才能把 D4-01～D4-07 标记为 `complete` 并进入 Day 5。

## 3. 步骤状态

| 步骤 | 当前状态 | 关闭条件 |
|---|---|---|
| 1. 可控 Memory 写入 | `planned` | 本文步骤 1 的全部验收条件通过 |
| 2. Memory 召回与治理 | `planned` | 本文步骤 2 的全部验收条件通过 |
| 3. Evidence/Claim 账本 | `planned` | 本文步骤 3 的全部验收条件通过 |
| 4. Research L3 graph | `planned` | 本文步骤 4 的全部验收条件通过 |
| 5. Workbench/Eval/Day 4 门禁 | `planned` | 本文步骤 5、Day 4 门禁及适用 DoD 全部通过 |

状态只随实际证据更新。代码存在、页面截图、单条漂亮答案、Mock success 或局部绿色测试都不能把任一步改为完成。
