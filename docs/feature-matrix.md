# 七天目标能力矩阵

> 计划编号：`IIP-MASTER-001`
>
> 文档状态：已接受
>
> 更新日期：2026-08-12
>
> 权威来源：`docs/master-plan.md` 1.4.0

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
| R2 | Research 状态图、Checkpoint、四类详情、图表与报告 | D4-03～D4-07 |

### 2.2 正式模块与数据所有权登记

每个 ID 都必须落在下列唯一正式位置；实现时再在同一行的验收记录中补充具体 migration、端点、测试、CI/评测链接、限制和 DoD `N/A` 复核。没有业务数据库的工程/文档目标也必须明确写出事实所有者，不能假装由 PostgreSQL 管理。

| 目标 ID | 正式代码/文档位置 | 主要事实或数据所有者 |
|---|---|---|
| D1-01、D1-08～D1-10 | 仓库根配置、`.github/workflows`、`docs/`、`scripts/` | Git 提交、锁文件、GitHub Actions 与受版本控制文档 |
| D1-02、D1-03、D1-11、D1-12 | `infra/`、后端 platform/config/db 与 `jobs`、Alembic migrations | PostgreSQL schema/Job/Outbox/Schedule/history；Redis 是可恢复 broker；Compose 与环境配置由 Git 管理 |
| D1-04、D1-05 | 后端 `identity` | `users`、`refresh_sessions`、`workspaces`、`workspace_members`、`audit_logs` |
| D1-06 | `apps/web` identity/profile/shell 与后端 `identity` | 身份事实仍在 PostgreSQL；浏览器只保留短期 UI 状态和内存 Access Token |
| D1-07 | FastAPI OpenAPI 与 `packages/api-contract` | OpenAPI 为唯一契约源，生成物由 Git 管理 |
| D2-01～D2-07 | 后端 `conversation`、`files`、`jobs`、LLM/Web ports；前端 chat | PostgreSQL Message/Turn/Generation/Job/Evidence；Redis 只存短期流事件；附件在私有 MinIO |
| D3-01～D3-08 | `industry`、`tools`、connector adapters；前端 industry/home/chat card | PostgreSQL 行业、公司、来源、采集、指标与 Tool Run；外部 Provider 只提供输入 |
| D3-09～D3-11 | `data_explorer`、`tools`；前端 database/chart | PostgreSQL Connection/Schema Snapshot/Query Run/Chart Spec；查询结果大对象在私有 MinIO |
| D4-01～D4-07 | `memory`、`research`、`evidence`、`jobs`；前端 memory/research | PostgreSQL Memory/Run/Checkpoint/Claim/Graph/Report；Redis 仅短期事件；报告资产在私有 MinIO |
| D5-01～D5-07 | `files`、`knowledge`、`ingestion`、parser adapters；前端 knowledge | PostgreSQL 文档/版本/Chunk/任务关系，MinIO 原件与资产，Milvus/ES 可重建索引 |
| D6-01～D6-07 | `retrieval`、`evidence`、`evaluation` | PostgreSQL Evidence/Citation/评测版本；Milvus/ES 派生排名；版本化数据集与报告由 Git/对象存储管理 |
| D7-01～D7-08 | 跨模块、`tests/`、`infra/`、`docs/runbooks/`、观测配置 | 各业务事实仍由原模块拥有；CI、评测、Trace 基线、演练和审计证据分别进入 Git/GitHub/观测后端 |

## 3. Day 1：工程地基、身份与 Workspace

