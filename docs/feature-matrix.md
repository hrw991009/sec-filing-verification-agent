# 七天目标能力矩阵

> 计划编号：`IIP-MASTER-001`
>
> 文档状态：已接受
>
> 更新日期：2026-08-20
>
> 权威来源：`docs/master-plan.md` 1.7.0

## 1. 使用规则

本矩阵回答三个问题：两个参考项目映射出了哪些目标能力、七天内完成到什么边界、当前是否真的已经完成。矩阵中的行就是本计划冻结的完整目标清单，不允许在执行中临时挑选或静默删减。

矩阵每一目标行在 Day 7 的目标状态都是 `complete`。冻结范围可以限制格式、Provider 数量或数据规模，但不能降低实现质量；范围内必须通过适用的真实链路、正式数据模型、权限、失败恢复、自动化测试、错误码和文档门禁。

当前状态只使用：

- `complete`：冻结范围内已经逐条评审全局 Definition of Done，所有适用项通过，所有 `N/A` 都有具体理由与复核人，并附实际证据；
- `implemented_pending_verification`：正式代码、迁移或文档已经实现，但本轮适用的统一门禁、真实依赖验证或干净 CI 尚未全部实际通过；它不是完成状态；
- `thin_slice`：已有部分真实链路，但冻结范围仍有缺口；
- `contract_only`：只有正式契约或 readiness，没有真实用户闭环；
- `blocked`：存在已记录且当前无法解除的外部阻塞；
- `planned`：尚未开始实现。

`implemented_pending_verification`、`thin_slice`、`contract_only`、`blocked` 和 `planned` 都不能作为 Day 7 最终状态。状态变化必须附带测试、评测、演练或真实用户路径证据，不能只凭代码存在或页面截图改为 `complete`。面向用户的业务目标不得把真实旅程、服务端权限、失败/恢复或安全检查标为 `N/A`；工程、文档和治理目标可用等价的开发者/运维旅程与自动校验替代。

D1-09 的参考仓历史凭据处置需要 Provider 侧外部操作。它不否定 D1-01～D1-08、D1-10～D1-12 已完成的新仓工程门禁，也不阻断 Day 2 学习；但在 6 组候选全部吊销/轮换并复扫前，D1-09 仍阻断 Day 7 发布标签。

## 2. 参考项目标识

| 标识 | 只读参考项目 | 主要可借鉴能力 |
|---|---|---|
| R1 | `D:\my_work_project` | PDF 版面/OCR、图片表格资产、Dense + BM25 + RRF、多模态 RAG、检索调试 |
| R2 | `D:\industry_information_assistant\industry_information_assistant` | 身份、首页行业上下文、聊天、多知识库、资讯/政策/招投标/股票、数据库、记忆、Research、图表和报告 |
| NEW | 新项目质量增强 | Workspace 隔离、统一 Evidence、可靠异步任务、安全 Text2SQL、CI、评测、安全、可观测和恢复 |

### 2.1 参考项目双向覆盖审计

下表从参考项目能力反查矩阵 ID，防止只从新项目视角列计划而漏掉参考目标。这里的“全部目标能力”指两个参考项目映射并冻结到本矩阵的全部用户结果，不等于复制旧仓库的重复代码、占位页面、安全缺陷或未接通的内部文件。

| 参考来源 | 参考能力组 | 对应矩阵目标 |
|---|---|---|
| R1 | PostgreSQL、Milvus、Elasticsearch、MinIO、Alembic 与可重建索引 | D1-02、D1-11、D5-02～D5-06、D6-01～D6-03、D7-06 |
| R1 | PDF 版面、OCR、图片、复杂表格与解析资产 | D5-03～D5-07、D6-04、D6-05 |
| R1 | Dense、BM25、RRF、重排与检索调试 | D6-01～D6-03、D6-07 |
| R1 | 多模态 RAG、Evidence 与可定位引用 | D6-04～D6-07、D7-07 |
| R2 | 注册登录、资料、Workspace 角色与身份页面 | D1-04～D1-06 |
| R2 | 首页与当前行业上下文 | D3-01、D7-01 |
| R2 | 会话、搜索模式、附件、流式聊天与恢复 | D2-01～D2-07 |
| R2 | 多知识库、文档状态与详情 | D5-01～D5-07 |
| R2 | Web、新闻、政策、招投标、股票与定时采集 | D3-03～D3-08 |
| R2 | 数据库浏览、Text2SQL 与图表 | D3-09～D3-11 |
| R2 | 记忆创建、使用与管理 | D4-01、D4-02 |
| R2 | Research 状态图、Checkpoint、四类详情、图表与报告 | D4-04～D4-07、D5-09、D6-08 |

### 2.2 正式模块与数据所有权登记

每个 ID 都必须落在下列唯一正式位置；实现时再在同一行的验收记录中补充具体 migration、端点、测试、CI/评测链接、限制和 DoD `N/A` 复核。没有业务数据库的工程/文档目标也必须明确写出事实所有者，不能假装由 PostgreSQL 管理。

