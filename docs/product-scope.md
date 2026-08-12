# 产品范围说明

> 计划编号：`IIP-MASTER-001`
>
> 文档状态：已接受
>
> 更新日期：2026-08-12
>
> 权威来源：`docs/master-plan.md` 1.7.0

## 1. 产品定位

行业智能工作台是一个面向行业研究与企业知识工作的多模态智能平台。

平台的核心目标不是简单提供聊天页面，而是帮助用户围绕真实资料、行业数据和外部来源，完成可追溯、可恢复、可验证的研究工作。

七天计划的能力目标，是高质量实现能力矩阵从两个只读参考项目映射出的全部目标能力，并在一致性、安全、测试、评测、可观测和可恢复性方面补齐旧项目的不足。

“择优重构”指选择更可靠的职责划分、交互方式和技术实现，并合并重复能力；它不允许静默删除能力矩阵中的任何七天目标。项目不会直接拼接旧仓库，也不会复制其中受版权保护的源码、文案、图片或素材，而是通过独立实现或合规适配器交付矩阵定义的等价目标能力。

## 2. 七天目标与诚实边界

七天学习与开发阶段的目标版本为：

```text
v0.1.0-agent-learning-foundation
```

该版本需要建立一条真实可运行的核心用户路径：

```text
注册 → 登录 → 创建知识库 → 上传 PDF → 观察异步解析
→ 选择知识库分别完成图片题与表格题 → 获得带页码、图片与表格引用的回答
→ 调用安全 Text2SQL 并查看图表 → 管理记忆
→ 发起 Research → 中断并恢复 → 查看带引用的报告和证据图
→ 在 Agent Learning Workbench 反查 Context、Tool、Memory、Evidence、Checkpoint 与 Retrieval
→ 比较 L0/L2/L3/L4/L5 以及 Memory/RAG 对照
```

七天版本必须高质量完成能力矩阵从两个参考项目映射出的全部目标能力，并达到各自冻结的七天验收深度；它不宣称把这些能力的所有数据源、格式、规模和企业运行条件都打磨到生产级成熟度。

七天结束时，Agent Runtime/Harness、Eval、Learning Workbench、身份与 Workspace、聊天、会话管理、附件、多知识库、异步文档入库、混合 RAG、多模态 Evidence、Citation、Short/Long-term Memory、工具、行业数据、安全 Text2SQL、图表、Deep Research、报告和证据图等目标能力都不能缺席。

“冻结范围”表示七天产品主动限制能力的广度，例如冻结一组真实文档夹具、一个合规 Web Search Adapter、新闻/政策/招投标/股票各一个正式 Provider 契约与至少一个真实来源样例，或一个安全 Text2SQL 样例库；它不表示可以使用低质量代码、假数据、静默 Mock、缺失权限或跳过失败测试。冻结范围内的能力必须真实、完整、可测试、可恢复。

`thin_slice`、`contract_only`、`blocked` 和 `planned` 只记录七天内的开发过程，不能冒充 Day 7 已完成。未配置外部来源在实现期间必须诚实显示未配置；相应“契约与 readiness”目标只有在合同测试、错误语义和 UI 全部通过后才改为 `complete`，但仍不能把未配置 Provider 描述成数据可用。矩阵中任何目标若没有达到冻结范围并标为 `complete`，七天计划就仍未完成。

生产级成熟度不属于本次七天验收。本文只定义 Day 1～Day 7 的能力、质量和验收。

以下内容不能算作“已完成”：

- 空页面；
- 硬编码演示数据；
- 只在前端发生的假删除；
- 静默返回成功的 Mock；
- 没有正式数据模型和错误语义的占位接口；
- 没有权限边界和测试的孤立功能；
- 没有真实 Provider 时伪造的外部数据。

未配置的外部能力必须明确返回 `PROVIDER_NOT_CONFIGURED`，不能假装成功。

## 3. 核心产品能力

### 3.1 身份与工作空间

