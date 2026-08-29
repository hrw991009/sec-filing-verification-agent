# Day 1～Day 10 目标能力矩阵：SEC 披露与财务事实核验 Agent

> 计划编号：`IIP-MASTER-001`
>
> 文档状态：已接受
>
> 更新日期：2026-08-28
>
> 权威来源：`docs/master-plan.md` 2.1.7

## 1. 使用规则

本矩阵回答三个问题：历史参考项目映射出了哪些通用能力、SEC 财务 Agent 后续完成到什么边界、当前是否真的已经完成。Day 1～Day 4 与 Day 5 Step 1～3 的历史事实被冻结；从 Day 5 Step 4 起，新增目标只围绕 SEC 披露与财务事实核验。矩阵中的行就是当前计划冻结的完整目标清单，不允许在执行中临时挑选或静默删减。

矩阵每一目标行在 Day 10 的目标状态都是 `complete`。冻结范围可以限制 form、Provider、公司或数据规模，但不能降低实现质量；范围内必须通过适用的真实链路、正式数据模型、权限、失败恢复、自动化测试、错误码和文档门禁。

当前状态只使用：

- `complete`：冻结范围内已经逐条评审全局 Definition of Done，所有适用项通过，所有 `N/A` 都有具体理由与复核人，并附实际证据；
- `implemented_pending_verification`：正式代码、迁移或文档已经实现，但本轮适用的统一门禁、真实依赖验证、干净 CI、目标分支合并或所有者验收尚未全部实际通过；它不是完成状态；
- `thin_slice`：已有部分真实链路，但冻结范围仍有缺口；
- `contract_only`：只有正式契约或 readiness，没有真实用户闭环；
- `blocked`：存在已记录且当前无法解除的外部阻塞；
- `planned`：尚未开始实现。

`implemented_pending_verification`、`thin_slice`、`contract_only`、`blocked` 和 `planned` 都不能作为 Day 10 最终状态。分支 CI 通过不等于合入 `main`、合并提交 CI 或所有者验收；状态变化必须附带测试、评测、演练或真实用户路径证据，不能只凭代码存在或页面截图改为 `complete`。面向用户的业务目标不得把真实旅程、服务端权限、失败/恢复或安全检查标为 `N/A`；工程、文档和治理目标可用等价的开发者/运维旅程与自动校验替代。

D1-09 的参考仓历史凭据处置需要 Provider 侧外部操作。它不否定 D1-01～D1-08、D1-10～D1-12 已完成的新仓工程门禁，也不阻断后续学习；但在 6 组候选全部吊销/轮换并复扫前，D1-09 仍阻断 Day 10 发布标签。

## 2. 参考项目标识

| 标识 | 只读参考项目 | 主要可借鉴能力 |
|---|---|---|
| R1 | `D:\my_work_project` | PDF 版面/OCR、图片表格资产、Dense + BM25 + RRF、多模态 RAG、检索调试 |
| R2 | `D:\industry_information_assistant\industry_information_assistant` | 身份、首页行业上下文、聊天、多知识库、资讯/政策/招投标/股票、数据库、记忆、Research、图表和报告 |
| NEW | 新项目质量增强 | Workspace 隔离、统一 Evidence、可靠异步任务、安全 Text2SQL、CI、评测、安全、可观测和恢复 |
| SEC | SEC EDGAR 官方接口与原始 filing | CIK/filing/accession、submissions、XBRL facts、原始 HTML/iXBRL、point-in-time 与 Fair Access |
| BENCH | FinQA、TAT-QA、FinanceBench、FinSearchComp 与通用 Agent benchmark | 分层验证数值/表格、filing RAG、live research、Tool trajectory、状态可靠性与提示注入；不作为产品代码来源 |

### 2.1 参考项目双向覆盖审计

下表从参考项目能力反查矩阵 ID，防止只从新项目视角列计划而漏掉参考目标。这里的“全部目标能力”指两个参考项目映射并冻结到本矩阵的全部用户结果，不等于复制旧仓库的重复代码、占位页面、安全缺陷或未接通的内部文件。

| 参考来源 | 参考能力组 | 对应矩阵目标 |
|---|---|---|
| R1 | PostgreSQL、Milvus、Elasticsearch、MinIO、Alembic 与可重建索引 | D1-02、D1-11、D5-02～D5-06、D6-03、D6-07、D7-01、D10-06 |
| R1 | PDF 版面、OCR、图片、复杂表格与解析资产 | D5-03～D5-07、D6-03、D6-07、D7-02 |
| R1 | Dense、BM25、RRF、重排与检索调试 | D5-08、D7-01、D7-08 |
| R1 | 多模态 RAG、Evidence 与可定位引用 | D7-01～D7-03、D8-01、D10-02 |
| R2 | 注册登录、资料、Workspace 角色与身份页面 | D1-04～D1-06 |
| R2 | 首页与当前行业上下文 | D3-01（历史完成；后续不扩张） |
| R2 | 会话、搜索模式、附件、流式聊天与恢复 | D2-01～D2-07 |
| R2 | 多知识库、文档状态与详情 | D5-01～D5-07 |
| R2 | Web、新闻、政策、招投标、股票与定时采集 | D3-03～D3-08 |
| R2 | 数据库浏览、Text2SQL 与图表 | D3-09～D3-11 |
| R2 | 记忆创建、使用与管理 | D4-01、D4-02 |
| R2 | Research 状态图、Checkpoint、四类详情、图表与报告 | D4-04～D4-07、D5-09、D7-07、D8-01～D8-08、D10-02 |
| SEC | Filer/filing identity、原始 snapshot、XBRL facts 与 accession | D6-01～D6-08、D7-01～D7-06 |
| SEC | Filing diff、amendment、监控和 point-in-time | D7-05、D8-01～D8-08、D9-05～D9-08 |
| BENCH | 数值/表格/证据/搜索/工具/安全评测 | D9-01～D9-08、D10-07 |

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
| D6-01～D6-08 | `disclosures`、`files`、`knowledge`、`ingestion`、`jobs`、SEC ports；前端 Filer/Filings Workbench | PostgreSQL 持有 filer/filing/XBRL/版本状态，MinIO 持有不可变原始 filing 快照，Milvus/ES 为可重建索引；SEC 只提供外部输入 |
| D7-01～D7-08 | `retrieval`、`context`、`financial_verification`、`tools`、`evidence`、`research`；前端 Retrieval/Calculation Workbench | PostgreSQL 持有 ContextManifest/Evidence/Citation/Calculation/Claim；检索索引是派生候选，计算输入和公式必须连接正式 Evidence |
| D8-01～D8-08 | `research`、`financial_verification`、`agent_runtime`、`jobs`、`disclosures`；前端 Verifier/Monitor Workbench | PostgreSQL 持有 Verifier 结果、Checkpoint/Approval、副作用账本、Monitor/Case；Schedule/Job/Outbox 承载可恢复执行 |
| D9-01～D9-08 | `evaluation`、`agent_harness`、`evals/`、版本化 dataset cards | Eval manifests/reports 由 Git/对象存储管理并反查 PostgreSQL AgentRun；外部 benchmark 仍受各自版本/许可约束 |
| D10-01～D10-08 | 跨模块、`tests/`、`infra/`、`docs/runbooks/`、前端 SEC Agent Workbench | 各业务事实仍由原模块拥有；CI、Trace 基线、演练、发布和所有者验收证据进入 Git/GitHub 与观测后端 |

