# ADR 0005：LangGraph 仅用于 Deep Research

> 状态：已接受
>
> 日期：2026-08-03
>
> 依据：`docs/master-plan.md` 第 3.2、3.3、5.4、6.5、8、13、17 节

## 背景

Deep Research 需要计划、多源检索、Claim 提取、分析、写作、核验、修改、Checkpoint、中断、恢复和预算控制，适合使用显式状态图表达。

普通 CRUD、身份、普通聊天和文档入库具有更直接的事务或任务状态机。如果为了“Agent 化”把所有功能都放入 LangGraph，会隐藏业务状态、增加调试难度，并形成图内和图外两套正式执行逻辑。

项目需要明确 LangGraph 的适用边界。

## 决定

LangGraph 只用于 Deep Research 的正式状态图，不渗透普通 CRUD、身份、普通聊天、知识库管理或文档入库。

Research 使用一个 typed `ResearchState`，正式流程为：

```text
scope → plan → parallel_retrieve(knowledge/web/industry/sql)
→ extract_claims → analyze → outline → write
→ verify → revise（最多 N 次）→ finalize
```

具体约束如下：

1. 使用一张正式图，不能定义图后再绕开图手工执行同一 Research。
2. 每个节点结束后保存 PostgreSQL-backed checkpoint。
3. 恢复从最后一个成功节点继续。
4. 节点前和节点中的外部副作用必须幂等。
5. Research 设置最大步骤、并发、Token、费用、运行时间、revise 次数和工具 allowlist。
6. 取消必须传播到图和底层协作式任务。
7. 高成本或有外部副作用的操作通过 interrupt 请求人工确认。
8. resume 后不得重复已经完成的外部副作用。
9. 每一步保存输入和输出摘要、Evidence、Token、费用、耗时、错误和 checkpoint。
10. 不保存原始 chain-of-thought，只保存面向用户的结果、证据和简短 reasoning summary。
11. Research 模块通过 Port 使用检索、行业数据、数据库、工具和模型能力，不能直接导入具体供应商 SDK。

## 结果

### 收益

- Research 状态和节点边界清晰；
- 能够在 Worker 中断后从 checkpoint 恢复；
- 预算、工具权限、取消和人工确认具有统一执行位置；
- Fake LLM 和 Fake Tool 下可以确定性测试事件序列；
- Report、Claim 与 Evidence 可以跟随正式状态图产生；
- 普通业务保持简单，不承担无必要的 Agent 复杂度。

### 代价与风险

- ResearchState 和 checkpoint 需要版本化；
- 节点输入、输出和副作用必须满足可重放约束；
- 图升级时需要考虑已有 Run 的兼容和迁移；
- interrupt、resume 和取消传播会增加状态测试量；
- 如果节点职责过大，状态图仍可能退化为名义编排；
- 如果图外存在第二条 Research 链路，将导致状态和审计漂移。

## 否决方案

### 所有业务都使用 LangGraph

否决原因：CRUD、身份和普通任务没有多步骤 Agent 状态需求，强行进入图只会增加复杂度和隐藏事务边界。

### 定义状态图，但在线上手工调用节点

否决原因：这会形成名义编排和正式手工链路并存，checkpoint、取消和恢复都无法可信工作。

### Research 仅使用临时内存状态

否决原因：Worker 重启后无法恢复，也不能提供持久审计、预算和用户可见进度。

### 无限循环的自主 Agent

否决原因：模型不能自行扩大步骤、工具权限、Token、费用和时间预算。

## 验证

- Fake LLM 和 Fake Tool 下事件序列确定可复现；
- 每个节点成功后保存 checkpoint；
- 在中间节点强制终止 Worker 后，从最后 checkpoint 恢复；
- 重复 resume 不重复外部副作用；
- 取消、预算耗尽、最大步骤和最大 revise 真实生效；
- 未授权工具请求被拒绝；
- 其他 Workspace 无法读取 Run、Checkpoint、Report 或 Evidence；
- Report 的关键 Claim 都能解析到真实 Evidence；
- 数据库中不存在原始 chain-of-thought。

## 变更与回滚

ResearchState、节点顺序或 checkpoint Schema 变化必须新增或更新 ADR，并提供版本兼容、已有 Run 迁移和回滚方案。

如果新版图无法继续处理旧 Run，必须明确将旧 Run 标记为不可恢复并给出用户可见错误，不能静默从头执行或重复副作用。