用户可以注册、登录、查看资料、修改密码、刷新会话和退出登录。修改密码必须验证当前密码，成功后在同一事务撤销全部旧 Session，清除浏览器凭据并要求用新密码重新登录。注册成功时，系统自动创建默认 Workspace，并将注册用户设为该 Workspace 的 owner。

Refresh 与 CSRF Token 同步轮换；首次刷新已经提交但响应丢失时，5 秒内只能安全重发同一个加密保存的 successor，不能创建第二条 Session 链。浏览器认证采用 same-site Web/API 和 Secure `__Host-` Cookie，开发环境也不能混用 `localhost` 与 `127.0.0.1`。

Workspace membership 使用 ADR 0006 冻结的 owner/admin/member/viewer 服务端动作矩阵：viewer 只读，member 可处理普通业务资源，admin 只能管理 member/viewer，只有 owner 能任命高权限角色和修改/删除 Workspace。用户不能自提权，最后一个 owner 不能被移除或降级，并发角色变化必须通过行锁和约束保持至少一名 owner。

所有租户业务资源必须受 Workspace 边界保护。用户 A 不得读取、修改或检索用户 B 所属 Workspace 的数据。

### 3.2 可恢复的流式聊天

用户可以创建、列表查看、打开、重命名和删除会话，分页查看消息，发送消息与附件，获得自动标题，并为每轮选择 `none/web/local/both` 搜索模式、当前行业和一个或多个知识库。用户可以查看流式回答、停止生成、重试失败回答，并在刷新或断线后恢复历史与可恢复事件。

生成失败时，系统必须保存用户消息、已经生成的部分内容、失败原因和可重试状态，不能因为模型失败而丢失用户输入。

### 3.3 私有知识库与异步文档入库

用户可以创建多个知识库，并通过私有 MinIO 上传文件。七天范围真实支持 PDF、TXT 和 Markdown；必须分别用一份数字文本 PDF、一份需要 OCR 的扫描 PDF，以及另一份同时含图片和复杂表格的 PDF 完成解析、资产预览、检索和回答闭环，不能用一份容易的样本代替三类能力验收。

上传请求只负责接收文件并创建任务。解析、资产抽取、Chunk、Embedding 和索引必须由 Worker 异步执行。

用户可以观察入库阶段、进度和失败原因，并执行重试、取消或重新索引。

### 3.4 混合检索与结构化引用

系统并行使用 Milvus Dense 检索和 Elasticsearch BM25 检索，通过 RRF 融合，并允许插入 Reranker。

检索必须过滤当前 Workspace、用户选中的知识库、状态为 ready 的文档和当前有效索引版本。索引结果返回后，必须回到 PostgreSQL 重新加载和授权，不能只信任索引中的权限字段。

最终回答中的 Citation 必须能够定位到真实的文档版本、页码、bbox、文本片段、图片、表格、网页段落或 SQL 结果。

### 3.5 用户可控记忆

Short-term Memory 保存 Thread 内的消息引用、摘要、compaction revision、freshness 和实际 Context 注入清单；它与当前模型窗口、Run State、Checkpoint 和 Long-term Memory 分开建模，不能未经决策自动提升为长期事实。

记忆必须允许用户查看、确认、编辑、停用和删除，也必须允许用户关闭自动记忆功能。

Long-term Memory 必须记录 provenance、scope、confidence、写入原因、策略或用户决定和版本修订。系统不能默认永久保存全部聊天，也不能保存原始 chain-of-thought；删除或停用后，后续 Context manifest 和回答不得继续引用该记忆。

### 3.6 工具与行业数据

系统通过统一 Tool Registry 管理知识检索、Web 搜索、行业资讯、政策、招投标、股票、数据库浏览、Text2SQL 和图表渲染。

工具必须具有输入和输出 Schema、所需权限、超时、调用预算、审计记录和稳定错误语义。

