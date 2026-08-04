# ADR 0002：PostgreSQL 是唯一业务事实源

> 状态：已接受
>
> 日期：2026-08-03
>
> 依据：`docs/master-plan.md` 第 3.1、3.3、5、6.2、6.3、8、17 节

## 背景

系统同时使用 PostgreSQL、Redis、MinIO、Milvus 和 Elasticsearch。

如果多个存储都被当作最终事实源，当写入、删除、重试或网络故障发生时，系统将无法判断哪一份状态可信，也无法可靠恢复。

关系数据、权限、任务状态、引用关系和审计记录需要事务、约束、查询和迁移能力，因此必须选择一个明确的业务事实源。

## 决定

PostgreSQL 是唯一业务事实源。

具体所有权如下：

1. PostgreSQL 保存用户、Workspace、membership、业务资源、Refresh Session、审计日志、Job、Outbox、最终消息、Evidence、Citation、Research 状态和其他持久业务状态。
2. Redis 只保存 Celery 队列、限流、缓存和带 TTL 的短期流事件，不保存唯一业务事实。
3. Celery result backend 不能作为业务 Job 状态或业务结果的最终来源。
4. MinIO 保存私有二进制资产，但资产所有权、Workspace、状态、bucket 和 object key 引用保存在 PostgreSQL。
5. Milvus 保存可重建的 Dense 向量索引。
6. Elasticsearch 保存可重建的 BM25 关键词索引。
7. Milvus 和 Elasticsearch 的结果必须回 PostgreSQL 重新加载，并重新校验 Workspace、active version 和资源状态。
8. 所有正式表结构变化只能通过 Alembic migration 完成。
9. 禁止使用 `Base.metadata.create_all()`、手工 ALTER 或启动时自动修改正式数据库。

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

### 对多个存储执行应用层“同时写入”

否决原因：外部存储不共享 PostgreSQL 事务；任何中途失败都会产生无法自动回滚的不一致。

### 使用 `create_all()` 自动建表

否决原因：无法提供可审查和可回放的正式迁移历史，也无法安全管理已有数据上的结构变化。

## 验证

- 在全新空库执行 `alembic upgrade head`；
- Redis 不可用时，持久业务记录仍能从 PostgreSQL 解释；
- 清空 Milvus 和 Elasticsearch 后，可以依据 PostgreSQL 重建索引；
- 检索结果必须经过 PostgreSQL Workspace 二次授权；
- 重复投递同一 Job 不产生重复业务资源或索引；
- 外部删除失败能够被对账发现；
- 备份恢复演练能够恢复 PostgreSQL 和 MinIO，并重新生成派生索引。

## 变更与回滚

任何改变数据所有权的决定都必须新增 ADR，并说明现有数据迁移、双写窗口、一致性验证、失败补偿和回滚路径。

在新方案完成迁移和对账前，PostgreSQL 仍是唯一业务事实源。