## 3. Day 1：工程地基、身份与 Workspace

| ID | 目标能力与用户结果 | 来源 | 冻结范围 | 验收证据 | 当前状态 | Day 10 |
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
| D1-10 | Day 1 当时的文档包：[README](../README.md)、[产品范围](product-scope.md)、[能力矩阵](feature-matrix.md)、[架构与六项 ADR 索引](architecture.md#19-架构决策记录)、[Day 1 学习日志](learning-log/day-1.md) 与[参考仓凭据审计](security/credential-exposure-audit.md) 已同步到实际代码路径和运行方法；v2.0.0 后续新增 ADR 0007，不改写该历史收口 | 文档治理目标不改变业务数据；以结构、链接、事实一致性和版本控制检查替代运行时门禁 | `complete`；2026-08-12 |
| D1-11～D1-12 | `migrations/versions/` 包含身份/Workspace 与可靠 Job/Outbox/Schedule 迁移；`modules/jobs/` 与 `workers/` 实现事务写入、独立发布、Worker fencing、对账及 DB-only Beat；fresh migration、并发与故障恢复测试已通过 | 不适用面向终端用户的页面旅程；以数据库迁移、Dispatcher/Worker/Scheduler 故障与恢复测试提供等价证据 | `complete`；2026-08-12 |

## 4. Day 2：Agent Runtime/Harness 基座、聊天、会话与可恢复 SSE

| ID | 目标能力与用户结果 | 来源 | 冻结范围 | 验收证据 | 当前状态 | Day 10 |
|---|---|---|---|---|---|---|
| D2-01 | 可替换 Model Provider | R1 + R2 + NEW | `stream/complete`；确定性 Fake 仅测试；一个服务端 OpenAI-compatible Adapter。Embedding 使用 Day 5 的独立 Port | Provider contract、超时、429、半截响应、Token/费用 | `complete` | `complete` |
| D2-02 | 完整会话管理 | R1 + R2 | 新建、列表、详情、消息分页、重命名、删除、自动标题 | 正常/空/分页/刷新/删除 E2E，删除真实调用后端 | `complete` | `complete` |
| D2-03 | 单轮搜索模式 | R2 | 每个 Turn 明确保存 `none/web/local/both`、行业和一个或多个知识库；Day 2 启用 none，Day 3 启用 web，Day 5 启用 local/both，未就绪时返回稳定 readiness 错误 | 四种模式快照/恢复与分阶段 readiness 测试；禁止 Mock 搜索成功 | `complete` | `complete` |
| D2-04 | 聊天附件生命周期 | R1 + R2 + NEW | 上传、处理状态、关联消息、列表、删除；文本/图片真实解析，其他格式不伪装支持 | 文件类型真值表、越权/删除/失败测试 | `complete` | `complete` |
| D2-05 | 可恢复流式回答 | R1 + R2 + NEW | 202 + Job；固定 `id: sequence`/`event: type`/`data: envelope`；不推进游标的 comment 心跳；停止/重试、Last-Event-ID、snapshot、唯一终态、有界缓冲与慢客户端背压 | wire contract、非法/跨流/超前游标、断开/重连/重复/缺口/过期、慢客户端、Redis 丢失、取消测试 | `complete` | `complete` |
| D2-06 | 失败不丢用户工作 | NEW | 保存用户消息、partial、错误码和可重试状态；已有 Evidence locator 后再保存 Citation | Provider 失败和 Worker 中断后刷新仍可解释；Day 2 L0 没有 Evidence 来源，通用 Citation 在 Day 4 已落地，SEC Citation 门禁在 Day 7～Day 10 验收 | `complete` | `complete` |
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

D2-01～D2-09 的正式实现、真实用户旅程、全量本地门禁、干净 GitHub CI 与学习者复盘均已通过，当前统一标为 `complete`。D2-06 中的 Citation 在 Day 2 L0 明确为不适用：Day 2 是一次无工具的直接回答，Evidence/Claim 在 Day 4 落地，基于 SEC Evidence locator 的发布 Citation gate 现归属 Day 7～Day 10。Day 2 没有可引用的 Evidence 来源，生成空引用或伪引用反而违反计划；本次只校正后续执行日归属，不改写 Day 2 结论。复核：执行代理，2026-08-15。

2026-08-15 已针对当前工作树完成最终本地收口：245 份 Python 文件通过 Ruff format/check，mypy 检查 240 个文件无问题；在 `POSTGRES_TESTS_REQUIRED=1`、`REDIS_TESTS_REQUIRED=1`、`MINIO_TESTS_REQUIRED=1` 下 708 个 pytest 全部通过且无 skip，fresh migration、真实 PostgreSQL/Redis/MinIO、wheel/sdist 构建和 Python audit 同时通过。Web 的 format/lint/typecheck、10 个 Vitest 文件共 42 个测试、生产构建、OpenAPI 确定性、Node audit 和 3 条 Playwright 浏览器旅程通过；其中成功旅程从页面 POST 经 PostgreSQL Outbox/Job、正式 JobExecutionRuntime 和唯一 DirectAnswerRuntime，在只替换 ModelProvider 测试边界的前提下验证两段 committed SSE、final Message 与刷新恢复。受控路径与 39 个 Git 提交的 Gitleaks 扫描未发现 Secret。

Agent 追加 DoD 的适用性已单独复核：Day 2 的成功、Provider 失败、取消、预算耗尽、不可恢复中断收敛和重复请求都有版本化 Scenario。Tool 失败因 L0 明确没有 Tool Action/Observation，阶段性 `N/A`，复核人为执行代理、日期 2026-08-15；该义务在 Day 3 L1/L2 恢复为适用。Day 2 对不可恢复中断必须收敛为唯一 failed 终态，只有“同一次模型调用的 durable graph resume”因主计划明确归属 D5-09 而阶段性 `N/A`，复核人为执行代理、日期 2026-08-15，不能据此豁免 Day 5。

提交 [`bf4feaff`](https://github.com/hrw991009/industry-intelligence-platform/commit/bf4feaff2e0fa5487a6f01ed0fd4cd63f5b4f659) 的 [CI 31922391846](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/31922391846) 已在干净环境通过全部适用 Job；学习者于 2026-08-16 完成 Runtime、Harness、Worker、Checkpoint 与 Trace 的职责复盘，并在复核中校正 Dispatcher/Worker 数据流和 Day 2 Checkpoint 不承诺真实 resume 的边界。逐项 Definition of Done/N/A 复核见 `docs/agent-runtime.md`。真实 OpenAI-compatible Provider smoke 可以补充信心，但没有配置 Provider 时，Fake/冻结回归只能证明契约，正式 `provider_not_configured` 链路只能证明不会回退 Fake，绝不能把两者写成真实模型质量成功。

## 5. Day 3：Agent Harness v1、有界 Tool Use、行业与 Text2SQL

| ID | 目标能力与用户结果 | 来源 | 冻结范围 | 验收证据 | 当前状态 | Day 10 |
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

D3-01～D3-11 的冻结范围已经全部进入正式实现并通过本地验收：生产 L0、生产 Web L2 与 Harness L1/L2 由同一 `UnifiedAgentRuntime` dispatch；Conversation→Job/Outbox→正式 Loader→`industry.web_search:v1` 的用户链路和安全 Trace 已由真实 PostgreSQL 与 Playwright 验证。L2 在同一精确 allowlist 内选择并执行两个不同 typed Tool；`database.text2sql:v1` 使用独立只读账号、完整 AST/allowlist/预算并生成受校验表格/line/bar/pie/scatter Artifact。四个行业、四类来源、手动/定时采集、陈旧 QueryRun 对账、Tool Inspector、行业/数据库/图表页面、24 条累计 Scenario 和 `evals/reports/day3-v1.json` 均有可执行证据。统一本地门禁为 Python `898 passed`、Web `54 passed`、Playwright `4 passed`，真实 PostgreSQL/Redis/MinIO 强制开启且无 skip；312 个 Python 文件 format、Ruff、303 个源文件 mypy、OpenAPI `api:check`、build、Python/Node 审计、migration 往返和受控路径/44-commit Secret 扫描均通过。普通 Conversation 逻辑删除保留 Tool audit；显式物理 Run purge 与隔离备份恢复演练移至 Day 10 发布门禁。[PR #5](https://github.com/hrw991009/industry-intelligence-platform/pull/5) 已合并，head 提交已包含在 `main`；合并提交 [`6968c63f`](https://github.com/hrw991009/industry-intelligence-platform/commit/6968c63f3330f3079e3e1cc2db0b29488d7502a2) 的 [CI 32112639811](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/32112639811) 在干净环境通过全部 7 个适用 Job。2026-08-20 复核后，D3-01～D3-11 均为 `complete`。

## 6. Day 4：Agent Memory、Evidence 与 Research L3

| ID | 目标能力与用户结果 | 来源 | 冻结范围 | 验收证据 | 当前状态 | Day 10 |
|---|---|---|---|---|---|---|
| D4-01 | 从会话创建可控记忆 | R2 + NEW | 候选摘要、确认/编辑、保存、来源、置信度和用户开关 | 组件、API、权限和敏感内容策略测试 | `complete` | `complete` |
| D4-02 | 记忆检索与删除 | R2 + NEW | 回答显示使用的记忆；搜索、停用、过期、删除后立即不再召回 | 删除后下一次回答不使用、跨租户为 0 | `complete` | `complete` |
| D4-03 | Short-term Memory 与 Context manifest | NEW | Thread 消息引用、摘要、compaction revision、freshness、实际注入清单；不自动提升为 Long-term Memory | 压缩、过期、上下文预算、与 State/Checkpoint/Long-term Memory 分层测试 | `complete` | `complete` |
| D4-04 | 唯一 typed Research L3 graph | R2 + NEW | clarification、ResearchBrief、plan、Observation→Evidence、Claim support/refute/uncertain 和可解释草稿 | 确定 Fake 下状态/Event 序列、scope、coverage/conflict 可复现 | `complete` | `complete` |
| D4-05 | Memory/Research Learning Workbench | R2 + NEW | Memory 候选/确认/召回/冲突/修改/删除、Context manifest、Plan/Action/Observation/Evidence/Claim 图和不确定项 | 真实 Event/manifest 驱动；刷新、修改、删除后的 UI 与下一 Run 一致 | `complete` | `complete` |
| D4-06 | Claim 与证据图 | R2 + NEW | 关键 Claim、Evidence/Entity 基础图、locator、support/refute/uncertain、coverage/conflict | 图节点/边可反查，缺证据必须显示 uncertain | `complete` | `complete` |
| D4-07 | Memory/Research 预算与策略边界 | NEW | Context、Token、费用、时间、Tool allowlist；不存原始 CoT、不执行模型代码 | 预算耗尽、跨租户、错误 Memory/Evidence 和审计测试 | `complete` | `complete` |

Day 4 的实现与验收按 [五步执行计划](learning-log/day-4.md) 推进。单步只有代码或页面而未通过该步的真实链路、权限、失败、契约和评测条件时，只能记录过程状态，不能提前标记 `complete`。

2026-08-20 已完成步骤 1、2 的本地纵向验收：正式 Conversation/Message→候选→用户确认→跨 Conversation 的下一次正式 Run→PostgreSQL 重新授权召回→`ContextCompilerV1` ModelInput/manifest→Trace/Workbench→修改、反馈、停用、过期、删除与下一次召回链路通过；真实浏览器确认被纳入 Memory 实际送入模型并可反查 revision，真实 PostgreSQL 验证更新生效、稳定排除原因、重复删除、在线 deletion residual=0 和跨 Workspace 召回为 0。版本化 `day4-memory-v1`/`memory-scorer-v1` 固定质量、污染、删除、Token 与 latency 指标口径。PostgreSQL/Redis/MinIO 全部强制开启时 Python 916 条、Vitest 60 条、Playwright 5 条全部通过，Ruff、mypy、构建、OpenAPI 确定性、依赖审计与受控路径 Secret 扫描通过。由于尚未 commit/push 并取得干净 GitHub CI，D4-01～D4-03 保持 `implemented_pending_verification`；D4-07 只完成 Memory 预算和策略边界，Research 部分尚未开始，因此为 `thin_slice`。

2026-08-21 已完成步骤 3 的本地纵向实现：Day 3 Web/行业与 Text2SQL 正式 Observation 经同一 Normalizer 校验 Tool 信封、当前授权、版本/hash、许可、typed locator 和底层依赖后成为 Evidence 或稳定 rejected decision；`ResearchClaim`、supports/refutes/context、coverage/conflict 和 Claim→Evidence 派生图落入 PostgreSQL，Evidence 失效会清空 excerpt、使关系/图失效并重算 Claim。Trace 可发起提升，Evidence Inspector 可刷新恢复并反查 Run/Step/ToolCall/Observation、来源版本和 normalizer；`day4-evidence-v1`/`evidence-scorer-v1` 固定 attribution、支持度、coverage、conflict、可解析率、权限泄漏和 latency 口径。由于尚未 commit/push 并取得干净 CI，D4-06 为 `implemented_pending_verification`；D4-05 只完成 Memory 与 Evidence Inspector 切片，Research Plan/时间线仍未完成，故为 `thin_slice`。该段记录步骤 3 验收时点；步骤 4 的后续状态见下一段。

2026-08-21 已完成步骤 4 的本地纵向实现：显式 ResearchBrief 与唯一 `research-l3-graph-v1` 通过 202 + Idempotency-Key、同一 ResearchRun/AgentRun/Job/Outbox、统一 Runtime 内的既有 bounded Tool loop、Evidence/Claim Application Service 和 PostgreSQL L3 draft 串成正式链路。确定 Fake 证明 accepted Evidence→supported Claim→explainable draft；真实行业 Tool 缺少不可变快照时证明 rejected decision→uncertain Claim→uncertain draft。取消、deadline、invalid output、max steps、Token/费用和跨 Workspace 路径均有明确终态；迁移、生成契约、真实依赖、Python/Web/浏览器、构建、audit 和 Secret 门禁通过。D4-04、D4-07 保持 `implemented_pending_verification`；该时点 D4-05 仍为 `thin_slice`、步骤 5 仍为 `planned`，后续本地收口见下一段。

2026-08-22 已完成步骤 5 的本地收口：Research Workbench 从正式 OpenAPI/Event/Trace/资源 API 展示 Brief、Plan、节点/Step、usage、Evidence/Claim、coverage/conflict、uncertain draft、失败/取消/预算和刷新恢复，并与 Evidence Inspector 双向导航；真实 Chromium Research L3 链贯穿 PostgreSQL Job/Outbox、统一 Runtime、Web Tool、Normalizer、Claim 和 draft。保留 Day 2/3 的 24 条基线，Day 4 新增 26 条独立 Scenario，累计 50 条；Memory、Memory off/on、Evidence 和 Research 使用独立规则 Scorer，同题 L0/L2/L3 同时报告质量代理、步骤、Token、费用和延迟。全量门禁为 pytest 946、Vitest 75、Playwright 6，真实 PostgreSQL/Redis/MinIO 无 skip，migration 全历史往返、OpenAPI hash、build、audit、受控路径及 53 个可达提交的 Gitleaks 均通过；后端总体覆盖率 82.12%，前端关键状态分支 100%。Day 4 核心 Domain/Application/Research workflow 合集为 85%，低于 90% 目标，已在学习日志记录原因、风险、CI 不退化缓解与复核人；原定 Day 7 前补齐，计划 2.0.0 将最终发布门禁移至 Day 10，但阈值仍为 90%。该段保留步骤 5 本地关闭时点的事实；远端分支复核见下一段。

2026-08-22 五个步骤及收口文档形成 `4243cb0`、`7f8c7ac`、`446c9cc`、`27b75ea`、`b99ca7a`、`9c9a630` 并推送到 `feat/day-4`；最终功能提交 `b99ca7a` 的分支 CI `32547497639` 全绿。随后 [PR #7](https://github.com/hrw991009/industry-intelligence-platform/pull/7) 合入 `main`，合并提交 [`c0b854e`](https://github.com/hrw991009/industry-intelligence-platform/commit/c0b854e64ef1966b76cdcc38c41a507959c836cb) 的 [CI 32549438592](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/32549438592) 再次通过 Browser E2E、Python/Web 质量、真实 PostgreSQL/Redis/MinIO 集成、依赖审计和完整历史 Secret 扫描共 7 个适用 Job。项目所有者在收到当前日、门禁、切片和计划偏差审计后明确授权收口；执行代理据正式 Trace、50 条累计 Scenario、四套独立规则 Scorer、真实浏览器旅程和 DoD 记录完成复盘与 D4-01～D4-07 双向映射。故 Day 4 统一为 `complete`，允许进入 Day 5。核心合集 85%→90% 与 D1-09 外部凭据治理继续阻断 Day 10 发布标签，两者均不被本次关闭掩盖。

## 7. Day 5：Agent Knowledge、SEC Fixture 与 Durable Research L4

| ID | 目标能力与用户结果 | 来源 | 冻结范围 | 验收证据 | 当前状态 | Day 10 |
|---|---|---|---|---|---|---|
| D5-01 | 多知识库管理 | R2 + NEW | 创建、列表、详情、编辑、删除、文档计数和 Workspace 隔离 | CRUD、计数、越权和真实删除测试 | `complete` | `complete` |
| D5-02 | 私有预签名上传 | R1 + R2 + NEW | MinIO 私有；有界文件校验、SHA-256、短签名 URL 与 Workspace 授权复用统一上传合同 | 代表性伪类型、损坏/加密/超限、重复和签名 URL 越权测试 | `complete` | `complete` |
| D5-03 | 文档格式真实能力 | R1 + R2 | PDF、TXT、Markdown；数字 PDF、扫描 PDF、含图片/复杂表格 PDF 使用独立 fixture；不把前端 accept 当成功 | 格式/解析/页码/bbox/OCR/图片/表格与故障合同 | `complete` | `complete` |
| D5-04 | 版本化解析资产 | R1 + NEW | Document/Version/Page/Chunk/Asset；页码、bbox、标题、parser/chunker 版本和关系 | 解析资产可追溯且重复 Worker 不重复写入 | `complete` | `complete` |
| D5-05 | 可观察异步入库 | R1 + R2 + NEW | 阶段、失败、重试、取消；vector/lexical 两类索引写入后才 ready | 阶段故障、Worker 重启、重复投递、超时、取消及双索引 readiness | `complete` | `complete` |
| D5-06 | 跨存储删除与对账 | R1 + NEW | deleting → PG/Milvus/ES/MinIO 清理 → deleted；孤儿检测和重放 | 外部删除点故障、对账和零活跃残留 | `complete` | `complete` |
| D5-07 | 文档/Chunk/资产详情 | R1 + R2 | 文档、状态、错误、版本、Chunk、页面、图片、表格、删除与刷新恢复 | 组件、权限、OpenAPI 和浏览器测试 | `complete` | `complete` |
| D5-08 | Embedding/index-write、SEC Fixture Dense Tool 与 Calculator | NEW + SEC | 复用同一入库/Runtime 链完成固定 SEC accession fixture、Dense `knowledge_search`、KnowledgeContextSource、`finance.calculate@v1` 和 filing/calculation Evidence lineage | Embedding/index 合同；同一 Runtime 接入 filing fixture；公式/单位/期间/错误语义、F0～F2 对照与 ready fixture 浏览器 Evidence 反查 | `implemented_pending_verification` | `complete` |
| D5-09 | SEC Fixture Durable Research L4、Checkpoint 与 HITL | R2 + NEW + SEC | 将带 FinancialScope 的 LangGraph state 映射统一 Run/Event/Checkpoint；interrupt/resume、持久审批、取消、幂等计算/Artifact | hard stop、重复 resume/decision、allow/deny/timeout、零重复副作用与 fixture 暂停/审批/resume/刷新浏览器旅程 | `implemented_pending_verification` | `complete` |

2026-08-26 合并事实：Day 5 五步由 [PR #9](https://github.com/hrw991009/industry-intelligence-platform/pull/9) 合入 `main`，功能 head `cff25c1` 的 push CI [`32920879147`](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/32920879147) 与 PR CI [`32924323618`](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/32924323618) 均成功；合并提交 [`a38d0ae`](https://github.com/hrw991009/industry-intelligence-platform/commit/a38d0aee101b66d9c6601a01b426ffd1ec0dcb34) 的 main CI [`32924732755`](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/32924732755) 再次通过 7 个适用 Job。D5-01～D5-07 的冻结验收因此关闭为 `complete`。但 Day 5 本地记录明确没有 ready SEC fixture 的 Dense/calculation Evidence 浏览器全链，也没有同一 fixture 暂停/审批/resume/刷新旅程；项目所有者随后明确要求开始 Day 6 Step 1，只调整该步骤的开始顺序，不关闭或豁免 D5-08/D5-09。两项与 Day 5 总门禁继续保持 `implemented_pending_verification`。F0/F1 仍非独立真实模型结果；live SEC、后台审批超时扫描和 Day 8 跨刷新/Worker 重启组合门不属于 Day 5 范围。

## 8. Day 6：SEC 官方披露数据与 Point-in-Time

| ID | 目标能力与用户结果 | 来源 | 冻结范围 | 验收证据 | 当前状态 | Day 10 |
|---|---|---|---|---|---|---|
| D6-01 | Filer/CIK 身份解析 | SEC + NEW | 公司名/ticker/历史 alias → 候选 → 明确 CIK；歧义必须澄清 | identity fixture、历史 alias、多候选与错误公司负向 | `implemented_pending_verification` | `complete` |
| D6-02 | Filing/accession 时点选择 | SEC + NEW | 启用 `10-K/10-Q/10-K/A`，`10-Q/A` 仅保证 amendment 合同兼容；report/filed/accepted/public-available time、visibility/amendment policy、`as_of`、base relation，以及 submissions current + `filings.files` supplemental + bulk/incremental watermark coverage | cutoff、form、期间、可见性依据、amendment、历史 supplemental、重复 accession、coverage 缺失/损坏、post-watermark gap 与 future leakage 测试 | `thin_slice` | `complete` |
| D6-03 | 不可变原始 Filing 快照 | SEC + R1 | canonical identity/current projection、append-only source version 与 Workspace import 分层；官方 HTML/iXBRL/XML/response/必要附件、URL、source-version visibility、retrieved_at、hash、MinIO ref | correction/deletion、重复同步、引用/删除、内容变化异常、损坏/partial 与授权测试 | `implemented_pending_verification` | `complete` |
| D6-04 | XBRL Context/Fact | SEC + NEW | concept/value/unit/instant-duration/accession；聚合 response 与 raw iXBRL/instance XML 来源分离，context/dimensions/decimals/scale 按 source capability 可空 | standard/custom tag、aggregate/raw locator、单位/期间/context、provenance 与 locator 测试 | `implemented_pending_verification` | `complete` |
| D6-05 | SEC typed read Tools | SEC + NEW | `resolve_filer/list_filings/get_xbrl_facts/search_filing/read_filing_section` | Tool schema、参数、allowlist、错误语义和同一 Runtime Trace | `implemented_pending_verification` | `complete` |
| D6-06 | Fair Access 与来源治理 | SEC + NEW | 服务端 User-Agent、全局速率预算、缓存、429/5xx 退避；<100 CIK API、≥100 CIK 或全量刷新走 bulk；bulk 保存 published/coverage watermark，时间缺口须由官方增量补齐；无浏览器直连 | client contract、rate-limit、bulk threshold/watermark/hash/partial/failure、post-watermark gap、timeout、license/source review | `thin_slice` | `complete` |
| D6-07 | Filing 入库与 Workbench | R1 + SEC | Workspace import 复用 File/Knowledge/Ingestion/双索引；CIK→accession→canonical snapshot→DocumentVersion/Chunk 与 XBRL context/fact 导航 | PG/MinIO/Milvus/ES 集成、OpenAPI、standard/raw fact 组件与浏览器旅程 | `implemented_pending_verification` | `complete` |
| D6-08 | `sec-source-v1` 数据合同评测 | SEC + BENCH + NEW | ≥24 contract/closeout regression cases，`execution_kind=tool|sync`、`sync_kind=canonical_source|workspace_import`：identity、visibility/amendment、coverage watermark、snapshot、custom tag、unit/period、429、重复同步和跨 Workspace；每例固定 snapshot/import presence 预期 | manifest/scorer/eligible denominator、canonical/import lineage、失败例零已提交 snapshot/import、deterministic report、live smoke 分报、source/future leakage 指标 | `implemented_pending_verification` | `complete` |

2026-08-27 收口映射：Day 6 已由 [PR #10](https://github.com/hrw991009/industry-intelligence-platform/pull/10) 合入 `main`；功能 head `7a4766b` 的 push/PR CI `33053621106`、`33053623731` 和合并提交 `84a7945` 的 main CI `33054136204` 均通过 7 个适用 Job，项目所有者已要求核对 Day 6 并准备 Day 7 文档。提交、合并和 CI 条件已关闭，但确定性报告仍为 contract `18/18`、closeout `4/6`、总计 `22/24`，两条 bulk watermark case 保持 `capability_missing` 且没有从分母删除；当前也没有合法 SEC 联系身份对应的 live smoke。因此 D6-01/D6-03/D6-04/D6-05/D6-07/D6-08 继续为 `implemented_pending_verification`，D6-02/D6-06 因 bulk snapshot/watermark/post-gap 缺口保持 `thin_slice`。Day 6 分支结束不等于 D6-01～D6-08 全部 `complete`。详见 [Day 6 执行计划](learning-log/day-6.md)。

## 9. Day 7：Filing Hybrid Retrieval、计算与核对

| ID | 目标能力与用户结果 | 来源 | 冻结范围 | 验收证据 | 当前状态 | Day 10 |
|---|---|---|---|---|---|---|
| D7-01 | XBRL + Filing Hybrid Retrieval | R1 + SEC + NEW | 锁定 accession；XBRL facts 与 Dense/BM25/RRF/rerank 双通道 | Recall@5/MRR、wrong-accession、filter 和分层 Retrieval Trace | `implemented_pending_verification` | `complete` |
| D7-02 | SEC Evidence locator/Citation | ADR 0003 + SEC | filing section/table/text 与 XBRL fact locator；官方 URL/hash/context/unit/period | source identity/Citation 可解析 100%、失效与权限测试 | `thin_slice` | `complete` |
| D7-03 | Financial Context Compiler | NEW | FinancialScope、Memory、facts、filing Evidence、Tool Observation、Token budget 与排除原因 | cutoff/错误 accession/unit 排除、manifest 与注入测试 | `implemented_pending_verification` | `complete` |
| D7-04 | Typed financial calculator | NEW | Decimal operator、百分比/变化率、unit/scale/rounding、Evidence inputs | program/execution、零分母、单位冲突和 lineage 测试 | `implemented_pending_verification` | `complete` |
| D7-05 | Period/unit/context reconciliation | SEC + NEW | company、form、fiscal period、instant/duration、dimension、custom/standard concept、amendment | 错误 company/period/accession 为 0，冲突 typed result | `implemented_pending_verification` | `complete` |
| D7-06 | Filing/amendment diff | SEC + NEW | 同公司可比期间、base/amendment、相邻 filing 的 fact/section diff | 不可比拒绝、Evidence 双向定位和幂等测试 | `implemented_pending_verification` | `complete` |
| D7-07 | 中文 SEC L4 Agent profile | NEW | scope→resolve→select→decompose→structured+narrative→calculate→reconcile→draft | 同一 Runtime/Checkpoint、中文/英文事实链一致与停止语义 | `implemented_pending_verification` | `complete` |
| D7-08 | `sec-tool-v1` 与 A0/A1/A2 | BENCH + NEW | oracle、纯 Hybrid RAG、RAG+SEC/XBRL+calculator；简单/计算/修订/无答案 | 分层质量、简单题退化≤2pp、成本/延迟和回退决定 | `implemented_pending_verification` | `complete` |

2026-08-27 Step 1 映射：项目所有者明确开始 Day 7 Step 1。生产组合根已把 `sec.search_filing@v1` 接到 Milvus Dense + Elasticsearch BM25、RRF60、可插拔 reranker、section cap 与版本化 Trace；候选经 PostgreSQL 重载 Workspace/import/snapshot/index 后才返回。Tool Observation 改用内部 `sec://` identity，Evidence normalizer 与迁移支持 filing text、XBRL fact 的 scope/source/hash/context 重载。确定性、依赖错误、权限、locator round-trip、migration 往返和真实 PostgreSQL/MinIO/Milvus/Elasticsearch 检索通过；本地 Python 全量为 `1121 passed`、总体分支覆盖率 `80.62%`、核心合集 `86%`，Web 质量与构建、OpenAPI 连续生成也通过。冻结 Recall@5/MRR、table/cell/character locator、正式 Citation 可解析率、分支/PR/main CI 尚缺，因此 D7-01 为 `implemented_pending_verification`、D7-02 为 `thin_slice`，D7-03～D7-08 保持 `planned`。Day 6 两条 bulk case 改为 Day 10 发布硬门，原 `22/24` 不变。

2026-08-28 Step 2 映射：项目所有者在 Step 1 的 ranking/table/Citation 债务仍登记的前提下明确继续。现有 Context Compiler 扩展为兼容 `context-v1` 与 `financial-context-v1` 的同一实现；生产 LOCAL Tool L2 注入锁定的 `FinancialScope`，WEB 路径保持原合同。金融 Tool Observation 按 scope/cutoff/unit/Token budget 记录稳定排除原因，并在 manifest/Trace/OpenAPI 中保存 locator identity、版本与 hash；错误 accession、future XBRL、unit 冲突、畸形来源、超预算和 prompt injection 均有负向测试，同输入 manifest 保持确定一致。Step 2 已提交为 `d3c88d5`；分支 CI `33140371558` 有 6 个 Job 通过，PostgreSQL integration 因 `asdict()` 深拷贝冻结 `source_identity` 失败。当前工作树已改用浅层字段投影并增加回归测试，尚无新远端 CI，因此 D7-03 仍为 `implemented_pending_verification`。Step 1 分支提交 `2944591` 的 CI `33135122319` 已通过，但 Recall@5/MRR、table/cell/character locator、Citation 100%、PR/main CI 仍未关闭。Day 6 原 `22/24` 不变。

2026-08-28 Step 3 映射：项目所有者明确继续下一步。正式 XBRL Tool 输出获得确定性 Evidence ref；新 PostgreSQL operand port 在计算前重载 Workspace import、active Knowledge/DocumentVersion、filing/source/fact identity 与 cutoff。既有 Decimal calculator 增加 percentage 和 fact-to-scope scale propagation；`financial-reconciliation-v1` 对 scope、period kind、unit、dimensions、concept 和 amendment 返回 typed result，非 `consistent` 不计算。Calculation Evidence 再次重载输入 Evidence/XBRL 来源并重跑 reconciliation/calculator，正式 operand 解析失败不得降级到 fixture。本地 Python `1060 passed, 84 skipped`、全后端 Ruff、全仓 mypy `473` 个源文件、Web format/lint/typecheck、Vitest `85 passed` 与 build 通过；本机 Docker 未运行，新增 PostgreSQL operand 测试未执行，当前 Step 3 未提交且无新远端 CI，因此 D7-04/D7-05 为 `implemented_pending_verification`，D7-06～D7-08 保持 `planned`。

2026-08-28 Step 4 映射：项目所有者明确继续。当前工作树新增 fail-closed `sec-filing-diff-v1`/`sec.diff_filings@v1`，只接受已解析 base/amendment 或同主体同 form 的相邻期间，复用既有 XBRL/Hybrid service 并返回左右 fact Evidence 与 section locator；生产 exact LOCAL surface 启用中文 `sec-l4-v1`，不建立第二套 Runtime/graph。SEC 与 Research Workbench 展示 diff、Context 排除、Calculation/reconciliation 和 Citation 反查；Evidence API 补齐 SEC/XBRL/Calculation 判别联合序列化。Python `1069 passed, 84 skipped`、Ruff、mypy `475` 个源文件、Web 全门 `87 passed`、build 与 OpenAPI 确定性通过。提交 `3462b48` 的 CI `33149285431` 为 6/7 Job，通过项之外的 PostgreSQL Job 暴露 ready 版本未激活的测试夹具错误；当前已同步 `Document.active_version_id`，尚待真实依赖和新远端 CI。正式浏览器 API 全链、中英 paired run、提交/PR/main CI 未完成，故 D7-06/D7-07 为 `implemented_pending_verification`，D7-08 保持 `planned`。

2026-08-28 Step 5 映射：项目所有者明确继续。当前工作树冻结 10-case `sec-tool-v1`、同一数据版本/预算和 30 个 A0/A1/A2 runs；独立 observation 经规则 scorer 重算 identity、答案/Evidence、calculation lineage、Citation、拒答、Tool/budget 与成本延迟，并可确定生成 JSON/Markdown。deterministic report 中 A2 复杂题相对 A1 提升 `0.833333`、简单题退化 `0`，A2 拒答/Citation/lineage 为 `1.0`，三策略错误 company/period/accession 为 `0`。报告明确不是 live/model/public benchmark，`day7_closeout_ready=false`。提交 `e5fb75c` 的 CI `33152912538` 暴露 raw XBRL QName unit、过期 E2E selector 和测试版本常量误报，当前已按正式边界修复；本地 Python `1081 passed, 84 skipped`、Ruff、mypy `478` 个源文件、Web `87 passed`、格式/lint/typecheck/build、OpenAPI/报告连续生成和 Gitleaks 通过，但尚无新远端 CI。真实依赖、正式浏览器、中英 paired、分支/PR/main CI、owner review、Step 1 ranking/Citation 与 Day 6 `22/24` 仍缺，故 D7-08 为 `implemented_pending_verification`，Day 7 未关闭。

2026-08-28 合并映射：Day 7 已由 [PR #11](https://github.com/hrw991009/industry-intelligence-platform/pull/11) 合入 `main`，功能 head `6a25ab2` 的两组 PR 检查均通过；合并提交 `ae33b98` 的 [main CI `33156337673`](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/33156337673) 最终为 6/7 Job 通过、Browser E2E 失败。合并事实不关闭 ranking/table/Citation、真实依赖、正式浏览器、中英 paired、main CI 和 owner review，因此 D7-01/D7-03～D7-08 保持 `implemented_pending_verification`，D7-02 保持 `thin_slice`。项目所有者授权继续 Day 8 规划并将统一查漏补缺安排到 Day 10，这不是状态豁免。

## 10. Day 8：Verified Agent L5、Monitor 与 HITL

| ID | 目标能力与用户结果 | 来源 | 冻结范围 | 验收证据 | 当前状态 | Day 10 |
|---|---|---|---|---|---|---|
| D8-01 | SEC Evidence-aware Verifier | NEW | filer/accession/as_of、support、unit/period/context、calculation、coverage/conflict/Citation | 规则 Scorer、verified false support=0、typed issue list | `implemented_pending_verification` | `complete` |
| D8-02 | 四种业务核验终态 | NEW | `verified/partial/conflict/insufficient_evidence`，与 Runtime stop reason 分离 | UI/API/导出一致，后 3 种不伪装 verified | `implemented_pending_verification` | `complete` |
| D8-03 | 最多一次 bounded revise | NEW | targeted retrieve/recalculate→revise→finalize；不改 scope/toolset | max revise、budget/deadline/no-progress、A2/A3 对照 | `planned` | `complete` |
| D8-04 | Indirect Prompt Injection 防护 | AgentDojo 方法 + NEW | filing/网页/表格不能改 Instructions、Tool、Scope、Budget、as_of 或审批 | benign utility、attack cases、未授权写 Tool=0 | `planned` | `complete` |
| D8-05 | 披露 Monitor 与差异 Case | SEC + NEW | filer/forms/rules/watermark/schedule；新 filing/amendment 生成幂等 Case | tick/misfire/429/dead-letter/amendment/重复通知测试 | `planned` | `complete` |
| D8-06 | `monitor.subscribe@v1` 持久 HITL | NEW | 写 Tool 请求→ApprovalRequest→allow/deny/timeout→幂等订阅 | 跨刷新/Worker 重启、重复 decision、deny/timeout 零写入 | `planned` | `complete` |
| D8-07 | L4/L5 Durable recovery | R2 + NEW | Checkpoint CAS、hard stop、resume、取消竞态、副作用账本 | 恢复成功 100%，Tool/Calculation/Monitor/Case 重复数 0 | `planned` | `complete` |
| D8-08 | Verified/Monitor Workbench 与 A2/A3/A4 | NEW | Verifier issues、revise diff、Approval、Monitor、Case、两个 accession/Evidence | 正式 API/Event/Trace 驱动、fault/security report 和净收益决定 | `planned` | `complete` |

2026-08-28 规划映射：Day 8 按依赖收敛为五步：① D8-01/D8-02 Claim Verifier 与四种业务状态；② D8-03/D8-04 one-revise 与不可信输入防线；③ D8-05 Monitor/watermark/幂等 Case；④ D8-06/D8-07 及 D8-08 部分的持久 HITL、恢复与 Workbench；⑤ D8-08 的 `sec-verification-v1`、A2/A3/A4 与收口。具体合同见 [Day 8 执行计划](learning-log/day-8.md) 和 [SEC Verifier、Monitor 与恢复设计](sec-verification-monitor-design.md)。本轮没有 Day 8 代码、迁移、测试或运行证据，D8-01～D8-08 全部保持 `planned`。

2026-08-29 Step 1 映射：当前工作树新增确定性 `sec-claim-verifier-v1`、四种业务状态与 typed issue，复用正式 Evidence availability、SEC locator/hash、`FinancialScope` 和 Calculation 重算链；新增 append-only PostgreSQL report/Claim/issue、授权只读 API、Event/Trace contract 和 OpenAPI。14 条聚焦规则/API 测试、真实 PostgreSQL append-only/stale-revision 测试及完整 Alembic 往返/autogenerate drift 检查通过；完整 Python 回归为 `1089 passed, 85 skipped`，并修复 `sec-tool-v1` 报告在 Windows/Linux 间的 LF 字节漂移。当前没有 graph 事件发射、one-revise、Workbench、frozen eval、提交或远端 CI，因此仅 D8-01/D8-02 更新为 `implemented_pending_verification`，D8-03～D8-08 保持 `planned`。

## 11. Day 9：Benchmark、Temporal Eval 与中文验证

| ID | 目标能力与用户结果 | 来源 | 冻结范围 | 验收证据 | 当前状态 | Day 10 |
|---|---|---|---|---|---|---|
| D9-01 | Release Eval manifest/schema | NEW | dataset/split/license/hash、CIK/accession/as_of、runtime/model/tool/context/budget/scorer | schema/compatibility、case→run→trace→Evidence 可反查 | `planned` | `complete` |
| D9-02 | FinQA adapter | BENCH | supporting facts、program/execution answer；固定上下文数值推理 | 官方指标、固定 commit/hash、数据/代码许可卡 | `planned` | `complete` |
| D9-03 | TAT-QA adapter | BENCH | table+text、answer、scale、derivation/source | 官方 EM/F1、固定 commit/hash、许可卡 | `planned` | `complete` |
| D9-04 | FinanceBench/FinSearchComp 补充 | BENCH | 公开 150 题非商用审查；historical/live search 分报 | dataset cards、许可/动态/judge 限制、无单一硬门 | `planned` | `complete` |
| D9-05 | `sec-temporal-v1` | SEC + NEW | ≥60 固定 case：事实/表格/计算/跨期/amendment/custom/无答案/安全恢复 | point-in-time gold、source/program/result/status 与 holdout | `planned` | `complete` |
| D9-06 | 中英配对验证 | NEW | ≥30 pair 共享 filer/accession/Evidence/program/result/status | 事实链一致率 100%，中文质量人工抽样 | `planned` | `complete` |
| D9-07 | Agent trajectory/state/security suite | BFCL/ToolSandbox/tau-bench/AgentDojo 方法 | required/allowed/forbidden Tool、partial order、final DB state、pass^k、injection | 固定版本/许可、未授权写/重复副作用/攻击指标 | `planned` | `complete` |
| D9-08 | A0～A4 release ablation | NEW | trajectory/result/evidence/runtime/point-in-time/security/cost/latency 分层 | deterministic/offline/live 分报、live≥3 次、策略回退决定 | `planned` | `complete` |

## 12. Day 10：SEC 工作台、质量与可发布版本

| ID | 目标能力与用户结果 | 来源 | 冻结范围 | 验收证据 | 当前状态 | Day 10 |
|---|---|---|---|---|---|---|
| D10-01 | SEC 产品路由与状态 | NEW | Filer/Filings、Verification、Evidence/Calculation、Monitor/Case、Eval；ambiguous/partial/conflict/insufficient | 组件、权限、刷新恢复和路由 E2E | `planned` | `complete` |
| D10-02 | 完整中文用户路径 | SEC + NEW | resolve→accession/as_of→XBRL+RAG→calculate→verify→report→approve monitor→diff Case | 无人工改库/假数据的 Playwright + 真实来源演示分报 | `planned` | `complete` |
| D10-03 | 完整 CI 与供应链门禁 | NEW | format/type/test/build/migration/OpenAPI/Gitleaks/Semgrep/audit/license/NOTICE/coverage | 干净 branch/main CI、核心≥90%、后端≥80%、关键前端≥75% | `planned` | `complete` |
| D10-04 | SEC Agent 可观测与审计 | NEW | request/job/run/tool/evidence/calculation/checkpoint/monitor/case、rate-limit/freshness/future leakage | Workbench/告警定位和固定报告可重放 | `planned` | `complete` |
| D10-05 | Agent/SEC 安全收口 | NEW | Workspace、Tool、Prompt Injection、Secret、对象访问、写审批、来源许可、免责声明 | 跨租户/未来信息/未授权写/Secret/敏感原文均为 0 | `planned` | `complete` |
| D10-06 | 环境、恢复与回滚 | NEW | fresh startup、备份恢复、Filing 索引重建、Worker/Redis/MinIO/ES/Milvus/SEC 故障、上一镜像 | Runbook 演练、正式 Scenario 继续且零重复副作用 | `planned` | `complete` |
| D10-07 | Release Eval 与完整性审计 | BENCH + NEW | 全矩阵、A0～A4、fixed/public/live、D1-09、Day4 90% 债务、DoD/owner acceptance | 每项证据齐全，无未关闭阻断项和重复正式链路 | `planned` | `complete` |
| D10-08 | 文档、限制与发布候选 | NEW | README/ADR/架构/评测/Runbook/rollback、非投资建议边界、`v0.2.0-sec-disclosure-verifier` | 链接/格式/diff/secret scan、main merge CI 与所有者复核 | `planned` | `complete` |

## 13. 明确不继承的参考项目行为

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
- 用 Web 摘要、最新缓存或错误 accession 代替回答时实际可见的 SEC filing。
- 让模型不可审计地心算、混用单位/期间/修订，或输出预测、估值、目标价、荐股和交易动作。

## 14. 格式与功能真值原则

文档、附件、Provider 和页面都必须用真实夹具或真实合规来源验证。UI `accept`、类型声明、未调用的 Service、只有接口没有用户旅程、只有页面没有后端状态，都不能证明能力完成。

Day 1～Day 10 范围以本矩阵的“冻结范围”为准。SEC 能力还必须证明 CIK/accession/form/period/`as_of`/unit/formula/source snapshot 的真值，并把 frozen replay、公开 benchmark 和 live model/tool 结果分报。任何新增、删除或降级必须先由用户确认，再同步更新主计划、产品范围、架构/ADR、测试和本矩阵的变更证据。