七天内必须接通一个合规的真实 Web Search Adapter，并为新闻、政策、招投标和股票各接通至少一个合规真实来源样例。它们使用公共来源基表与领域明细表，保留原链接、发布时间、采集时间、内容哈希和许可证或使用约束。Celery Beat 只计算到期计划，并通过与手动触发相同的 Application Service 持久化 ScheduleOccurrence、Collection Run、Job 和 Outbox；Dispatcher 再发布任务。持久 Schedule 明确 IANA timezone、next due、24 小时/100 次补跑上限和超限 `misfire_blocked`，不能在停机后静默漏跑。该链路同时提供游标、幂等外部 ID、去重、退避、最后成功时间和 dead-letter 状态。

同一领域的额外未配置 Adapter 必须明确显示为未配置，禁止返回假数据。

### 3.7 安全 Text2SQL

Text2SQL 使用独立只读数据库账户，并限制允许访问的 schema、table 和 column。

模型生成的 SQL 必须通过 sqlglot 解析完整 AST，只允许受控的 SELECT 或 CTE。系统必须拒绝多语句、DML、DDL、COPY、CALL、危险 CTE，以及超出行数、时间或扫描预算的查询。

通过校验的查询结果可以进入表格和图表，但模型只能生成受限 ECharts JSON Schema；服务端和前端必须拒绝脚本、函数、外链和超预算数据，不能执行模型生成的 JavaScript。

### 3.8 可恢复的 Deep Research

Deep Research 使用 LangGraph 建立一个正式 typed graph，支持计划、多源检索、Claim 提取、分析、写作、核验、有上限的修改、Checkpoint、中断、恢复、取消和预算。

研究报告的每个关键 Claim 必须关联 Evidence，并能从 Claim/Evidence 生成基础关系图和受校验的 ECharts 视图。用户可以查看研究时间线、来源、审批请求、审批决定与继续执行、取消、恢复、报告和证据图。

普通 CRUD、身份、普通聊天和文档入库不得为了“Agent 化”而使用 LangGraph。

### 3.9 Agent Runtime、Harness、Eval 与学习型可视化

普通回答、Tool Use 和 Deep Research 共用一套 Provider-neutral Agent Runtime，统一 `Run/Step/Event/State/Budget/stop reason`、Context manifest、Checkpoint 和 Trace。Agent Harness 在 Runtime 上组合 Instructions、Tool/Skill、Memory、Knowledge/RAG、Approval、Artifact 与 Eval hook，不建立第二套模型或工具循环。

Evaluation Harness 使用同一 Runtime/Harness 执行版本化 Scenario、Fake/Replay、Fault injection 和 Scorer。数据集从 Day 2 起逐日累计，分别评价结果、轨迹、Evidence、Memory、Knowledge/RAG、恢复、Token、费用和延迟；Trace 记录执行事实，Eval 负责评分，两者不能互相冒充。

Agent Learning Workbench 完整展示 Run/Context、Tool、Memory、Evidence/Claim、Knowledge locator、Checkpoint/HITL、Retrieval/Citation 和 Report。复杂前端可由独立前端实现工作流依据 OpenAPI、Event、Trace、Manifest 与状态契约并行交付，其编码工时不计入学习者的核心概念时段；页面、交互、真实数据链路和 Playwright 门禁仍属于正式产品范围。

## 4. 每日范围

| 阶段 | 主要范围 | 核心门禁 |
|---|---|---|
| Day 1 | 工程地基、身份、Workspace、基础设施、健康检查、CI | 注册登录与跨 Workspace 负向测试通过 |
| Day 2 | Agent Runtime v0、基础 Harness、流式直接回答与会话 | 唯一 Runtime、固定 Scenario、可恢复 SSE 与 Run/Context Trace 闭环 |
| Day 3 | 有界 Tool Use、行业能力和 Text2SQL | Tool Schema/scope/预算不可绕过，真实来源与 Artifact 可追溯 |
| Day 4 | Short/Long-term Memory、Evidence 与 Research L3 | Memory 全生命周期可治理；Claim/Evidence、coverage 和不确定项可解释 |
| Day 5 | Agent Knowledge、异步入库与 Durable Research L4 | 私有知识可引用；Checkpoint/HITL 恢复且不重复副作用 |
| Day 6 | Hybrid RAG、多模态 Context 与 Research L5 | 引用可解析、跨租户召回为零，Verifier 与 bounded revise 有正式报告 |
| Day 7 | Agent Eval、故障回归、统一 Workbench 与完整交付 | ≥50 场景、完整用户路径、恢复门禁、迁移、CI 和密钥扫描全绿 |

