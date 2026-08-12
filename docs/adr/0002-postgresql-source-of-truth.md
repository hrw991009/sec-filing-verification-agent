# ADR 0002：PostgreSQL 是唯一业务事实源

> 状态：已接受
>
> 日期：2026-08-03
>
> 修订日期：2026-08-12
>
> 依据：`docs/master-plan.md` v1.7.0 第 3.1、3.3、5.1～5.5、6.2～6.4、8 与 17 节

## 背景

系统同时使用 PostgreSQL、Redis、MinIO、Milvus 和 Elasticsearch。

如果多个存储都被当作最终事实源，当写入、删除、重试或网络故障发生时，系统将无法判断哪一份状态可信，也无法可靠恢复。

关系数据、权限、任务状态、Agent Run/Step/Event/Checkpoint、Memory、Context manifest、引用关系和评测结果需要事务、约束、查询和迁移能力，因此必须选择一个明确的业务事实源。

## 决定

PostgreSQL 是唯一业务事实源。

具体所有权如下：

1. PostgreSQL 保存用户、Workspace、membership、业务资源、Refresh Session、审计日志、Job、Outbox、最终消息、Evidence、Citation、Research 领域扩展和其他持久业务状态。
2. Redis 只保存 Celery 队列、限流、缓存和带 TTL 的短期流事件，不保存唯一业务事实。
3. Celery result backend 不能作为业务 Job 状态或业务结果的最终来源。
4. MinIO 保存私有二进制资产，但资产所有权、Workspace、状态、bucket 和 object key 引用保存在 PostgreSQL。
5. Milvus 保存可重建的 Dense 向量索引。
6. Elasticsearch 保存可重建的 BM25 关键词索引。
7. Milvus 和 Elasticsearch 的结果必须回 PostgreSQL 重新加载，并重新校验 Workspace、active version 和资源状态。
8. 所有正式表结构变化只能通过 Alembic migration 完成。
9. 禁止使用 `Base.metadata.create_all()`、手工 ALTER 或启动时自动修改正式数据库。
10. 普通回答、Tool Use 与 Research 共用 `agent_runs`、`agent_steps`、`agent_events`、`agent_checkpoints`、`run_artifacts` 和 `tool_calls`；`research_runs` 只通过 `agent_run_id` 扩展领域事实，不另建 research_steps/research_checkpoints 第二套执行历史。
11. Short-term Memory 的 `thread_memory_states`、Long-term Memory 的 `memories/memory_revisions` 和每个 Step 的 `context_manifests` 保存在 PostgreSQL；缓存、向量或摘要生成物可以重建，不能反过来成为 Memory 或 Context 注入事实源。
12. `evaluation_cases` 与 `evaluation_results` 保存数据集/版本、runtime/harness/model/prompt/context/toolset version、Budget、Scorer 结果、usage、费用和延迟；评测报告文件是可重现交付物，不替代关联 AgentRun 与数据库事实。
13. Agent Event 是按 sequence 持久的业务变化与恢复依据；Token delta 可以只进入带 TTL 的 Redis 流，但关键 Step、审批、Checkpoint、Evidence、Artifact、快照和唯一终态必须持久化。

## Agent 状态、Checkpoint 与 Trace

- **State** 是同一 AgentRun 内随 Step 演进的计划、消息、中间结果、Evidence/Artifact 引用、预算和计数；
- **Checkpoint** 是可恢复 State 的版本化快照，使用 schema version、revision 与 optimistic CAS，用于继续同一次 Run；
- **Event** 是单调追加的业务变化，关键 Event 支持 SSE snapshot/replay 与恢复审计；
- **Trace** 是 Event、Context manifest、usage、Evidence、Artifact 和脱敏决策摘要形成的可观测投影，用于调试和 Eval，不用于恢复；
- **Memory** 是跨 Step/Run 可选择的 Context source，不是 Checkpoint；Context compaction 也不等于 Memory。

Trace 后端、日志系统、Redis Stream 或浏览器缓存都不得成为恢复真相。Trace 丢失不应改变 Run 的业务终态；Checkpoint 缺失或 schema 不兼容必须明确拒绝恢复，不能根据 Trace 猜测 State。

