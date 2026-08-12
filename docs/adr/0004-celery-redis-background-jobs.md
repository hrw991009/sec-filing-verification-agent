# ADR 0004：采用 Celery 与 Redis 执行异步任务

> 状态：已接受
>
> 日期：2026-08-03
>
> 修订日期：2026-08-12
>
> 依据：`docs/master-plan.md` v1.7.0 第 3.1～3.3、5.1、5.4、6.2～6.4、Day 1、Day 2、Day 5、Day 7 与 17.1 节

## 背景

文档解析、OCR、Embedding、向量索引、关键词索引、模型生成、Deep Research、数据采集、对账和评测都可能超过普通 HTTP 请求的合理时长。

如果这些任务在 API 请求中同步执行，会占用 Web 进程、难以取消、无法在 Worker 重启后恢复，也无法安全处理重复请求和外部系统故障。

FastAPI 进程内后台任务和内存字典不能提供可靠队列、跨进程状态、持久重试和故障恢复。

同时必须区分“任务在哪个进程可靠执行”和“Agent 下一步做什么”。Celery/Job Runtime 负责投递、lease、fencing、进程重启和执行尝试；Agent Runtime 负责 Run/Step/State/Event、model/tool loop、Budget、stop reason、Checkpoint 与 interrupt/resume。二者的 retry 和 Checkpoint 语义不能互相冒充。

## 决定

采用 Celery 5、Redis、独立 Outbox Dispatcher 和 Celery Beat 构成七天版本的异步执行基础设施。

职责划分如下：

1. FastAPI 验证请求并在 PostgreSQL 事务中创建业务资源、Job 和 Outbox。
2. 创建长任务的接口支持 `Idempotency-Key`，返回 `202 + job/run id + events_url`。
3. 独立 Outbox Dispatcher 根据 PostgreSQL Outbox 投递 Celery；它与 API、Worker、Beat 使用同一应用镜像，但以单独进程运行。
4. Redis 承担 Celery broker、短期事件、限流和缓存职责。
5. Celery Worker 承载解析、Embedding、索引、Agent Run、Research、采集和评测，但必须调用正式 Application Service；Agent 命令再进入唯一 `AgentRuntime.run`，Worker 不直接调用 Model Provider、选择 Tool 或推进 Research graph。
6. Celery Beat 只计算到期的计划，并以 `(schedule_id, scheduled_for)` 作为 occurrence 幂等键调用与 API/手动触发相同的 Application Service；该 Service 在同一 PostgreSQL 事务中创建或复用 ScheduleOccurrence、业务 Run、Job 和 Outbox。
7. PostgreSQL 保存业务 Job 的状态、阶段、尝试次数、错误码、进度、取消请求和最终结果。
8. Celery result backend 不能作为业务状态的最终来源。
9. Worker task 复用 Application Service，不能形成第二套业务逻辑。
10. 每个 task 使用独立 SQLAlchemy Session。
11. 普通回答、Tool Use、Research 与 Evaluation Harness 共用同一 Agent Runtime/Harness；Celery task、LangGraph 节点或测试 Harness 都不得建立第二套 model/tool loop。

Beat 不得直接调用 `send_task`、`delay` 或向 Redis 写队列消息。定时触发和手动触发必须汇合到同一个 Application Service，只有 Outbox Dispatcher 可以把持久 Outbox 发布给 Celery。Beat 在 PostgreSQL 提交前崩溃时不会留下虚假 occurrence；提交后崩溃时，下次 tick 由唯一约束复用同一 occurrence，Dispatcher 仍能继续发布。

`schedules` 持久保存 IANA timezone、cron、`next_due_at`、misfire policy、catch-up window、max catch-up 和版本；`schedule_occurrences` 以 `(schedule_id, scheduled_for)` 唯一。Beat 使用 PostgreSQL 时间、`FOR UPDATE SKIP LOCKED` 和短事务扫描所有到期计划，在同一事务内创建 occurrence/Run/Job/Outbox 并推进 `next_due_at`，因此整个停机时段结束后仍会发现遗漏。