任何一天的门禁没有通过，计划必须顺延，不能通过删除测试、放宽权限、伪造数据或跳过迁移来赶进度。

复杂前端页面和学习型可视化保留为正式交付，由独立前端实现工作流按 OpenAPI、Event、Trace、Manifest 和状态契约并行实现，不计入学习者的核心概念工时。学习者仍须能够使用这些页面解释 Runtime、Tool、Memory、Evidence、Checkpoint、Knowledge/RAG 与 Eval 的真实行为。

逐项目标、参考来源、冻结范围、验收证据和当前事实状态见 [七天目标能力矩阵](feature-matrix.md)。每日摘要不能代替该矩阵；Day 7 必须逐行验收。

## 5. Day 1 纵向切片

Day 1 的目标用户旅程为：

```text
注册 → 创建默认 Workspace → 建立 owner membership
→ 登录 → 获取当前用户信息 → Refresh Token 轮换
→ Logout → 前端 Auth Guard → 进入受保护首页
```

该切片同时依赖 PostgreSQL、Redis、MinIO、Alembic、Pydantic Settings、FastAPI、React、统一 API Client、OpenAPI 类型生成、真实健康检查、CI、密钥扫描以及正常、失败、权限和浏览器测试。

Day 1 未满足完整用户路径前，不能进入 Day 2。

## 6. 质量属性与完成标准

高质量不是“代码能够运行”这一项，而是功能正确、数据可信、权限安全、故障可恢复、性能可度量、问题可观察、发布可回退，并且用户能够完成真实工作。下列要求约束 Day 1～Day 7 的每个纵向切片和七天完整交付，不能因时间紧而取消门禁。

### 6.1 功能完整性与诚实状态

每项能力必须登记为 `complete`、`implemented_pending_verification`、`thin_slice`、`contract_only`、`blocked` 或 `planned`，禁止用模糊百分比表示完成度。这些状态描述当前事实进度，不是“高质量、低质量”的等级。`implemented_pending_verification` 表示正式实现已经写入，但本轮适用的统一门禁、真实依赖验证或干净 CI 尚未全部实际通过；它不是 `complete` 的别名。

七天验收时，矩阵中的每项目标都必须在预先冻结的范围内通过适用的安全、测试、失败、恢复、文档和兼容门禁，并标为 `complete`。任何矩阵目标仍为 `implemented_pending_verification`、`thin_slice`、`contract_only`、`blocked` 或 `planned`，都表示七天计划没有完成。

面向用户的业务能力只有在具有真实旅程，并通过适用的后端行为、正常/空/失败/无权/取消或恢复 UI、持久化模型、权限与输入校验、测试、日志、错误码和文档门禁后，才可能标记为 `complete`。工程、文档和治理目标按其开发者/运维旅程逐项评审适用性，并记录 `N/A` 理由与复核人。空页面、硬编码数据、静默 Mock、孤立接口和没有证据的口头结论都不属于完成。

Day 7 前至少形成 50 条可重复评测题和七天功能矩阵。矩阵必须覆盖两个参考项目映射出的全部目标能力，并分别记录七天验收深度和实际状态。任何目标没有达到其冻结的七天验收深度，都表示七天计划仍未完成。

### 6.2 数据正确性与所有权

PostgreSQL 是唯一业务事实源。Redis、Milvus 和 Elasticsearch 属于可恢复执行层或派生层。MinIO 是受 PostgreSQL 引用和权限管理的私有资产层。