| ID | 目标能力与用户结果 | 来源 | 冻结的七天范围 | 验收证据 | 当前状态 | Day 7 |
|---|---|---|---|---|---|---|
| D1-01 | 可复现 Monorepo 与运行时 | NEW | uv/pnpm workspace、精确运行时、锁文件、单一后端入口 | 新 clone 锁定安装、Python/Node build | `complete` | `complete` |
| D1-02 | 本地基础设施 | R1 + NEW | PostgreSQL、Redis、私有 MinIO 默认启动；Milvus、Elasticsearch、观测 profile 验证 healthcheck | Compose 启动/停止、非公开端口、健康故障测试 | `implemented_pending_verification` | `complete` |
| D1-03 | 配置与真实健康检查 | NEW | Pydantic Settings、`.env.example`、环境边界、fail fast、`live/ready` | 缺配置启动失败；PG/Redis 故障时 ready 失败、live 仍响应 | `implemented_pending_verification` | `complete` |
| D1-04 | 注册、登录与账户安全 | R2 + NEW | email 注册/登录、`me`、修改密码（验证当前密码并撤销全部旧 Session）、EdDSA Access Token、Refresh/CSRF 同步轮换、5 秒加密 successor 响应恢复、Logout、same-site Cookie | 重复账号、错误/相同新密码、改密事务与旧 Session 失效、伪造 Token、CSRF、并发/响应丢失刷新、grace 内外重放、hostname/same-site 和限流测试 | `implemented_pending_verification` | `complete` |
| D1-05 | Workspace 与成员角色 | R2 + NEW | 注册创建默认 Workspace 和 owner；冻结 owner/admin/member/viewer 权限矩阵、成员增删/改角色规则、禁止自提权和最后一个 owner 保护；服务端 membership 授权 | 每个角色允许/拒绝矩阵、并发最后 owner、用户 A 访问用户 B 资源全部拒绝 | `implemented_pending_verification` | `complete` |
| D1-06 | 前端身份旅程 | R2 + NEW | 登录/注册、Auth Guard、用户资料、修改密码、基础导航、内存 Access Token、刷新恢复 | Playwright 注册 → 登录 → 首页 → 修改密码 → 旧会话被拒 → 新密码重新登录 → 刷新 → Logout | `implemented_pending_verification` | `complete` |
| D1-07 | 唯一 API 契约 | NEW | OpenAPI 生成 TypeScript，统一 API Client，禁止手写第二套 DTO | OpenAPI diff、生成物干净重建、契约测试 | `implemented_pending_verification` | `complete` |
| D1-08 | Python/Web/浏览器质量门 | NEW | Ruff、mypy、pytest、Prettier、ESLint、tsc、Vitest、Playwright、build | 本地与 GitHub Actions 全绿，失败探针能阻断 | `implemented_pending_verification` | `complete` |
| D1-09 | 供应链与密钥安全 | NEW | Gitleaks 新仓当前树/完整历史；参考仓历史脱敏扫描；旧凭据逐项吊销/轮换记录；依赖审计；固定 Action SHA | 无有效密钥；处置记录、Python/Node audit 和 CI 六项任务通过 | `thin_slice` | `complete` |
| D1-10 | 产品、架构与 ADR 基线 | NEW | 中文范围、能力矩阵、架构和六项 ADR 与主计划一致 | 链接/结构/内容复核和 `git diff --check` | `implemented_pending_verification` | `complete` |
| D1-11 | Alembic 与可重复数据库迁移 | R1 + NEW | 从第一张业务表起只用 Alembic；命名约定、单一 head、upgrade/downgrade 策略；禁止 `create_all`/手工改表 | 全新 PostgreSQL `upgrade head`、schema smoke、受支持降级/前滚演练、CI fresh migration | `implemented_pending_verification` | `complete` |
| D1-12 | 可靠异步执行底座 | NEW | API/Beat 事务写 Job+Outbox，独立 Dispatcher，AOF Redis，late ACK/worker-lost 配置，Job lease/heartbeat/fencing，published 未 started 与 hard-timeout 对账重投 | 发布/标记各崩溃点、Redis 接受后丢消息、Broker 断线、重复并发 Worker、soft/hard timeout、过期 lease 与唯一结果测试 | `implemented_pending_verification` | `complete` |

### 3.1 当前实现证据与待验证边界