七天默认补跑窗口为 24 小时、单次最多 100 个 occurrence。Schedule 必须显式选择逐次补跑、合并为一个覆盖完整 cursor 窗口的运行，或进入需人工处理状态；禁止静默跳过。超过窗口或数量上限时原子记录 `misfire_blocked`、暂停自动扫描、保留未推进的 `next_due_at`、遗漏区间和数量并告警，避免每个 tick 重复告警；授权补跑/恢复仍走 Application Service 与 Outbox。cron 按 IANA timezone 解释、`scheduled_for` 保存 UTC；DST 不存在时刻顺延并记录调整，重复时刻固定采用较早 offset。手动立即运行使用独立 trigger ID，不改变定时 `next_due_at`。

交付语义明确为 **at-least-once（至少一次）**，不承诺不存在重复投递。Outbox 发布、Redis broker、Worker 丢失或 ACK 丢失都可能让同一消息再次到达；业务幂等键、数据库唯一约束和外部系统确定性 ID 必须把这些重复执行收敛为一个业务结果。

Outbox Dispatcher 使用短事务和 `FOR UPDATE SKIP LOCKED` 批量抢占可投递事件，记录 `status`、`attempt`、`locked_at/locked_by`、`next_attempt_at`、`published_at` 和 `last_error_code`。发布后但标记前崩溃会产生重复消息，这是至少一次语义的一部分；消费者不能依赖“只发一次”。超过最大发布次数的事件进入 PostgreSQL 持久化 `dead_letter` 状态，必须能够查询、告警、审计和人工重放。

`published_at` 只证明 Redis 当时接受消息，不证明 Worker 已启动。Job 保存 dispatch attempt/time、start time、lease owner、单调 fencing token、lease expiry 和 heartbeat。独立对账器扫描“published/dispatched 但超过队列阈值仍未 started”和“running 但 lease 过期”的 Job，幂等创建新 Outbox 尝试或按阶段转为重试、失败、dead-letter/人工处理。每个消费者先取得当前 lease/fencing token；迟到消息或失联后仍运行的旧 Worker 不能提交新阶段或覆盖结果。

Redis broker 启用 AOF（`appendonly yes`、`appendfsync everysec`）和持久卷以缩小数据丢失窗口，但 PostgreSQL Job/Outbox、未启动对账和幂等重投才是恢复依据，不能把 Redis 持久化描述成端到端不丢保证。

## 可靠执行规则

每项任务必须定义：

- 业务 Job ID；
- 阶段状态；
- 幂等键；
- 最大尝试次数；
- 超时；
- 稳定错误码；
- 可重试和不可重试错误；
- 协作式取消；
- 外部副作用的确定性 ID；
- 补偿或对账（reconciliation）；
- 必要的 dead-letter 处理。

重复投递不得产生重复 Chunk、索引、对象、Message 或外部副作用。

Worker 中断后，系统依据 PostgreSQL 中的 Job 和阶段状态恢复或安全重试。

### Celery retry、Runtime retry 与 Provider retry

三类 retry 必须分别记录和限制：

- **Celery/Job retry**：消息重复、Worker lost、lease 过期或进程级瞬时故障导致代码重新获得执行机会。它使用 Job attempt、lease/fencing、幂等键和 Outbox，对同一个业务意图重投，不自动新增 Agent Step，也不允许旧 Worker 覆盖新结果；
- **Agent Runtime retry**：模型或 Tool 结果已经成为可观察 Step 后，由 Runtime/Harness policy 根据错误分类、Budget、deadline、max steps 和 no-progress 判定是否创建下一 Step/attempt。它必须产生 Event、usage 和明确 stop reason，禁止 LangGraph 节点或 Tool Adapter 隐式无限重试；
- **Provider/Adapter retry**：只处理连接超时、429、短暂 5xx 等明确传输级瞬时错误，具有很小的固定上限、退避和 usage/错误记录。它不能伪装成 Runtime retry，不能重复已提交副作用，也不能跨过整体 Agent deadline/Budget。

Celery redelivery 恢复同一个 Job/AgentRun 的执行所有权；用户显式“重试回答”创建可关联的新 AgentRun attempt，不篡改旧 Run；重复 resume 继续同一 Run，但必须先读取 Checkpoint 与副作用账本。输入错误、权限拒绝、Tool denied、schema 不兼容、格式不支持、no-progress 和 Budget 耗尽均不可由 Celery 自动重试来掩盖。

### Agent Checkpoint 与 Ingestion stage checkpoint