跨 PostgreSQL、MinIO、Milvus 和 Elasticsearch 的流程必须通过 Outbox、确定性外部 ID、阶段状态、幂等、补偿、对账和孤儿检测保持可恢复的最终一致性，不能假装一次数据库回滚可以回滚全部存储。

Outbox 标记 published 不能被解释为任务已经启动。Job 必须具有 dispatch/start、lease、fencing token 和 heartbeat；独立对账器会重投“已发布但长期未 started”的 Job，并处理 hard timeout 或失联 Worker 的过期 lease。Redis AOF 只缩小丢消息窗口，不能替代 PostgreSQL 对账、幂等与唯一约束。

数据库约束、并发冲突、重复投递、部分失败、删除和重建都必须有自动化测试或故障演练证据。

### 6.3 租户隔离、安全与隐私

所有租户业务资源必须具有 `workspace_id`。所有 Repository 查询必须显式接收 WorkspaceScope。索引检索结果必须回 PostgreSQL 重新执行 Workspace 授权。跨租户泄漏的验收目标为 0。

系统不得：

- 在源码、`.env`、前端 `VITE_*`、日志或测试快照中保存真实密钥；
- 把 Refresh Token 放入 LocalStorage；
- 公开 MinIO Bucket；
- 在带凭据请求中使用通配 CORS；
- 信任文档、网页、SQL 结果或模型输出；
- 在 API 或 Worker 进程中执行模型生成的代码；
- 保存模型原始 chain-of-thought；
- 允许模型自行扩大工具权限或预算；
- 使用未消毒 HTML；
- 返回长期公开对象 URL。

每项能力完成前必须做威胁与隐私检查。认证、授权、上传、SSRF、提示注入、工具越权、Text2SQL、对象访问、日志脱敏和数据删除必须具有负向测试；高危 SQL 拒绝率必须为 100%。

### 6.4 可靠性、可恢复性与数据生命周期

长任务必须具有持久 Job 状态、阶段状态、幂等键、重试上限、稳定错误码、取消状态、Outbox、补偿和对账。不能依赖 API 或 Worker 进程中的内存字典保存业务状态。

LLM 失效、Worker 重启、Redis、Elasticsearch 或 MinIO 故障、重复任务、迁移失败和网络超时必须产生可解释状态，且不得静默丢失用户输入或制造重复副作用。

删除必须覆盖 PostgreSQL 关系、MinIO 对象、Milvus/Elasticsearch 派生索引以及相关缓存。发布前至少完成一次 PostgreSQL/MinIO 备份、删除测试数据、恢复数据以及从 PostgreSQL 重建索引的演练，并验证上一镜像可以回退。

### 6.5 性能、容量与成本

性能不能以“本机感觉够快”验收。关键路径必须记录并报告：

- API 的请求量、错误率和 p50/p95/p99 延迟；
- Job 的排队时间、阶段耗时、成功率、重试率和积压量；
- 文档解析、Embedding、索引与删除的吞吐和资源消耗；
- Dense、BM25、Rerank、LLM 和完整 RAG 的分段延迟；
- SSE 首事件时间、中断响应时间、断线恢复和重复事件情况；
- LLM/Research 的 Token、费用、步骤、并发和总运行时间；
- 外部 Provider 的可用率、限流、数据新鲜度和失败类型。

七天内必须建立可重复的性能基线、资源预算和运行上限，并完成主计划规定的故障测试。七天计划不虚构尚无真实生产流量支撑的高可用 SLO。任何检索参数或性能优化都必须有评测数据支持，不能用降低正确性、安全性或可恢复性换取表面速度。

### 6.6 AI、检索与证据质量

RAG、Agent 和性能相关功能必须进入可重复评测基线。至少评估召回、排序、Citation 可解析率、回答忠实度、拒答、跨租户隔离、延迟和费用。