| 目标 ID | 正式代码/文档位置 | 主要事实或数据所有者 |
|---|---|---|
| D1-01、D1-08～D1-10 | 仓库根配置、`.github/workflows`、`docs/`、`scripts/` | Git 提交、锁文件、GitHub Actions 与受版本控制文档 |
| D1-02、D1-03、D1-11、D1-12 | `infra/`、后端 platform/config/db 与 `jobs`、Alembic migrations | PostgreSQL schema/Job/Outbox/Schedule/history；Redis 是可恢复 broker；Compose 与环境配置由 Git 管理 |
| D1-04、D1-05 | 后端 `identity` | `users`、`refresh_sessions`、`workspaces`、`workspace_members`、`audit_logs` |
| D1-06 | `apps/web` identity/profile/shell 与后端 `identity` | 身份事实仍在 PostgreSQL；浏览器只保留短期 UI 状态和内存 Access Token |
| D1-07 | FastAPI OpenAPI 与 `packages/api-contract` | OpenAPI 为唯一契约源，生成物由 Git 管理 |
| D2-01～D2-09 | 后端 `agent_runtime`、`agent_harness`、`conversation`、`files`、`jobs`、LLM/Web ports；前端 chat | PostgreSQL 持有 AgentRun/Step/Event/Checkpoint envelope、Session/Turn/Message、ContextManifest、Artifact 与 Job/Outbox；Redis 只存短期 delta/流事件；附件在私有 MinIO |
| D3-01～D3-08 | `industry`、`tools`、connector adapters；前端 industry/home/chat card | PostgreSQL 持有行业、公司、来源、采集、指标及 ToolCall/Observation 引用；外部 Provider 只提供输入 |
| D3-09～D3-11 | `data_explorer`、`tools`；前端 database/chart | PostgreSQL Connection/Schema Snapshot/Query Run/Chart Spec；查询结果大对象在私有 MinIO |
| D4-01～D4-07 | `memory`、`research`、`evidence`；前端 Agent Learning Workbench | PostgreSQL Short/Long-term Memory、ResearchBrief、Run、Evidence/Claim；Redis 仅短期事件 |
| D5-01～D5-09 | `files`、`knowledge`、`ingestion`、`research`、parser adapters；前端 knowledge/checkpoint | PostgreSQL 文档/版本/Chunk/任务关系与 Agent Checkpoint/Approval，MinIO 原件与资产，Milvus/ES 可重建索引 |
| D6-01～D6-08 | `retrieval`、`context`、`research`、`evidence`、`evaluation`；前端 retrieval/report Workbench | PostgreSQL 持有 ContextManifest/Evidence/Citation/Claim/Report；Retrieval Trace 进入观测与版本化评测证据而非恢复事实源；Milvus/ES 是可重建候选索引；版本化数据集与报告由 Git/对象存储管理 |
| D7-01～D7-10 | 跨模块、`agent_harness`、`evaluation`、`tests/`、`infra/`、`docs/runbooks/`、前端 Agent Learning Workbench | 各业务事实仍由原模块拥有；Scenario/Scorer/报告、CI、Trace 基线、演练和审计证据分别进入版本化数据集、Git/GitHub 与观测后端 |

## 3. Day 1：工程地基、身份与 Workspace

| ID | 目标能力与用户结果 | 来源 | 冻结的七天范围 | 验收证据 | 当前状态 | Day 7 |
|---|---|---|---|---|---|---|
| D1-01 | 可复现 Monorepo 与运行时 | NEW | uv/pnpm workspace、精确运行时、锁文件、单一后端入口 | 新 clone 锁定安装、Python/Node build | `complete` | `complete` |
| D1-02 | 本地基础设施 | R1 + NEW | PostgreSQL、Redis、私有 MinIO 默认启动；Milvus、Elasticsearch、观测 profile 验证 healthcheck | Compose 启动/停止、非公开端口、健康故障测试 | `complete` | `complete` |
| D1-03 | 配置与真实健康检查 | NEW | Pydantic Settings、`.env.example`、环境边界、fail fast、`live/ready` | 缺配置启动失败；PG/Redis 故障时 ready 失败、live 仍响应 | `complete` | `complete` |
| D1-04 | 注册、登录与账户安全 | R2 + NEW | email 注册/登录、`me`、修改密码（验证当前密码并撤销全部旧 Session）、EdDSA Access Token、Refresh/CSRF 同步轮换、5 秒加密 successor 响应恢复、Logout、same-site Cookie | 重复账号、错误/相同新密码、改密事务与旧 Session 失效、伪造 Token、CSRF、并发/响应丢失刷新、grace 内外重放、hostname/same-site 和限流测试 | `complete` | `complete` |
| D1-05 | Workspace 与成员角色 | R2 + NEW | 注册创建默认 Workspace 和 owner；冻结 owner/admin/member/viewer 权限矩阵、成员增删/改角色规则、禁止自提权和最后一个 owner 保护；服务端 membership 授权 | 每个角色允许/拒绝矩阵、并发最后 owner、用户 A 访问用户 B 资源全部拒绝 | `complete` | `complete` |
| D1-06 | 前端身份旅程 | R2 + NEW | 登录/注册、Auth Guard、用户资料、修改密码、基础导航、内存 Access Token、刷新恢复 | Playwright 注册 → 登录 → 首页 → 修改密码 → 旧会话被拒 → 新密码重新登录 → 刷新 → Logout | `complete` | `complete` |
| D1-07 | 唯一 API 契约 | NEW | OpenAPI 生成 TypeScript，统一 API Client，禁止手写第二套 DTO | OpenAPI diff、生成物干净重建、契约测试 | `complete` | `complete` |
| D1-08 | Python/Web/浏览器质量门 | NEW | Ruff、mypy、pytest、Prettier、ESLint、tsc、Vitest、Playwright、build | 本地与 GitHub Actions 全绿，失败探针能阻断 | `complete` | `complete` |
| D1-09 | 供应链与密钥安全 | NEW | Gitleaks 新仓当前树/完整历史；参考仓历史脱敏扫描；旧凭据逐项吊销/轮换记录；依赖审计；固定 Action SHA | 无有效密钥；处置记录、Python/Node audit 和 CI 六项任务通过 | `thin_slice` | `complete` |
| D1-10 | 产品、架构与 ADR 基线 | NEW | 中文范围、能力矩阵、架构和六项 ADR 与主计划一致 | 链接/结构/内容复核和 `git diff --check` | `complete` | `complete` |
| D1-11 | Alembic 与可重复数据库迁移 | R1 + NEW | 从第一张业务表起只用 Alembic；命名约定、单一 head、upgrade/downgrade 策略；禁止 `create_all`/手工改表 | 全新 PostgreSQL `upgrade head`、schema smoke、受支持降级/前滚演练、CI fresh migration | `complete` | `complete` |
| D1-12 | 可靠异步执行底座 | NEW | API/Beat 事务写 Job+Outbox，独立 Dispatcher，AOF Redis，late ACK/worker-lost 配置，Job lease/heartbeat/fencing，published 未 started 与 hard-timeout 对账重投 | 发布/标记各崩溃点、Redis 接受后丢消息、Broker 断线、重复并发 Worker、soft/hard timeout、过期 lease 与唯一结果测试 | `complete` | `complete` |