- **Agent Checkpoint** 保存同一 AgentRun 的 typed State、schema version、revision/CAS、当前 graph node、pending actions、Evidence/Claim/Artifact refs、Budget、审批、取消和 stop metadata，用于 interrupt/resume；Trace 不是恢复输入；
- **Ingestion stage checkpoint** 保存 DocumentVersion/Job 的 validating、parsing、asset extraction、chunking、embedding、vector/lexical indexing 或 deleting 阶段、阶段幂等键、外部确定性 ID 和完成结果，用于阶段重试与对账；
- 两者可以共享 PostgreSQL、版本化、CAS、幂等和 fencing 原则，但不得共用一个无类型 state JSON、revision 序列或恢复状态机；
- Agent 使用尚未 ready 的 Knowledge 时只接收 `not_ready/partial_index/dependency_failed` 等稳定 Observation，不能读取入库 checkpoint 冒充 Agent State；入库 Worker 也不能读取 Agent Trace 决定从哪个解析阶段恢复。

Day 2 只冻结通用 Agent Checkpoint envelope、schema version、CAS 和不兼容拒绝；Day 5 才完成 LangGraph state 映射、持久 interrupt/resume、HITL 和 Worker hard stop 恢复。Document ingestion 从 Day 5 起使用独立 stage checkpoint；Embedding Provider 也只在 Day 5 实现，Hybrid RAG 查询策略不属于 Celery retry，并在 Day 6 启用。

Celery 明确配置 `task_acks_late=true`、`task_reject_on_worker_lost=true`、`task_acks_on_failure_or_timeout=true` 和 `worker_cancel_long_running_tasks_on_connection_loss=true`：成功任务在 Application Service 提交业务结果后 ACK；已经持久化为可解释失败或显式重试的异常可以 ACK，并由应用创建新的受控尝试；子进程/节点丢失则 reject/requeue，再由过期 lease 对账兜底。Worker 丢失或 Broker 连接丢失时，旧执行不能与重投任务无限并行。Redis visibility timeout 必须大于该队列允许的最大 hard time limit 加安全余量。不同队列分别设置 soft/hard time limit，七天配置中任何任务 hard limit 不得超过 30 分钟，Redis visibility timeout 固定为 60 分钟。

soft time limit 触发时，Worker 尽力在安全点保存稳定超时错误。hard time limit 可能直接杀死子进程，不能依赖它自己写入终态；独立对账器根据过期 lease/heartbeat 判断崩溃或 hard timeout，再按照阶段和错误分类安全重试、标记失败、补偿或进入 dead-letter。

自动重试只用于明确的瞬时错误，例如连接超时、429 或短暂 5xx，并使用指数退避、抖动和最大次数。输入无效、权限失败、格式不支持和预算超限属于不可重试错误。代码异常不能无限重试；达到上限后 Job 进入可解释失败状态。

此处“自动重试”指受错误分类约束的 Job/Adapter 重试；Agent Runtime 是否继续思考或再次调用 Tool，必须由版本化 Harness policy 单独决定并进入 Trace/Eval。Celery `autoretry_for` 不得包住包含未受幂等保护副作用的整个 Agent loop。

协作式取消通过 PostgreSQL `cancel_requested_at` 传播。Worker 在阶段边界和长循环安全点检查取消；不能在外部副作用提交到一半时强杀进程。断开 SSE 连接只停止客户端订阅，不等于取消 Job。

## 结果

### 收益

- API 可以快速返回，不被长任务占用；
- Worker 可以独立扩展、重启和部署；
- 任务拥有持久、可观察和可解释的业务状态；
- 支持重试、取消、恢复和阶段进度；
- Beat 提供统一定时采集、清理和对账入口；
- API 和 Worker 复用同一业务规则。
- Celery 重投与 Agent Runtime 继续执行具有不同、可审计的 attempt/Step 语义；
- Agent 与入库任务分别使用 typed Checkpoint，恢复边界清晰；

### 代价与风险

- 引入至少 API、Outbox Dispatcher、Worker、Beat 和 Redis 五类运行组件；
- Celery/Outbox 的至少一次投递语义会产生重复执行，因此业务逻辑必须幂等；
- Redis 和 Worker 故障需要明确 readiness、重试和运维流程；
- 跨存储任务仍然需要 Outbox、补偿和对账；
- 取消通常是协作式的，不能假定能够瞬间杀死任意外部调用。
- 如果错误地叠加 Celery、Provider 和 Runtime retry，可能放大成本、延迟和副作用，因此三层都必须有独立上限和关联 ID。