Day 7 的 Citation 可解析率必须为 100%，RAG 基线不得低于 Day 6；相对已接受基线下降超过 2 个百分点时门禁失败。七天目标所覆盖的文档、行业 Provider、Text2SQL 和 Research 场景必须进入黄金集、回归集、人工抽检和来源冲突检查。

模型输出不是事实源。每个关键 Claim 必须能够回到用户有权访问的 Evidence；无法获得足够证据时，应明确拒答或表达不确定性，不能生成伪引用。

### 6.7 可观测性与审计

API、Worker、检索、Provider、LLM 和 Research 链路必须使用结构化 JSON 日志、指标和 Trace，并统一携带适用的 `request_id`、`trace_id`、`job_id`、`stream_id` 或 `run_id`。

日志不得记录 Secret、Cookie、完整 Prompt、全文文档、原图或其他敏感原文。授权变更、Token Session、工具调用、Text2SQL、对象访问、删除和管理操作必须具有可查询审计记录。

错误必须具有稳定错误码、用户可理解的提示和开发者可定位的关联 ID。关键错误率与延迟、队列积压、Provider 失败、索引不一致和备份失败必须进入指标、仪表盘或可验证告警。

### 6.8 可维护性与可演进性

模块必须遵守 Router → Application Service → Repository/Port 的依赖方向，Worker 复用同一 Service，Provider SDK 只进入 Adapter。禁止重复正式链路、千行万能 Service、跨模块绕过契约和为目录图创建空文件。

OpenAPI 是前后端唯一契约源；SSE 信封必须版本化并支持向前兼容。Provider、Parser、Retrieval 和 Tool 通过稳定 Port 隔离，使七天内的全部目标能力能够共用一套正式业务模型，而不是形成临时链路。

影响技术栈、数据所有权、安全边界、模块职责或七天范围的变化必须更新 ADR，并说明迁移、兼容和回滚。依赖和镜像升级必须通过测试、漏洞扫描和回归评测。

### 6.9 可复现、可部署与可使用

项目固定 Python、Node.js、uv 和 pnpm 版本。Python 与 Node 依赖必须进入锁文件。CI 必须在干净环境中执行格式、lint、类型检查、测试、构建、fresh migration、契约检查、密钥扫描、依赖扫描和镜像扫描。

数据库结构只能通过 Alembic 创建和修改。新环境必须能够按照 README 和 Runbook 启动，Compose 服务必须具有 healthcheck、资源限制、持久卷、优雅关闭和明确迁移步骤。

关键页面必须覆盖 loading、empty、error、forbidden、retry、cancelled 和 partial 状态，提供明确下一步操作，并通过语义化结构、基本键盘操作和真实浏览器 E2E 验证核心旅程。

### 6.10 全局 Definition of Done

任何目标改为 `complete` 前，都必须先逐条评审以下条件是否适用。适用项必须全部通过；`N/A` 必须写清为什么与该目标无关、由谁复核，不能用来逃避实现。面向用户的业务能力不得把真实用户旅程、服务端权限、正常/失败/恢复测试或安全检查标为 `N/A`。工程、文档和治理目标可以用等价的开发者或运维旅程及自动化校验替代；只有目标确实不改变数据库、HTTP/SSE 或运行时行为时，才可把相应 migration、契约或遥测项记为 `N/A`。

1. 存在真实业务用户旅程；工程、文档或治理目标则存在可重复的开发者/运维验收旅程，不是孤立接口、空页面或口头结论；
2. 正常、边界、失败、权限和恢复测试齐全；
3. 具有 Alembic migration、OpenAPI/SSE 契约和兼容策略；
4. 具有结构化日志、指标、Trace 和稳定错误码；
5. 具有数据所有权、删除、补偿、备份与恢复策略；
6. 完成威胁与隐私检查，日志和前端没有 Secret 或敏感原文泄漏；
7. 审核第三方源码、素材、模型、数据源和依赖的许可证与使用条款；需要时保留 NOTICE、归属和修改说明，不引入来源不明或不兼容内容；
8. RAG、Agent 和性能相关能力进入可重复评测基线；
9. README 或 Runbook 写清启动、限制、故障、迁移与回滚；
10. 清除调试输出、硬编码、静默 Mock、临时旁路和重复正式链路；
11. 在干净环境或 staging 完成演示或等价的可重复验收。