### 3.1 当前实现证据与验证结论

| 目标 | 可复核的实际证据 | DoD `N/A` 说明 | 复核结论 |
|---|---|---|---|
| D1-01 | [运行时约束提交 28f885d](https://github.com/hrw991009/industry-intelligence-platform/commit/28f885d766043efc3303b75ea7611d5e63481acb)、[Monorepo 提交 ebf8872](https://github.com/hrw991009/industry-intelligence-platform/commit/ebf88723c8dc8747a868ec7c1152b801a96e91d7)、锁定安装及 Python/Node build 已在 [CI 30797166192](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/30797166192) 的干净 Ubuntu 环境通过 | 此目标不创建数据库表、HTTP/SSE 契约或运行时业务数据，因此 migration、业务权限/删除、业务遥测不适用；以锁文件、构建、干净环境复现和 Git 历史替代 | `complete`；复核人：项目所有者与执行代理，2026-08-03 |
| D1-02～D1-03 | `infra/compose/compose.yaml` 定义默认依赖与四类 profile；`core/config.py`、`core/health.py`、`core/database.py`、`core/redis_client.py` 实现配置和依赖探针；本地 Compose/真实依赖门禁与 [CI 31578083339](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/31578083339) 已通过 | 健康、配置、Compose contract 和真实依赖均有自动化证据；不适用业务数据删除与 RAG 评测 | `complete`；2026-08-12 |
| D1-04～D1-05 | `modules/identity/` 与 `modules/workspaces/` 是唯一正式链路；注册、登录、刷新、改密、退出、角色矩阵、跨租户拒绝、最后 owner 的单元/HTTP/PostgreSQL/Redis 测试及真实 E2E 已通过 | 不适用 RAG 质量评测；身份、权限、失败/恢复与真实用户路径均已验证 | `complete`；2026-08-12 |
| D1-06～D1-07 | `apps/web/src/auth/`、`apps/web/src/app/`、`apps/web/src/api/` 实现真实身份旅程；`industry_platform/openapi.py` 和 `packages/api-contract/` 提供唯一生成契约；API 漂移、类型、Web 和 Playwright 门禁已通过 | 不适用独立业务 migration；OpenAPI、真实浏览器旅程和失败路径提供等价证据 | `complete`；2026-08-12 |
| D1-08 | [提交 2c4e6e9](https://github.com/hrw991009/industry-intelligence-platform/commit/2c4e6e92237584bbac2816577e1509286f08b14b) 的 Python、Web、浏览器、迁移、PostgreSQL/Redis、契约、构建、依赖与密钥门禁已在本地及 [CI 31578083339](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/31578083339) 的干净环境通过 | 此目标本身不创建业务数据；以全链质量门和失败探针替代业务删除/恢复证据 | `complete`；2026-08-12 |
| D1-09 | [CI 基线提交 54f7c48](https://github.com/hrw991009/industry-intelligence-platform/commit/54f7c48e8b01b194bb1ec7a0fa2f90682ef169ba) 与 [CI 30797166192](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/30797166192) 已证明新仓历史扫描及 Python/Node audit 通过；[参考仓凭据暴露审计](security/credential-exposure-audit.md) 记录 R1/R2 脱敏扫描及 6 组待处置候选 | 业务 migration、用户旅程和 RAG 评测不适用；但 Provider 侧吊销/轮换和复核证据尚缺，不能标为完成 | `thin_slice`；6 组候选全部处置并复扫后复核 |
| D1-10 | 当前文档包：[README](../README.md)、[产品范围](product-scope.md)、[能力矩阵](feature-matrix.md)、[架构与六项 ADR 索引](architecture.md#19-架构决策记录)、[Day 1 学习日志](learning-log/day-1.md) 与[参考仓凭据审计](security/credential-exposure-audit.md) 已同步到实际代码路径和运行方法；格式与 diff 检查已通过 | 文档治理目标不改变业务数据；以结构、链接、事实一致性和版本控制检查替代运行时门禁 | `complete`；2026-08-12 |
| D1-11～D1-12 | `migrations/versions/` 包含身份/Workspace 与可靠 Job/Outbox/Schedule 迁移；`modules/jobs/` 与 `workers/` 实现事务写入、独立发布、Worker fencing、对账及 DB-only Beat；fresh migration、并发与故障恢复测试已通过 | 不适用面向终端用户的页面旅程；以数据库迁移、Dispatcher/Worker/Scheduler 故障与恢复测试提供等价证据 | `complete`；2026-08-12 |

## 4. Day 2：Agent Runtime/Harness 基座、聊天、会话与可恢复 SSE

| ID | 目标能力与用户结果 | 来源 | 冻结的七天范围 | 验收证据 | 当前状态 | Day 7 |
|---|---|---|---|---|---|---|
| D2-01 | 可替换 Model Provider | R1 + R2 + NEW | `stream/complete`；确定性 Fake 仅测试；一个服务端 OpenAI-compatible Adapter。Embedding 使用 Day 5 的独立 Port | Provider contract、超时、429、半截响应、Token/费用 | `complete` | `complete` |
| D2-02 | 完整会话管理 | R1 + R2 | 新建、列表、详情、消息分页、重命名、删除、自动标题 | 正常/空/分页/刷新/删除 E2E，删除真实调用后端 | `complete` | `complete` |
| D2-03 | 单轮搜索模式 | R2 | 每个 Turn 明确保存 `none/web/local/both`、行业和一个或多个知识库；Day 2 启用 none，Day 3 启用 web，Day 5 启用 local/both，未就绪时返回稳定 readiness 错误 | 四种模式快照/恢复与分阶段 readiness 测试；禁止 Mock 搜索成功 | `complete` | `complete` |
| D2-04 | 聊天附件生命周期 | R1 + R2 + NEW | 上传、处理状态、关联消息、列表、删除；文本/图片真实解析，其他格式不伪装支持 | 文件类型真值表、越权/删除/失败测试 | `complete` | `complete` |
| D2-05 | 可恢复流式回答 | R1 + R2 + NEW | 202 + Job；固定 `id: sequence`/`event: type`/`data: envelope`；不推进游标的 comment 心跳；停止/重试、Last-Event-ID、snapshot、唯一终态、有界缓冲与慢客户端背压 | wire contract、非法/跨流/超前游标、断开/重连/重复/缺口/过期、慢客户端、Redis 丢失、取消测试 | `complete` | `complete` |
| D2-06 | 失败不丢用户工作 | NEW | 保存用户消息、partial、错误码和可重试状态；已有 Evidence locator 后再保存 Citation | Provider 失败和 Worker 中断后刷新仍可解释；Day 2 L0 没有 Evidence 来源，Citation 按主计划在 Day 6 接入 | `complete` | `complete` |
| D2-07 | 安全 Markdown 与可见步骤 | R2 + NEW | 消毒 Markdown；只展示 Tool/Research 的审计摘要，不展示原始 CoT | XSS、恶意链接、Prompt 注入和日志检查 | `complete` | `complete` |
| D2-08 | Agent Runtime v0 与唯一执行入口 | NEW | 版本化 AgentRun/Step/Event/State/Artifact/Budget、Provider-neutral Ports、唯一终态/stop reason、ContextCompiler v0/manifest，以及 Checkpoint envelope/schema/CAS 基础；Runtime Context 不原样入模，Celery 只承载正式 Runtime | 领域不变量、Context manifest、Checkpoint 版本/CAS、生产/Harness 同入口、原子创建/可靠投递、SSE 重连不重复当前 Model Step | `complete` | `complete` |
| D2-09 | Harness Scenario、Fake 与 Trace 基座 | NEW | 版本化 Scenario/EvalCase v1、runtime/harness/model/prompt/context/available-tools version、Fake Model、record/replay、Provider fault injection、预算/中断/重复请求可靠性 Scenario、最小 CLI、依赖文档 | 固定 Provider Scenario 两次 Event 骨架一致；格式错误/timeout/取消可重现；可靠性 Scenario 绑定正式 Runtime/Application/PostgreSQL 测试；L0 toolset 可为空；Trace 无 Secret、敏感原文和原始 CoT | `complete` | `complete` |

### 4.1 Day 2 完成证据与后续边界

当前仓库已经写入正式实现，而不再是 `planned`：

- `modules/agent_runtime/` 包含版本化 Run/Step/Event/State/Budget、Checkpoint 基础契约、Context Compiler、Direct Answer Runtime、PostgreSQL 持久化、有界 SSE/snapshot/取消、不可恢复执行终态收敛和脱敏 Trace 查询；`modules/agent_harness/` 通过同一 Runtime 运行 Scenario/Fake/Replay，不另写生产 loop。
- `modules/conversations/`、`modules/files/` 和 `workers/` 已连接 Conversation/Turn/Message、Run、Job/Outbox、私有 MinIO 附件和 Worker 执行；Turn 的模式、行业和知识库选择是持久快照。Day 2 只接受 `none`，其余模式返回明确的 readiness 错误。
- `test_agent_fake_success_postgres.py` 使用真实 Conversation、Outbox、JobExecutionRuntime、唯一 DirectAnswerRuntime、PostgreSQL Message/Event 与 committed replay 证明正式成功链；确定性 Fake 只替换外部 Provider Port，Provider 只调用一次。`test_agent_unconfigured_provider_postgres.py` 则证明正式失败链不会暗中回退 Fake。
- `evals/scenarios/day2-v2.json` 有 6 个版本化 Provider 用例：正常、格式错误、超时、429、半截响应和取消；`evals/scenarios/day2-reliability-v1.json` 另有费用预算耗尽、Worker 不可恢复中断收敛和重复 Turn 请求 3 个版本化场景，它们绑定正式 Runtime/Application/PostgreSQL 测试，不另建执行 loop。`evals/fixtures/day2-model-v1.json` 是有界回放数据，`evals/snapshots/day2-traces-v1.json` 是不含问题、答案和 Secret 的 Event 骨架快照，`evals/reports/day2-v1.json` 保存可比较结论、可靠性覆盖与测量限制。
- Web 聊天工作台消费生成的 OpenAPI 类型与正式 HTTP/SSE：会话、分页消息、模式、附件、停止、重连、错误/partial 状态、安全 Markdown 和脱敏 Run/Context Trace；万能 Page 已拆成独立会话侧栏、消息、输入、Trace 和状态模块。相关组件测试位于 `apps/web/src/chat/`，浏览器旅程位于 `tests/e2e/`。

D2-01～D2-09 的正式实现、真实用户旅程、全量本地门禁、干净 GitHub CI 与学习者复盘均已通过，当前统一标为 `complete`。D2-06 中的 Citation 在 Day 2 L0 明确为不适用：`master-plan.md` 把 Day 2 定义为一次无工具的直接回答，把 Evidence/Claim 放在 Day 4，把基于真实 Evidence locator 的 Citation gate 放在 Day 6。Day 2 没有可引用的 Evidence 来源，生成空引用或伪引用反而违反计划；本次只校正矩阵的执行日归属，不删除 Day 6 的 Citation 能力。复核：执行代理，2026-08-15；Day 7 完成状态前仍需项目所有者复核。

2026-08-15 已针对当前工作树完成最终本地收口：245 份 Python 文件通过 Ruff format/check，mypy 检查 240 个文件无问题；在 `POSTGRES_TESTS_REQUIRED=1`、`REDIS_TESTS_REQUIRED=1`、`MINIO_TESTS_REQUIRED=1` 下 708 个 pytest 全部通过且无 skip，fresh migration、真实 PostgreSQL/Redis/MinIO、wheel/sdist 构建和 Python audit 同时通过。Web 的 format/lint/typecheck、10 个 Vitest 文件共 42 个测试、生产构建、OpenAPI 确定性、Node audit 和 3 条 Playwright 浏览器旅程通过；其中成功旅程从页面 POST 经 PostgreSQL Outbox/Job、正式 JobExecutionRuntime 和唯一 DirectAnswerRuntime，在只替换 ModelProvider 测试边界的前提下验证两段 committed SSE、final Message 与刷新恢复。受控路径与 39 个 Git 提交的 Gitleaks 扫描未发现 Secret。

Agent 追加 DoD 的适用性已单独复核：Day 2 的成功、Provider 失败、取消、预算耗尽、不可恢复中断收敛和重复请求都有版本化 Scenario。Tool 失败因 L0 明确没有 Tool Action/Observation，阶段性 `N/A`，复核人为执行代理、日期 2026-08-15；该义务在 Day 3 L1/L2 恢复为适用。Day 2 对不可恢复中断必须收敛为唯一 failed 终态，只有“同一次模型调用的 durable graph resume”因主计划明确归属 D5-09 而阶段性 `N/A`，复核人为执行代理、日期 2026-08-15，不能据此豁免 Day 5。

提交 [`bf4feaff`](https://github.com/hrw991009/industry-intelligence-platform/commit/bf4feaff2e0fa5487a6f01ed0fd4cd63f5b4f659) 的 [CI 31922391846](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/31922391846) 已在干净环境通过全部适用 Job；学习者于 2026-08-16 完成 Runtime、Harness、Worker、Checkpoint 与 Trace 的职责复盘，并在复核中校正 Dispatcher/Worker 数据流和 Day 2 Checkpoint 不承诺真实 resume 的边界。逐项 Definition of Done/N/A 复核见 `docs/agent-runtime.md`。真实 OpenAI-compatible Provider smoke 可以补充信心，但没有配置 Provider 时，Fake/冻结回归只能证明契约，正式 `provider_not_configured` 链路只能证明不会回退 Fake，绝不能把两者写成真实模型质量成功。

## 5. Day 3：Agent Harness v1、有界 Tool Use、行业与 Text2SQL

| ID | 目标能力与用户结果 | 来源 | 冻结的七天范围 | 验收证据 | 当前状态 | Day 7 |
|---|---|---|---|---|---|---|
| D3-01 | 首页与当前行业上下文 | R2 + NEW | 搜索/切换四个预设行业；影响推荐、资讯、招投标、聊天与 Research；不改变权限 | 刷新持久化、作用域和越权测试 | `complete` | `complete` |
| D3-02 | Tool Registry、L1/L2 有界循环与审计 | R2 + NEW | typed Action→Observation、Schema/scope、max_steps/deadline/token/cost/cancel/no_progress、稳定错误、静态 allow/deny 与 approval_required、ApprovalRequest/Decision 和副作用幂等契约、Tool Run 页面 | L0/L1/L2 轨迹对照；模型越权、超时、预算、停止原因、Schema、审批契约、幂等键和审计测试 | `complete` | `complete` |
| D3-03 | Web Search | R2 + NEW | 一个合规真实 Adapter、来源快照/摘要、Citation；URL/网络边界复用版本化 Web Tool 安全合同和受控 egress | Tool contract、真实来源、SSRF/跳转/响应预算专项负向集和来源追踪 | `complete` | `complete` |
| D3-04 | 新闻资讯 | R2 + NEW | 真实来源样例、分类、统计、分页、原链接、行业过滤、手动采集结果 | Provider contract、真实集成、去重和来源追踪 | `complete` | `complete` |
| D3-05 | 政策 | R2 + NEW | 正式模型、搜索/筛选、来源与时间、页面 readiness；至少一条真实来源闭环 | Contract、真实样例、权限和引用测试 | `complete` | `complete` |
| D3-06 | 招投标 | R2 + NEW | 招/中标、地区、分页、原链接、手动采集；至少一条真实来源闭环 | Contract、真实样例、去重和失败测试 | `complete` | `complete` |
| D3-07 | 股票 | R2 + NEW | 真实行情 Provider、工具事件、时间/来源和聊天行情卡片 | Contract、真实样例、过期/限流/错误 UI | `complete` | `complete` |
| D3-08 | 定时采集 | R2 + NEW | 持久 Schedule/Occurrence、IANA timezone、next_due_at、停机补跑/misfire 上限；Beat→Application Service→Job/Outbox→Dispatcher；游标、external ID/hash 去重、退避、last success、dead-letter、手动立即运行 | 重复 tick/多 Beat/停机 24h/超补跑上限/时区边界/Redis 故障测试；不重复入库且遗漏、合并和失败均可见 | `complete` | `complete` |
| D3-09 | 数据库浏览 | R2 + NEW | 表大小/行数列表、Schema、主键、索引、分页数据、连接测试 | allowlist、越权、分页和错误 UI | `complete` | `complete` |
| D3-10 | 安全 Text2SQL | R2 + NEW | 只读样例库、完整 AST、schema/table/column allowlist、预算和审计 | DML/DDL/COPY/CALL/多语句/危险 CTE 拒绝 100% | `complete` | `complete` |
| D3-11 | 受校验图表 | R2 + NEW | generated/validated SQL、解释、结果表、line/bar/pie/scatter/table ECharts | 函数/脚本/外链/超量数据全部拒绝 | `complete` | `complete` |

D3-01～D3-11 的冻结范围已经全部进入正式实现并通过本地验收：生产 L0、生产 Web L2 与 Harness L1/L2 由同一 `UnifiedAgentRuntime` dispatch；Conversation→Job/Outbox→正式 Loader→`industry.web_search:v1` 的用户链路和安全 Trace 已由真实 PostgreSQL 与 Playwright 验证。L2 在同一精确 allowlist 内选择并执行两个不同 typed Tool；`database.text2sql:v1` 使用独立只读账号、完整 AST/allowlist/预算并生成受校验表格/line/bar/pie/scatter Artifact。四个行业、四类来源、手动/定时采集、陈旧 QueryRun 对账、Tool Inspector、行业/数据库/图表页面、24 条累计 Scenario 和 `evals/reports/day3-v1.json` 均有可执行证据。统一本地门禁为 Python `898 passed`、Web `54 passed`、Playwright `4 passed`，真实 PostgreSQL/Redis/MinIO 强制开启且无 skip；312 个 Python 文件 format、Ruff、303 个源文件 mypy、OpenAPI `api:check`、build、Python/Node 审计、migration 往返和受控路径/44-commit Secret 扫描均通过。普通 Conversation 逻辑删除保留 Tool audit；显式物理 Run purge与隔离备份恢复演练仍是 Day 7 发布门禁。[PR #5](https://github.com/hrw991009/industry-intelligence-platform/pull/5) 已合并，head 提交已包含在 `main`；合并提交 [`6968c63f`](https://github.com/hrw991009/industry-intelligence-platform/commit/6968c63f3330f3079e3e1cc2db0b29488d7502a2) 的 [CI 32112639811](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/32112639811) 在干净环境通过全部 7 个适用 Job。2026-08-20 复核后，D3-01～D3-11 均为 `complete`。

## 6. Day 4：Agent Memory、Evidence 与 Research L3

| ID | 目标能力与用户结果 | 来源 | 冻结的七天范围 | 验收证据 | 当前状态 | Day 7 |
|---|---|---|---|---|---|---|
| D4-01 | 从会话创建可控记忆 | R2 + NEW | 候选摘要、确认/编辑、保存、来源、置信度和用户开关 | 组件、API、权限和敏感内容策略测试 | `implemented_pending_verification` | `complete` |
| D4-02 | 记忆检索与删除 | R2 + NEW | 回答显示使用的记忆；搜索、停用、过期、删除后立即不再召回 | 删除后下一次回答不使用、跨租户为 0 | `implemented_pending_verification` | `complete` |
| D4-03 | Short-term Memory 与 Context manifest | NEW | Thread 消息引用、摘要、compaction revision、freshness、实际注入清单；不自动提升为 Long-term Memory | 压缩、过期、上下文预算、与 State/Checkpoint/Long-term Memory 分层测试 | `implemented_pending_verification` | `complete` |
| D4-04 | 唯一 typed Research L3 graph | R2 + NEW | clarification、ResearchBrief、plan、Observation→Evidence、Claim support/refute/uncertain 和可解释草稿 | 确定 Fake 下状态/Event 序列、scope、coverage/conflict 可复现 | `planned` | `complete` |
| D4-05 | Memory/Research Learning Workbench | R2 + NEW | Memory 候选/确认/召回/冲突/修改/删除、Context manifest、Plan/Action/Observation/Evidence/Claim 图和不确定项 | 真实 Event/manifest 驱动；刷新、修改、删除后的 UI 与下一 Run 一致 | `thin_slice` | `complete` |
| D4-06 | Claim 与证据图 | R2 + NEW | 关键 Claim、Evidence/Entity 基础图、locator、support/refute/uncertain、coverage/conflict | 图节点/边可反查，缺证据必须显示 uncertain | `implemented_pending_verification` | `complete` |
| D4-07 | Memory/Research 预算与策略边界 | NEW | Context、Token、费用、时间、Tool allowlist；不存原始 CoT、不执行模型代码 | 预算耗尽、跨租户、错误 Memory/Evidence 和审计测试 | `thin_slice` | `complete` |

Day 4 的实现与验收按 [五步执行计划](learning-log/day-4.md) 推进。五步开始前 D4-01～D4-07 均保持 `planned`；单步只有代码或页面而未通过该步的真实链路、权限、失败、契约和评测条件时，只能记录过程状态，不能提前标记 `complete`。

2026-08-20 已完成步骤 1、2 的本地纵向验收：正式 Conversation/Message→候选→用户确认→跨 Conversation 的下一次正式 Run→PostgreSQL 重新授权召回→`ContextCompilerV1` ModelInput/manifest→Trace/Workbench→修改、反馈、停用、过期、删除与下一次召回链路通过；真实浏览器确认被纳入 Memory 实际送入模型并可反查 revision，真实 PostgreSQL 验证更新生效、稳定排除原因、重复删除、在线 deletion residual=0 和跨 Workspace 召回为 0。版本化 `day4-memory-v1`/`memory-scorer-v1` 固定质量、污染、删除、Token 与 latency 指标口径。PostgreSQL/Redis/MinIO 全部强制开启时 Python 916 条、Vitest 60 条、Playwright 5 条全部通过，Ruff、mypy、构建、OpenAPI 确定性、依赖审计与受控路径 Secret 扫描通过。由于尚未 commit/push 并取得干净 GitHub CI，D4-01～D4-03 保持 `implemented_pending_verification`；D4-07 只完成 Memory 预算和策略边界，Research 部分尚未开始，因此为 `thin_slice`。

2026-08-21 已完成步骤 3 的本地纵向实现：Day 3 Web/行业与 Text2SQL 正式 Observation 经同一 Normalizer 校验 Tool 信封、当前授权、版本/hash、许可、typed locator 和底层依赖后成为 Evidence 或稳定 rejected decision；`ResearchClaim`、supports/refutes/context、coverage/conflict 和 Claim→Evidence 派生图落入 PostgreSQL，Evidence 失效会清空 excerpt、使关系/图失效并重算 Claim。Trace 可发起提升，Evidence Inspector 可刷新恢复并反查 Run/Step/ToolCall/Observation、来源版本和 normalizer；`day4-evidence-v1`/`evidence-scorer-v1` 固定 attribution、支持度、coverage、conflict、可解析率、权限泄漏和 latency 口径。由于尚未 commit/push 并取得干净 CI，D4-06 为 `implemented_pending_verification`；D4-05 只完成 Memory 与 Evidence Inspector 切片，Research Plan/时间线仍未完成，故为 `thin_slice`；D4-04 与步骤 4～5 仍为 `planned`。

## 7. Day 5：Agent Knowledge 与 Durable Research L4

| ID | 目标能力与用户结果 | 来源 | 冻结的七天范围 | 验收证据 | 当前状态 | Day 7 |
|---|---|---|---|---|---|---|
| D5-01 | 多知识库管理 | R2 + NEW | 创建、列表、详情、编辑、删除、文档计数和 Workspace 隔离 | CRUD、计数、越权和真实删除测试 | `planned` | `complete` |
| D5-02 | 私有预签名上传 | R1 + R2 + NEW | MinIO 私有；有界文件校验、SHA-256、短签名 URL 与 Workspace 授权复用统一上传合同 | 代表性伪类型、损坏/加密/超限、重复和签名 URL 越权测试 | `planned` | `complete` |
| D5-03 | 文档格式真实能力 | R1 + R2 | PDF、TXT、Markdown；分别使用数字文本 PDF、需要 OCR 的扫描 PDF、另一份同时含图片和复杂表格且至少 20 页的 PDF；不把前端 accept 当成功 | 每种格式真实夹具；页码/bbox、OCR 文本、图片资产、复杂表格 HTML/截图、检索和 Citation 断言 | `planned` | `complete` |
| D5-04 | 版本化解析资产 | R1 + NEW | Document/Version/Chunk/Asset；页码、bbox、标题、parser/chunker 版本、图表关系 | 20 页 PDF 可追溯到 Chunk、图片和表格 | `planned` | `complete` |
| D5-05 | 可观察异步入库 | R1 + R2 + NEW | 阶段、进度、失败原因、重试、取消、reindex；vector/lexical 两类索引写入后才 ready，页面实时展示 | 每阶段故障、Worker 重启、重复投递、超时、取消及两类索引 readiness | `planned` | `complete` |
| D5-06 | 跨存储删除与对账 | R1 + NEW | deleting → PG/Milvus/ES/MinIO 清理 → deleted；孤儿检测和人工重放 | 每个外部删除点故障与定时对账测试 | `planned` | `complete` |
| D5-07 | 文档/Chunk/资产详情 | R1 + R2 | 文档页、状态、错误、Chunk 抽屉、页面、图片、表格截图与 HTML 预览 | 组件、权限和浏览器测试 | `planned` | `complete` |
| D5-08 | Agent Knowledge Tool、Embedding Port 与 Dense baseline | NEW | 版本化 EmbeddingProvider（provider/model/dimension/normalization/batch/timeout）、`knowledge_search`、KnowledgeContextSource、ready/active version、Evidence locator，以及稳定失败语义 | Embedding contract/Fake；同一 Runtime/graph 接入私有知识；错误语义、版本、删除和 Evidence 变化对照 | `planned` | `complete` |
| D5-09 | Durable Research L4、Checkpoint 与 HITL | R2 + NEW | 复用 Day 2 Checkpoint 与 Day 3 Approval/幂等契约，将 LangGraph state 映射统一 Run/Event/Checkpoint；实现 interrupt/resume、持久审批、取消、幂等副作用和完整时间线 | 一次 Worker hard stop、重复 resume、allow/deny/timeout 与零重复副作用；跨刷新/Worker 重启组合在 Day 7 发布回归 | `planned` | `complete` |

## 8. Day 6：Hybrid RAG、多模态 Context 与 Research L5

| ID | 目标能力与用户结果 | 来源 | 冻结的七天范围 | 验收证据 | 当前状态 | Day 7 |
|---|---|---|---|---|---|---|
| D6-01 | Dense + BM25 + RRF + Rerank | R1 + R2 + NEW | 复用 Day 5 Dense baseline，新增 ES BM25、RRF、可插拔 Reranker、去重和多样性；保持唯一 Dense 正式链路 | RRF 数学、过滤、版本、Dense 回归和确定性对照 | `planned` | `complete` |
| D6-02 | 检索调试 | R1 | 独立 hybrid API 返回两路名次/分数、RRF、Rerank、过滤原因和资产命中 | 响应契约与敏感候选不泄漏测试 | `planned` | `complete` |
| D6-03 | 严格作用域检索 | NEW | Workspace、单/多 KB、ready、active version；索引结果回 PG 二次授权 | 跨 Workspace 召回 0，旧版本不命中 | `planned` | `complete` |
| D6-04 | 统一 Evidence/Citation | R1 + R2 + NEW | PDF/Chunk/图/表/Web/SQL/行业统一 locator；点击回真实来源 | Citation 可解析率 100%，删除/失效后状态可解释 | `planned` | `complete` |
| D6-05 | 多模态回答 | R1 | 召回图片/表格去重后送 VLM，至少一题图片、一题表格真实闭环 | 资产命中、实际输入数量、答案和引用测试 | `planned` | `complete` |
| D6-06 | 证据门控与 Prompt Injection 防护 | NEW | 证据不足拒答；文档内容不能改角色、Tool allowlist 或预算 | 无答案拒答 ≥ 0.90，恶意文档负向集 | `planned` | `complete` |
| D6-07 | RAG 评测基线 | NEW | Day 6 累计 ≥40 条 Agent Scenario，其中冻结 ≥20 条 RAG 子集，覆盖可回答、无答案、表格、图片和多源冲突；Day 7 全 Agent Scenario 累计 ≥50；对比 Dense/BM25/RRF/RRF+Reranker | retrieval/evidence/final/trajectory/runtime recovery 分层；Recall@5 ≥0.80、Citation 100%、拒答 ≥0.90、跨租户 0，并记录 MRR@10/忠实度/多模态/延迟/Token/费用及相对基线 | `planned` | `complete` |
| D6-08 | Research L5 与完整报告界面 | NEW | Verifier 按 Claim 支持/覆盖/冲突核验，bounded revise 后输出 complete/partial/uncertain Report；展示 Retrieval→Context→Evidence→Claim→Citation→Report 全链 | 最大 revise、partial/uncertain、L3/L4/L5 对照和可视化反查 | `planned` | `complete` |

## 9. Day 7：集成、质量与可发布学习版本

| ID | 目标能力与用户结果 | 来源 | 冻结的七天范围 | 验收证据 | 当前状态 | Day 7 |
|---|---|---|---|---|---|---|
| D7-01 | 全部路由和真实 readiness | R2 + NEW | 首页、聊天、知识库、记忆、Research、新闻、政策、招投标、股票、数据库、设置 | 路由 E2E；不可用原因真实，不出现假数据 | `planned` | `complete` |
| D7-02 | 统一 UI 失败状态 | NEW | loading/empty/error/forbidden/retry/cancelled/partial 和可操作下一步 | 关键组件与浏览器测试 | `planned` | `complete` |
| D7-03 | 完整 CI 与供应链门禁 | NEW | 全测试、fresh migration、OpenAPI diff、Gitleaks、Semgrep、依赖/镜像/许可证扫描、NOTICE/归属/修改记录核对；核心 domain/application ≥90%、后端总体 ≥80%、前端关键 Hook/状态 ≥75% | 干净 CI 全绿、覆盖率报告达标、许可证与来源清单无未处置阻断项 | `planned` | `complete` |
| D7-04 | Agent 可观测与审计收口 | NEW | 聚合逐日 JSON 日志、OTel、Event/Trace、关联 ID、Token/费用/延迟、Context 裁剪、恢复成功率和关键告警；不另建第二套事实源 | 从 Workbench/告警定位到 request、Job、Run、Step、Tool、Evidence 和审计；固定 Scenario 的基线报告可重复 | `planned` | `complete` |
| D7-05 | Agent 边界与通用安全回归 | NEW | 重点验收 Tool scope/approval、Context/Secret 隔离、Prompt Injection 和 Workspace 零泄露；CORS、Rate Limit、Cookie、上传、私有对象、短 URL、SSRF/egress、Markdown、安全头等复用 Day 1 与专项合同 | Agent 行为边界、权限/输入/网络负向测试及密钥/敏感原文检查全绿；不重复建设安全机制 | `planned` | `complete` |
| D7-06 | 一键环境与定向恢复演练 | NEW | 反代、Web、API、Dispatcher、Worker、Beat、PG、Redis、私有 MinIO，以及 Milvus/ES/观测 profiles；复用既有 healthcheck、持久卷、资源限制、优雅关闭和 Alembic 路径 | 新环境启动；Run/Checkpoint、队列、索引和数据库各选关键故障演练，恢复后正式 Scenario 可继续且无重复副作用 | `planned` | `complete` |
| D7-07 | 完整用户路径 | R1 + R2 + NEW | 注册/登录 → 创建 KB 并观察真实解析 → 分别完成图片题和表格题及可定位引用 → Text2SQL/图表 → 保存、使用并删除记忆 → Research 中断/恢复 → 带引用报告和证据图 → Logout | 一条无人工改库/假数据的 Playwright，外加真实 Provider/来源演示证据；刷新后状态仍完整 | `planned` | `complete` |
| D7-08 | 七天能力完整性审计 | NEW | 本矩阵每一目标行均为 complete，实际证据、限制和 DoD `N/A` 复核齐全 | 双向参考项目审计，无目标遗漏和重复正式链路 | `planned` | `complete` |
| D7-09 | Release Agent Eval 与演进决策 | NEW | 将 `Scenario/EvalCase v1` 定稿为 release schema；累计 ≥50 场景，组合 Tool/Memory/Evidence/Knowledge/RAG/Runtime Scorer，聚合逐日 L0/L2/L3/L4/L5 与 Context 对照；只补跑版本变化项并决定是否进入后续 L6 实验 | trajectory/result/evidence/runtime 四层评分、fault suite、成本/延迟/质量基线和策略回退决定均可复现 | `planned` | `complete` |
| D7-10 | 统一 Agent Learning Workbench | NEW | 完整保留 Run/Context、Tool、Memory、Evidence/Claim、Knowledge locator、Checkpoint/HITL、Retrieval/Citation 与 Report 面板；复杂前端由正式契约和真实 Event/Trace/Manifest 驱动 | 跨面板关联导航、刷新/中断恢复、错误状态和完整 Playwright；无 Mock 展示链路或第二事实源 | `planned` | `complete` |

## 10. 明确不继承的参考项目行为

下列内容是旧实现的问题，不属于目标能力：

- 数据库或 Provider 失败后返回 Mock 成功；
- 前端文件选择器接受扩展名，但后端只生成占位文字；
- Access Token 或服务端 Secret 写入 LocalStorage、`VITE_*` 或源码；
- 在 API/Worker 进程中 `exec`/`eval` 模型生成代码；
- 展示或保存模型原始 chain-of-thought；
- 把隐藏、无创建入口或未接入聊天的页面标成真实闭环；
- 把内部 Service 文件、图标或占位路由当作用户能力；
- 把搜索摘要描述成已经抓取和快照了网页全文；
- 为同一职责保留 v1/v2/v3 或两套后端正式链路。

## 11. 格式与功能真值原则

文档、附件、Provider 和页面都必须用真实夹具或真实合规来源验证。UI `accept`、类型声明、未调用的 Service、只有接口没有用户旅程、只有页面没有后端状态，都不能证明能力完成。

七天范围以本矩阵的“冻结的七天范围”为准。任何新增、删除或降级必须先由用户确认，再同步更新主计划、产品范围、架构/ADR、测试和本矩阵的变更证据。