## 跨存储一致性

PostgreSQL 事务无法回滚 MinIO、Milvus、Elasticsearch 或外部 Provider，因此跨存储流程采用：

- PostgreSQL 事务中的业务资源、Job 和 Outbox；
- Dispatcher 投递 Celery；
- 确定性外部 ID，例如 `chunk_id:index_version`；
- 阶段状态和幂等键；
- 有上限的重试；
- 删除补偿；
- 定时对账（reconciliation）；
- 孤儿对象和孤儿索引检测。

文档只有在向量索引和关键词索引都成功后才能进入 `ready`。

删除先进入 `deleting`，外部资源清理完成后才能进入 `deleted`。

## 结果

### 收益

- 每项业务状态都有唯一可信来源；
- 可以通过 PostgreSQL 约束和事务维护租户权限与引用关系；
- Redis、Milvus 和 Elasticsearch 故障后可以重建；
- Worker 重启或任务重复投递时可以从持久状态恢复；
- Agent Run、Memory 注入、Research 恢复和 Eval 结果均能反查统一事实；
- 删除、补偿和对账具有可解释依据；
- Alembic 提供可审查、可重复的数据库演进记录。

### 代价与风险

- 每次索引检索后需要额外回 PostgreSQL 重新加载；
- 跨存储流程只能达到受控的最终一致性；
- 必须维护 Outbox、阶段状态、补偿和对账；
- PostgreSQL 成为核心依赖，需要备份、恢复、连接池和容量规划；
- 如果业务代码绕过 Repository 直接访问派生索引，可能破坏事实源约束。

## 否决方案

### 将 Redis 或 Celery result backend 作为 Job 真相

否决原因：Redis 是短期执行层，数据可能过期、驱逐或在故障后丢失，不能承担可审计业务状态。

### 将 Milvus 或 Elasticsearch 当作权限与资源真相

否决原因：索引是异步派生数据，可能滞后、重复或缺失，也不适合承担完整关系约束。

### 将 Trace、日志或 Redis Event 当作 Agent Checkpoint

否决原因：Trace 面向观察和评分，可能采样、脱敏或缺失；短期 Event 可能过期。恢复必须依赖 PostgreSQL 中版本化、通过 CAS 提交的 Agent State/Checkpoint。

### 为聊天与 Research 分别保存执行真相

否决原因：独立 Generation/Research Step/Checkpoint 会让终态、预算、重试、恢复和评测语义漂移。Research 只能扩展统一 AgentRun。

### 对多个存储执行应用层“同时写入”

否决原因：外部存储不共享 PostgreSQL 事务；任何中途失败都会产生无法自动回滚的不一致。

### 使用 `create_all()` 自动建表

否决原因：无法提供可审查和可回放的正式迁移历史，也无法安全管理已有数据上的结构变化。

## 验证

- 在全新空库执行 `alembic upgrade head`；
- Redis 不可用时，持久业务记录仍能从 PostgreSQL 解释；
- 普通回答、Tool Use 与 Research 都使用统一 AgentRun/Step/Event/Checkpoint；
- Checkpoint 能在 schema/revision 兼容时恢复同一 Run，Trace 不能被恢复代码读取为 State；
- Short/Long-term Memory 的写入、修订、停用、删除和实际 Context 注入均可审计，删除后残留为 0；
- Evaluation Result 能反查 Scenario 版本、AgentRun、Runtime/Harness/Context/Toolset 版本和组合评分；
- 清空 Milvus 和 Elasticsearch 后，可以依据 PostgreSQL 重建索引；
- 检索结果必须经过 PostgreSQL Workspace 二次授权；
- 重复投递同一 Job 不产生重复业务资源或索引；
- 外部删除失败能够被对账发现；
- 备份恢复演练能够恢复 PostgreSQL 和 MinIO，并重新生成派生索引。

## 变更与回滚

任何改变数据所有权的决定都必须新增 ADR，并说明现有数据迁移、双写窗口、一致性验证、失败补偿和回滚路径。

在新方案完成迁移和对账前，PostgreSQL 仍是唯一业务事实源。