测试结构建议参考 60% 领域单元测试、25% 组件与集成测试、10% 契约测试、5% 关键 E2E；比例用于发现失衡，不是为了凑数量。核心 domain/application 覆盖率必须不低于 90%，后端总体不低于 80%，前端关键 Hook/状态不低于 75%。Flaky test 必须修复，不能长期依赖 rerun 掩盖。

## 7. 七天结束验收口径

七天验收必须做一次双向能力审计：

1. 从两个参考项目出发，确认能力矩阵的每项目标都已映射到 Day 1～Day 7 的实现任务、测试和门禁；
2. 从新项目出发，确认每项能力只有一条正式业务链路，没有临时旁路、重复数据模型或第二套入口；
3. 对每项能力核对真实用户结果、七天验收深度、自动化测试、失败恢复、安全边界、质量指标和已知限制；
4. 运行完整用户路径、fresh migration、CI、密钥扫描、依赖/镜像扫描、RAG 回归和备份恢复演练；
5. 任一目标能力未达到规定深度，或任一最低质量门禁失败，七天计划即未完成并顺延，不能通过改名为“后续优化”绕过。

七天交付物是高质量、功能完整但尚不宣称生产级的 `v0.1.0-agent-learning-foundation`。本文到七天验收为止。

## 8. 当前实现状态

截至 2026-08-12，仓库已经写入以下 Day 1 正式实现：

- 精确运行时、uv/pnpm workspace、锁文件、Python/Web/浏览器质量门和固定 SHA 的 GitHub Actions；
- PostgreSQL、Redis、私有 MinIO 默认 Compose，以及 tools、vector、search、observability profiles；
- FastAPI、Pydantic Settings、真实 `live/ready`、统一错误语义和 Alembic 迁移；
- 注册、登录、`me`、修改密码、Logout、Ed25519 Access Token、Refresh/CSRF 轮换与恢复、登录限流；
- 默认 Workspace、owner membership、四角色服务端策略、跨租户拒绝与最后 owner 保护；
- React 身份旅程、内存 Access Token、统一 API Client 和由 OpenAPI 生成的 TypeScript 契约；
- PostgreSQL Job/JobEvent/Outbox/Schedule/Occurrence、独立 Dispatcher、Celery Worker、lease/heartbeat/fencing、Reconciler 和数据库驱动 Beat；
- 对应的领域、HTTP、PostgreSQL、Redis、契约和真实浏览器测试代码。

这些实现已经通过最终统一 formatter、全量本地门禁和提交 `2c4e6e9` 的干净 CI，因此能力矩阵中的 D1-02～D1-08、D1-10～D1-12 均已复核为 `complete`。

两个参考仓的脱敏扫描已经发现 6 组待处置候选，证据见[参考仓凭据暴露审计](security/credential-exposure-audit.md)。Provider 侧吊销/轮换尚未完成且候选仍全部为 `open`，因此 D1-09 保持 `thin_slice`；即使当前代码门禁全绿，也不能虚假关闭这一外部安全门禁。

D1-09 是独立的外部治理尾项：它不否定已完成的新仓 Day 1 工程门禁，也不阻断 Day 2 Agent 学习；但在 6 组候选全部吊销/轮换并复扫前，它仍阻断 Day 7 发布标签。

Day 2～Day 7 的 Agent Runtime/Harness、聊天、Tool Use、Short/Long-term Memory、Knowledge/RAG、Deep Research、Evaluation Harness 与 Learning Workbench 仍未实现。Day 1 的工程实现与自动化门禁已经完成；D1-09 的 6 组参考仓凭据仍是独立的外部处置尾项，只有完成 Provider 侧处置和复扫后，Day 1 全部矩阵项才能无保留地标为 `complete`。