| 目标 | 可复核的实际证据 | DoD `N/A` 说明 | 复核结论 |
|---|---|---|---|
| D1-01 | [运行时约束提交 28f885d](https://github.com/hrw991009/industry-intelligence-platform/commit/28f885d766043efc3303b75ea7611d5e63481acb)、[Monorepo 提交 ebf8872](https://github.com/hrw991009/industry-intelligence-platform/commit/ebf88723c8dc8747a868ec7c1152b801a96e91d7)、锁定安装及 Python/Node build 已在 [CI 30797166192](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/30797166192) 的干净 Ubuntu 环境通过 | 此目标不创建数据库表、HTTP/SSE 契约或运行时业务数据，因此 migration、业务权限/删除、业务遥测不适用；以锁文件、构建、干净环境复现和 Git 历史替代 | `complete`；复核人：项目所有者与执行代理，2026-08-03 |
| D1-02～D1-03 | `infra/compose/compose.yaml` 定义默认依赖与四类 profile；`core/config.py`、`core/health.py`、`core/database.py`、`core/redis_client.py` 实现配置和依赖探针 | 已有 Compose contract、配置、健康和真实依赖测试；本轮仍需统一门禁与干净 CI 实际通过 | `implemented_pending_verification`；2026-08-12 |
| D1-04～D1-05 | `modules/identity/` 与 `modules/workspaces/` 是唯一正式链路；覆盖注册、登录、刷新、改密、退出、角色矩阵、跨租户拒绝和最后 owner 的单元/HTTP/PostgreSQL/Redis 测试已写入仓库 | 不提前把“测试文件存在”当作“测试已通过”；最终真实依赖套件和 E2E 尚待本轮执行 | `implemented_pending_verification`；2026-08-12 |
| D1-06～D1-07 | `apps/web/src/auth/`、`apps/web/src/app/`、`apps/web/src/api/` 实现真实身份旅程；`industry_platform/openapi.py` 和 `packages/api-contract/` 提供唯一生成契约 | 当前生成物需要由 `pnpm run api:check`、Web 门禁和真实 Playwright 身份生命周期共同复核 | `implemented_pending_verification`；2026-08-12 |
| D1-08 | [早期 Python 门禁提交 406177b](https://github.com/hrw991009/industry-intelligence-platform/commit/406177b4d159fa96e12deac587e1d994c2ffd234)、[早期 Web/浏览器门禁提交 52964dc](https://github.com/hrw991009/industry-intelligence-platform/commit/52964dc5ca500bf27d127d56fd2b94266dd9e883) 和 [CI 30797166192](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/30797166192) 证明当时基线有效；当前 CI 已扩展 PostgreSQL/Redis、契约和真实身份 E2E | 历史 CI 不覆盖当前未提交实现；本轮完整本地门禁和新 CI 通过前不能沿用旧 `complete` 结论 | `implemented_pending_verification`；2026-08-12 |
| D1-09 | [CI 基线提交 54f7c48](https://github.com/hrw991009/industry-intelligence-platform/commit/54f7c48e8b01b194bb1ec7a0fa2f90682ef169ba) 与 [CI 30797166192](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/30797166192) 已证明新仓历史扫描及 Python/Node audit 通过；[参考仓凭据暴露审计](security/credential-exposure-audit.md) 记录 R1/R2 脱敏扫描及 6 组待处置候选 | 业务 migration、用户旅程和 RAG 评测不适用；但 Provider 侧吊销/轮换和复核证据尚缺，不能标为完成 | `thin_slice`；6 组候选全部处置并复扫后复核 |
| D1-10 | 当前文档包：[README](../README.md)、[产品范围](product-scope.md)、[能力矩阵](feature-matrix.md)、[架构与六项 ADR 索引](architecture.md#19-架构决策记录)、[Day 1 学习日志](learning-log/day-1.md) 与[参考仓凭据审计](security/credential-exposure-audit.md) 已同步到实际代码路径和运行方法 | 文档治理目标不改变业务数据；仍需最终 formatter、链接/结构复核与 `git diff --check` | `implemented_pending_verification`；2026-08-12 |
| D1-11～D1-12 | `migrations/versions/` 包含身份/Workspace 与可靠 Job/Outbox/Schedule 迁移；`modules/jobs/` 与 `workers/` 实现事务写入、独立发布、Worker fencing、对账及 DB-only Beat | migration smoke、Dispatcher/Worker/Scheduler 并发与故障测试已写入；本轮 PostgreSQL/Redis 全量套件与干净 CI 尚待实际通过 | `implemented_pending_verification`；2026-08-12 |

## 4. Day 2：聊天、会话、附件与可恢复 SSE

| ID | 目标能力与用户结果 | 来源 | 冻结的七天范围 | 验收证据 | 当前状态 | Day 7 |
|---|---|---|---|---|---|---|
| D2-01 | 可替换 LLM Provider | R1 + R2 + NEW | `stream_chat/complete/embed`；确定性 Fake 仅测试；一个服务端 OpenAI-compatible Adapter | Provider contract、超时、429、半截响应、Token/费用 | `planned` | `complete` |
| D2-02 | 完整会话管理 | R1 + R2 | 新建、列表、详情、消息分页、重命名、删除、自动标题 | 正常/空/分页/刷新/删除 E2E，删除真实调用后端 | `planned` | `complete` |
| D2-03 | 单轮搜索模式 | R2 | 每个 Turn 明确保存 `none/web/local/both`，可选一个或多个知识库 | 四种模式契约与恢复测试 | `planned` | `complete` |
| D2-04 | 聊天附件生命周期 | R1 + R2 + NEW | 上传、处理状态、关联消息、列表、删除；文本/图片真实解析，其他格式不伪装支持 | 文件类型真值表、越权/删除/失败测试 | `planned` | `complete` |
| D2-05 | 可恢复流式回答 | R1 + R2 + NEW | 202 + Job；固定 `id: sequence`/`event: type`/`data: envelope`；不推进游标的 comment 心跳；停止/重试、Last-Event-ID、snapshot、唯一终态、有界缓冲与慢客户端背压 | wire contract、非法/跨流/超前游标、断开/重连/重复/缺口/过期、慢客户端、Redis 丢失、取消测试 | `planned` | `complete` |
| D2-06 | 失败不丢用户工作 | NEW | 保存用户消息、partial、Citation、错误码和可重试状态 | Provider 失败和 Worker 中断后刷新仍可解释 | `planned` | `complete` |
| D2-07 | 安全 Markdown 与可见步骤 | R2 + NEW | 消毒 Markdown；只展示 Tool/Research 的审计摘要，不展示原始 CoT | XSS、恶意链接、Prompt 注入和日志检查 | `planned` | `complete` |

## 5. Day 3：行业、工具、数据库与图表

| ID | 目标能力与用户结果 | 来源 | 冻结的七天范围 | 验收证据 | 当前状态 | Day 7 |
|---|---|---|---|---|---|---|
| D3-01 | 首页与当前行业上下文 | R2 + NEW | 搜索/切换四个预设行业；影响推荐、资讯、招投标、聊天与 Research；不改变权限 | 刷新持久化、作用域和越权测试 | `planned` | `complete` |
| D3-02 | Tool Registry 与审计 | R2 + NEW | Schema、权限、超时、预算、重试、稳定错误和 Tool Run 页面 | 模型越权、超时、预算、Schema 和审计测试 | `planned` | `complete` |
| D3-03 | Web Search | R2 + NEW | 一个合规真实 Adapter、来源快照/摘要、Citation；SSRF 固定已验证公网 IP，保留原 Host/SNI/证书并在发送字节前确认 peer；逐跳重验、禁环境代理、网络 egress deny | localhost/私网/metadata/异常端口/userinfo、跳转、rebinding、peer/证书、代理继承、egress canary、超大/压缩响应负向测试 | `planned` | `complete` |
| D3-04 | 新闻资讯 | R2 + NEW | 真实来源样例、分类、统计、分页、原链接、行业过滤、手动采集结果 | Provider contract、真实集成、去重和来源追踪 | `planned` | `complete` |
| D3-05 | 政策 | R2 + NEW | 正式模型、搜索/筛选、来源与时间、页面 readiness；至少一条真实来源闭环 | Contract、真实样例、权限和引用测试 | `planned` | `complete` |
| D3-06 | 招投标 | R2 + NEW | 招/中标、地区、分页、原链接、手动采集；至少一条真实来源闭环 | Contract、真实样例、去重和失败测试 | `planned` | `complete` |
| D3-07 | 股票 | R2 + NEW | 真实行情 Provider、工具事件、时间/来源和聊天行情卡片 | Contract、真实样例、过期/限流/错误 UI | `planned` | `complete` |
| D3-08 | 定时采集 | R2 + NEW | 持久 Schedule/Occurrence、IANA timezone、next_due_at、停机补跑/misfire 上限；Beat→Application Service→Job/Outbox→Dispatcher；游标、external ID/hash 去重、退避、last success、dead-letter、手动立即运行 | 重复 tick/多 Beat/停机 24h/超补跑上限/时区边界/Redis 故障测试；不重复入库且遗漏、合并和失败均可见 | `planned` | `complete` |
| D3-09 | 数据库浏览 | R2 + NEW | 表大小/行数列表、Schema、主键、索引、分页数据、连接测试 | allowlist、越权、分页和错误 UI | `planned` | `complete` |
| D3-10 | 安全 Text2SQL | R2 + NEW | 只读样例库、完整 AST、schema/table/column allowlist、预算和审计 | DML/DDL/COPY/CALL/多语句/危险 CTE 拒绝 100% | `planned` | `complete` |
| D3-11 | 受校验图表 | R2 + NEW | generated/validated SQL、解释、结果表、line/bar/pie/scatter/table ECharts | 函数/脚本/外链/超量数据全部拒绝 | `planned` | `complete` |

## 6. Day 4：记忆与 Deep Research

| ID | 目标能力与用户结果 | 来源 | 冻结的七天范围 | 验收证据 | 当前状态 | Day 7 |
|---|---|---|---|---|---|---|
| D4-01 | 从会话创建可控记忆 | R2 + NEW | 候选摘要、确认/编辑、保存、来源、置信度和用户开关 | 组件、API、权限和敏感内容策略测试 | `planned` | `complete` |
| D4-02 | 记忆检索与删除 | R2 + NEW | 回答显示使用的记忆；搜索、停用、过期、删除后立即不再召回 | 删除后下一次回答不使用、跨租户为 0 | `planned` | `complete` |
| D4-03 | 唯一 typed Research graph | R2 + NEW | plan、多源检索、Claim、分析、写作、核验、有限 revise、finalize | 确定 Fake 下状态与事件序列可复现 | `planned` | `complete` |
| D4-04 | Checkpoint、中断与恢复 | R2 + NEW | PostgreSQL Checkpoint、列表/详情/删除、Worker 中断、重复 resume、取消、预算 | 从最后成功节点恢复且不重复副作用 | `planned` | `complete` |
| D4-05 | Research 完整 UI | R2 | 创建 Research、运行列表/详情、步骤时间线、审批/继续/取消/恢复、Checkpoint 列表/详情/删除，以及搜索结果/来源、证据图、图表、报告四类详情 | 创建 → 运行 → 取消/恢复 → Checkpoint 管理 → 刷新后恢复完整 UI state 的 Playwright | `planned` | `complete` |
| D4-06 | 报告、Claim 与证据图 | R2 + NEW | 章节草稿/最终报告、关键 Claim 引用、Claim/Evidence/Entity 基础图 | 引用 100% 可解析，图节点/边可反查 | `planned` | `complete` |
| D4-07 | 预算与安全边界 | NEW | 最大步骤、并发、Token、费用、时间、revise、Tool allowlist；不存原始 CoT、不 exec 模型代码 | Prompt/Tool 注入、预算耗尽、错误和审计测试 | `planned` | `complete` |

## 7. Day 5：多知识库与文档入库

| ID | 目标能力与用户结果 | 来源 | 冻结的七天范围 | 验收证据 | 当前状态 | Day 7 |
|---|---|---|---|---|---|---|
| D5-01 | 多知识库管理 | R2 + NEW | 创建、列表、详情、编辑、删除、文档计数和 Workspace 隔离 | CRUD、计数、越权和真实删除测试 | `planned` | `complete` |
| D5-02 | 私有预签名上传 | R1 + R2 + NEW | MinIO 私有；文件名、扩展名、MIME、magic bytes、大小、页数、SHA-256、像素/嵌套/解压预算 | 伪扩展名、路径穿越、空/损坏/加密 PDF、超限、重复、PDF/zip bomb、解压后超限和签名 URL 越权测试 | `planned` | `complete` |
| D5-03 | 文档格式真实能力 | R1 + R2 | PDF、TXT、Markdown；分别使用数字文本 PDF、需要 OCR 的扫描 PDF、另一份同时含图片和复杂表格且至少 20 页的 PDF；不把前端 accept 当成功 | 每种格式真实夹具；页码/bbox、OCR 文本、图片资产、复杂表格 HTML/截图、检索和 Citation 断言 | `planned` | `complete` |
| D5-04 | 版本化解析资产 | R1 + NEW | Document/Version/Chunk/Asset；页码、bbox、标题、parser/chunker 版本、图表关系 | 20 页 PDF 可追溯到 Chunk、图片和表格 | `planned` | `complete` |
| D5-05 | 可观察异步入库 | R1 + R2 + NEW | 阶段、进度、失败原因、重试、取消、reindex；页面实时展示 | 每阶段故障、Worker 重启、重复投递、超时、取消 | `planned` | `complete` |
| D5-06 | 跨存储删除与对账 | R1 + NEW | deleting → PG/Milvus/ES/MinIO 清理 → deleted；孤儿检测和人工重放 | 每个外部删除点故障与定时对账测试 | `planned` | `complete` |
| D5-07 | 文档/Chunk/资产详情 | R1 + R2 | 文档页、状态、错误、Chunk 抽屉、页面、图片、表格截图与 HTML 预览 | 组件、权限和浏览器测试 | `planned` | `complete` |

## 8. Day 6：混合 RAG、多模态 Evidence 与 Citation

| ID | 目标能力与用户结果 | 来源 | 冻结的七天范围 | 验收证据 | 当前状态 | Day 7 |
|---|---|---|---|---|---|---|
| D6-01 | Dense + BM25 + RRF + Rerank | R1 + R2 + NEW | Milvus/ES 并行召回、RRF、可插拔 Reranker、去重和多样性 | RRF 数学、过滤、版本和确定性回归 | `planned` | `complete` |
| D6-02 | 检索调试 | R1 | 独立 hybrid API 返回两路名次/分数、RRF、Rerank、过滤原因和资产命中 | 响应契约与敏感候选不泄漏测试 | `planned` | `complete` |
| D6-03 | 严格作用域检索 | NEW | Workspace、单/多 KB、ready、active version；索引结果回 PG 二次授权 | 跨 Workspace 召回 0，旧版本不命中 | `planned` | `complete` |
| D6-04 | 统一 Evidence/Citation | R1 + R2 + NEW | PDF/Chunk/图/表/Web/SQL/行业统一 locator；点击回真实来源 | Citation 可解析率 100%，删除/失效后状态可解释 | `planned` | `complete` |
| D6-05 | 多模态回答 | R1 | 召回图片/表格去重后送 VLM，至少一题图片、一题表格真实闭环 | 资产命中、实际输入数量、答案和引用测试 | `planned` | `complete` |
| D6-06 | 证据门控与 Prompt Injection 防护 | NEW | 证据不足拒答；文档内容不能改角色、Tool allowlist 或预算 | 无答案拒答 ≥ 0.90，恶意文档负向集 | `planned` | `complete` |
| D6-07 | RAG 评测基线 | NEW | Day 6 固定 20 题（12 可回答、4 无答案、2 表格、2 图片），Day 7 ≥50 题；Dense/BM25/RRF/RRF+Reranker 对比；版本化 JSON/Markdown 报告 | Recall@5 ≥0.80、Citation 可解析率 100%、无答案拒答 ≥0.90、跨租户召回 0、MRR@10/忠实度/引用/图片表格/延迟/Token/费用基线，相对已接受基线下降不超过 2 个百分点 | `planned` | `complete` |

## 9. Day 7：集成、质量与可发布学习版本

| ID | 目标能力与用户结果 | 来源 | 冻结的七天范围 | 验收证据 | 当前状态 | Day 7 |
|---|---|---|---|---|---|---|
| D7-01 | 全部路由和真实 readiness | R2 + NEW | 首页、聊天、知识库、记忆、Research、新闻、政策、招投标、股票、数据库、设置 | 路由 E2E；不可用原因真实，不出现假数据 | `planned` | `complete` |
| D7-02 | 统一 UI 失败状态 | NEW | loading/empty/error/forbidden/retry/cancelled/partial 和可操作下一步 | 关键组件与浏览器测试 | `planned` | `complete` |
| D7-03 | 完整 CI 与供应链门禁 | NEW | 全测试、fresh migration、OpenAPI diff、Gitleaks、Semgrep、依赖/镜像/许可证扫描、NOTICE/归属/修改记录核对；核心 domain/application ≥90%、后端总体 ≥80%、前端关键 Hook/状态 ≥75% | 干净 CI 全绿、覆盖率报告达标、许可证与来源清单无未处置阻断项 | `planned` | `complete` |
| D7-04 | 可观测与审计 | NEW | JSON 日志、OTel、指标/Trace、关联 ID、仪表盘、关键告警；固定场景记录 p50/p95/p99、错误率、吞吐、队列积压、Token 和费用基线 | 从故障 UI/告警定位到 trace、Job、审计；预算/限制真实生效且基线报告可重复 | `planned` | `complete` |
| D7-05 | 安全收口 | NEW | 精确 CORS/Origin、Rate Limit、same-site Cookie、上传、私有对象、短 URL、SSRF 应用校验 + 网络 egress deny、Markdown、非 root、安全头 | STRIDE、权限/输入/网络纵深负向测试、密钥/敏感原文检查 | `planned` | `complete` |
| D7-06 | Compose 与恢复演练 | NEW | 反代、Web、API、Dispatcher、Worker、Beat、PG、Redis、私有 MinIO；profile 启动 Milvus/ES/OTel/Prometheus/Grafana/Tempo/Loki；healthcheck、restart、持久卷、资源限制、非 root/只读根、优雅关闭与一次性 Alembic migration | 新环境启动、组件故障、正常停止、备份—删除—恢复、索引重建、迁移失败和上一镜像回退 | `planned` | `complete` |
| D7-07 | 完整用户路径 | R1 + R2 + NEW | 注册/登录 → 创建 KB 并观察真实解析 → 分别完成图片题和表格题及可定位引用 → Text2SQL/图表 → 保存、使用并删除记忆 → Research 中断/恢复 → 带引用报告和证据图 → Logout | 一条无人工改库/假数据的 Playwright，外加真实 Provider/来源演示证据；刷新后状态仍完整 | `planned` | `complete` |
| D7-08 | 七天能力完整性审计 | NEW | 本矩阵每一目标行均为 complete，实际证据、限制和 DoD `N/A` 复核齐全 | 双向参考项目审计，无目标遗漏和重复正式链路 | `planned` | `complete` |

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