## 否决方案

### 在 HTTP 请求中同步执行长任务

否决原因：会导致超时、连接占用、无法恢复以及 API 和后台执行边界混乱。

### 使用 FastAPI BackgroundTasks 承担正式长任务

否决原因：进程退出后任务和状态不可恢复，也不适合 OCR、索引、Research 等高成本工作。

### 使用进程内队列或字典

否决原因：状态只存在于单个进程，重启后丢失，也无法跨多个 Worker 协作。

### 仅使用 Celery result backend 保存结果

否决原因：业务状态需要关系约束、权限、审计、查询和长期保存，Redis result backend 不承担这些职责。

### 用 Celery task retry 代替 Agent Checkpoint/resume

否决原因：task retry 只重新获得执行机会，不知道 Agent 的 typed State、已完成 Step、审批和副作用结果；从头重跑可能重复模型费用、Tool Call 和外部写入。

### Agent 与文档入库共用一种 Checkpoint

否决原因：Agent Checkpoint 表达 Run/graph/approval State，入库 checkpoint 表达 DocumentVersion 的阶段与外部索引结果。混用会让 resume、删除、对账和 schema migration 无法判定正确状态机。

## 验证

- API 创建长任务后立即返回 202；
- 同一 Idempotency-Key 不产生重复业务 Job；
- 两个 Dispatcher 并发轮询不会遗漏事件，抢占超时后事件可以重新投递；
- Beat 重复 tick、进程重启和主从切换只创建一个 `(schedule_id, scheduled_for)` occurrence、一个业务 Run 和一个 Job；
- Beat 在 PostgreSQL 提交后、发布前崩溃时，Outbox 仍由 Dispatcher 发布；Redis 不可用时 occurrence 和 Job 仍可查询，恢复后能够继续投递；
- Beat 停机 24 小时后会按策略逐次或合并补跑；超过补跑上限会进入可见 `misfire_blocked`，不会静默漏跑或无限制造任务；
- 多 Beat 并发、IANA 时区、DST 不存在/重复时刻、手动立即运行和 `next_due_at` 推进均有数据库集成测试；
- 静态/架构测试阻止 Beat 直接调用 Celery 发布 API 或绕过 Application Service；
- Dispatcher 在发布后、标记前崩溃时允许重复消息，但只产生一个业务结果；
- 重复投递不产生重复副作用；
- Worker 在每个阶段强制中断后能够恢复或安全重试；
- Celery redelivery 不新增重复 Agent Step；Runtime retry 必须新增可观察 Step/Event；Provider retry 不越过 Agent deadline/Budget；
- Agent graph hard stop 从统一 Agent Checkpoint 恢复，重复 resume 不重复 Tool Call 或副作用；
- Ingestion 各阶段 hard stop 从独立 stage checkpoint 恢复，不产生重复 Chunk、Embedding、索引或 Artifact；
- Agent Checkpoint schema 不兼容时明确拒绝或迁移，Trace 和 ingestion stage state 都不能冒充恢复输入；
- Worker 完成前丢失、ACK 丢失和 visibility timeout 触发的重新投递均通过幂等测试；
- Redis 接受发布后丢失消息时，“长期未 started”对账会创建新的 Outbox 尝试，最终仍只产生一个业务结果；
- Broker 连接中断会取消失去消息所有权的长任务；迟到 Worker 的旧 fencing token 无法提交阶段或外部副作用；
- soft timeout 尽力持久化，hard timeout 由过期 lease 对账判定并恢复，测试不能假设被杀进程仍能写数据库；
- 取消、超时、最大重试和不可重试错误真实生效；
- Redis 或 Worker 不可用时，Job 在 PostgreSQL 中仍具有可解释状态；
- 超过投递或执行上限的事件进入可查询、可告警、可审计和可人工重放的 dead-letter 状态；
- 定时对账可以发现外部遗漏和孤儿；
- 用户 A 无法查看或控制用户 B Workspace 的 Job。

## 变更与回滚

替换 Celery 或 Redis 必须新增 ADR，证明新方案满足持久业务状态、幂等、重试、取消、恢复、定时调度和可观测要求。

迁移期间 PostgreSQL Job 与 Outbox 仍是业务真相。禁止长期同时维护两套正式任务执行链路。
