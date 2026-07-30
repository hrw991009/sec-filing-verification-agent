# 新项目七天学习与开发主计划

> 计划编号：`IIP-MASTER-001`
>
> 版本：`1.0.0`
>
> 制定日期：`2026-07-23`
>
> 状态：执行基线，等待用户确认项目名称后仅修改路径与品牌名
>
> 暂定新项目目录：`D:\industry_intelligence_platform`
>
> 参考项目：`D:\my_work_project`、`D:\industry_information_assistant\industry_information_assistant`

## 0. 本文档的权威性与使用方法

这不是一次性的聊天建议，而是新项目第一阶段的执行基线。新项目创建后，应在第一次提交中把本文档复制为 `docs/master-plan.md`，并长期纳入版本控制。

以后每次继续开发，人与 AI 都必须先做四件事：

1. 阅读本计划、当前 `docs/feature-matrix.md`、相关 ADR 和上一天的学习日志。
2. 明确当前处于第几天、哪一道验收门禁，以及上一项是否真正通过。
3. 只实现当前纵向切片；不得顺手引入未评审的新框架或第二套链路。
4. 完成后更新测试结果、功能状态、技术债和学习日志，再进入下一项。

决策优先级为：用户最新明确指令 > 经用户确认的 ADR > 本主计划 > 临时实现笔记。任何影响技术栈、数据所有权、安全边界、模块职责或七天范围的变化，都必须：

- 新增或更新 ADR；
- 说明为什么变、替代方案、迁移代价和回滚办法；
- 更新本文版本号与末尾变更记录；
- 不得由 AI 静默改变。

推荐以后用这句话恢复上下文：

```text
先读取 docs/master-plan.md、docs/feature-matrix.md、相关 ADR 和最新 learning-log，
告诉我当前门禁、尚未完成项和本次最小可验收纵向切片，再开始操作。
```

## 1. 七天目标与诚实边界

产品定位：面向行业研究与企业知识工作的“多模态行业智能工作台”。它将两个旧项目的优势择优重构，而不是把两个旧仓库拼接起来。

七天按每天 8～10 小时设计。目标是产出 `v0.1.0-learning-foundation`：

- 真正跑通身份与工作空间、流式聊天、会话管理、多知识库、异步文档入库、混合 RAG、图片/表格证据、结构化引用、记忆 MVP、工具平台、安全 Text2SQL、Deep Research MVP、CI、评测和本地部署。
- 新闻、政策、招投标、股票、Web 搜索和定时采集拥有正式的数据模型、Provider 契约、错误语义和 UI 状态；至少接通一个合规的真实外部来源，其他未配置来源必须明确显示 `PROVIDER_NOT_CONFIGURED`。
- 所有最终要实现的能力都有稳定模块位置和后续验收项，但不把空页面、硬编码演示数据或 Mock 响应算作“已完成”。

七天不可能高质量地把两套项目的全部能力做成生产级产品。七天后继续按第 15 节的迭代路线补齐所有数据源、复杂格式、企业权限和高可用能力。若每天只能投入 3～4 小时，应把计划中的每个“日”扩展为两个自然日。

任何一天的门禁没有通过，就顺延；不得通过删测试、放宽权限、伪造数据或跳过迁移来赶进度。

## 2. 从两个项目中择优保留什么

| 能力 | 借鉴方向 | 新项目处理 | 七天目标 |
|---|---|---|---|
| PDF 版面、OCR、图片、表格 | `my_work_project` | 解析器端口 + 多模态资产模型 | PDF 真闭环；复杂 OCR 适配器续作 |
| PostgreSQL + Milvus + Elasticsearch + MinIO | `my_work_project` | 保留，但重做一致性和安全边界 | 真闭环 |
| Dense + BM25 + RRF | `my_work_project` | 保留，并增加 rerank、评测、租户过滤 | 真闭环 |
| 召回图片交给视觉模型 | `my_work_project` | 保留，并统一为 Evidence | 至少一例真闭环 |
| Alembic | `my_work_project` | 从第一天强制使用 | 完成 |
| 用户、登录、角色、工作空间 | 参考项目 | 重构为严格租户隔离 | 完成 |
| 会话、附件、多知识库 | 参考项目 | 与统一文件、知识模型整合 | 完成 |
| 长期记忆 | 参考项目 | 用户可见、可编辑、可删除、默认不滥存 | MVP |
| Web 搜索 | 参考项目 | Provider 端口 + 来源证据 + SSRF 防护 | 一个真实适配器 |
| 新闻、政策、招投标、股票 | 参考项目 | 公共来源基表 + 各领域明细表 | 契约、页面和一条真实样例 |
| 数据库浏览、Text2SQL | 参考项目 | 只读账号、AST 校验、预算和审计 | 安全薄切片 |
| Deep Research 多 Agent | 参考项目 | 一个真实状态图，去除名义编排和双轨逻辑 | MVP |
| SSE、取消、Checkpoint、恢复 | 参考项目 | 版本化事件、持久状态、幂等副作用 | 完成基本闭环 |
| 报告、图表、知识图谱 | 参考项目 | Evidence/Claim 图 + 受校验 ECharts | 报告和图表薄切片 |
| 测试、评测、CI、安全、可观测 | 两项目均不足 | 新项目从第一天建设 | 基础门禁完成 |

法律和安全边界：

- 两个旧项目都只作为只读参考。借鉴职责、交互与通用架构思想，不直接复制参考项目受版权保护的源码、文案、图片或素材。
- 引入 DeepDoc/RAGFlow 等第三方代码前，先核对许可证、保留 NOTICE 和修改说明；不确定时采用端口适配或独立实现。
- 旧仓库中曾出现服务端 Token 写入源码或前端环境变量的迹象。开始 Day 1 前必须吊销/轮换相关凭据并做 Git 历史密钥扫描，不能只删除当前文件。

## 3. 不可静默偏离的技术决策

### 3.1 总体形态

采用“模块化单体 + 独立 Celery Worker + Celery Beat Scheduler”，第一阶段不拆微服务：

```text
React Web
   │ REST / fetch-SSE
   ▼
FastAPI API ── PostgreSQL（唯一业务事实源）
   │          Redis（队列、限流、短期流事件）
   │          MinIO（私有二进制资产）
   │          Milvus（可重建向量索引）
   │          Elasticsearch（可重建 BM25 索引）
   ▼
Celery Worker ─ 文档解析 / Embedding / 索引 / LLM / Research / 采集 / 评测
   ▲
Celery Beat ── 定时创建采集、对账、清理和评测任务
```

理由：OCR、索引、模型和研究任务不能占住 Web 请求；PostgreSQL 保持唯一可信状态，Redis/Milvus/ES 都是可恢复的执行或派生层。Celery 的任务必须幂等，业务 Job 状态以 PostgreSQL 为准，不以 Celery result backend 为准。

### 3.2 技术栈

| 层 | 固定选择 |
|---|---|
| 运行时 | Python 3.13.x；Node.js 24 LTS；Day 1 把精确补丁版本写入 `.python-version`、`.nvmrc` |
| Python 管理 | `uv`，提交 `uv.lock`，生产安装使用 frozen lock |
| Web 包管理 | `pnpm` workspace，提交 `pnpm-lock.yaml`，CI 使用 frozen lock |
| 后端 | FastAPI、Pydantic v2、SQLAlchemy 2 async、psycopg 3、Alembic |
| 异步任务 | Celery 5、Redis、Celery Beat；状态与业务结果落 PostgreSQL |
| 前端 | React 19、TypeScript strict、Vite、React Router、TanStack Query |
| UI 状态 | Zustand 只保存短期 UI 状态，绝不复制服务端业务数据 |
| UI/可视化 | Ant Design、ECharts |
| 安全渲染 | `react-markdown` + `rehype-sanitize`；确需 HTML 时再经过 DOMPurify |
| 数据库 | PostgreSQL 16，所有表结构变化只用 Alembic |
| 对象存储 | MinIO，Bucket 默认私有，只保存 object key，访问用短期签名 URL |
| 检索 | Milvus 稠密向量；Elasticsearch BM25；RRF；可插拔 reranker |
| Agent | LangGraph 只用于 Deep Research，不渗透普通 CRUD/聊天 |
| Text2SQL | `sqlglot` AST 校验 + 数据源只读账户 + 查询预算 |
| API 契约 | `/api/v1` OpenAPI 为唯一契约源，生成 TypeScript 类型 |
| 测试 | pytest、pytest-asyncio、HTTPX、Testcontainers、Vitest、RTL、MSW、Playwright |
| 可观测 | JSON 日志、OpenTelemetry、Prometheus；Grafana/Tempo/Loki 为可选 profile |
| 部署 | Docker Compose 单机学习/预发布；第一周不引入 Kubernetes |

版本规则：主技术选型不可随意替换；具体依赖精确版本以 Day 1 兼容性试验后生成的 lockfile 为准。依赖文件禁止大量无上限 `>=`，Compose 镜像使用明确 tag，发布阶段可进一步锁 digest。

### 3.3 十条核心原则

1. PostgreSQL 是业务事实源；Milvus、ES、Redis 是可重建层，MinIO 是受数据库引用管理的资产层。
2. 所有租户业务资源必须带 `workspace_id`，所有 Repository 查询都必须显式接收 WorkspaceScope。
3. Router 只调用 Application Service；Service 通过 Repository/Port 工作；Worker 复用相同 Service，不复制业务逻辑。
4. Provider SDK 只能出现在 `adapters/`，领域模块不得直接依赖某家模型、搜索或存储 SDK。
5. 长任务只返回 `202 + job/run id + events_url`，不在请求内同步 OCR、Embedding、索引或 Deep Research。
6. 文档、网页、SQL 结果和模型输出一律是不可信输入，不能提升系统权限或改变工具白名单。
7. 对外副作用、重试和恢复必须幂等；跨存储失败使用 Outbox、阶段状态、补偿与对账，不假装数据库 rollback 能回滚所有系统。
8. 不保存模型原始 chain-of-thought，只保存面向用户的结论、证据和简短 reasoning summary。
9. 正式功能不能在失败时静默回退到 Mock 成功；未配置必须返回明确错误。
10. 不允许 `exec`/`eval` 模型生成代码。未来确需代码执行时必须使用无网络、非 root、只读文件系统且有资源限制的一次性沙箱。

## 4. 目标 Monorepo 与模块边界

```text
industry-intelligence-platform/
├─ apps/
│  ├─ backend/
│  │  ├─ src/industry_platform/
│  │  │  ├─ main.py
│  │  │  ├─ core/                 # config/db/security/errors/logging/telemetry
│  │  │  ├─ modules/
│  │  │  │  ├─ identity/
│  │  │  │  ├─ files/
│  │  │  │  ├─ knowledge/
│  │  │  │  ├─ ingestion/
│  │  │  │  ├─ retrieval/
│  │  │  │  ├─ evidence/
│  │  │  │  ├─ conversation/
│  │  │  │  ├─ memory/
│  │  │  │  ├─ tools/
│  │  │  │  ├─ research/
│  │  │  │  ├─ industry/
│  │  │  │  ├─ data_explorer/
│  │  │  │  ├─ jobs/
│  │  │  │  └─ evaluation/
│  │  │  ├─ ports/                # llm/parser/embed/vector/lexical/object/web/data
│  │  │  ├─ adapters/             # 具体供应商实现
│  │  │  ├─ workflows/            # research graph 等
│  │  │  └─ workers/              # Celery app/tasks/beat
│  │  ├─ migrations/
│  │  └─ tests/
│  └─ web/
│     └─ src/
│        ├─ app/
│        ├─ routes/
│        ├─ features/
│        ├─ entities/
│        └─ shared/
├─ packages/
│  └─ api-contract/               # OpenAPI 生成，禁止手改
├─ tests/
│  ├─ e2e/
│  ├─ integration/
│  └─ evaluation/
├─ evals/
│  ├─ datasets/
│  └─ reports/
├─ infra/
│  ├─ compose/
│  ├─ docker/
│  └─ observability/
├─ docs/
│  ├─ adr/
│  ├─ architecture/
│  ├─ learning-log/
│  └─ runbooks/
├─ scripts/
├─ .env.example
├─ pyproject.toml
├─ pnpm-workspace.yaml
└─ README.md
```

推荐的业务模块内部结构是 `models.py`、`schemas.py`、`repository.py`、`service.py`、`router.py`、`events.py`，需要异步处理时再增加 `tasks.py`。不要为了形式创建空文件，也不要形成千行万能 Service。

主要依赖方向：

```text
identity ← 全部租户模块
files ← knowledge / conversation / evidence
knowledge ← ingestion / jobs / parser ports
retrieval ← knowledge / vector / lexical / evidence
conversation ← retrieval / memory / tools / evidence / LLM
research ← retrieval / industry / data_explorer / evidence / tools / LLM
industry ← jobs / evidence / connector ports
evaluation → 只读观察 conversation / retrieval / research
```

`research` 不能导入具体 Milvus/ES/MinIO 客户端；`conversation` 不能导入 Router；`evaluation` 不得参与线上回答。每个 HTTP 请求、Celery task 和并发协程各自拥有独立 SQLAlchemy Session，不能共享 AsyncSession。

## 5. 核心数据模型

统一使用 UUID、UTC `timestamptz`、`created_at`、`updated_at`；可恢复删除的资源增加 `deleted_at`。所有租户数据带 `workspace_id`，并建立组合索引或唯一约束。

### 5.1 身份与审计

- `users`：email、password_hash、status、last_login_at。
- `workspaces`：name、created_by。
- `workspace_members`：workspace_id、user_id、role；角色至少 owner/admin/member/viewer。
- `refresh_sessions`：refresh_token_hash、expires_at、revoked_at、device、rotation_family。
- `audit_logs`：actor、action、resource_type/id、trace_id、sanitized_metadata。

Access Token 短期有效并只存在前端内存；Refresh Token 使用轮换的 opaque token，哈希后存库，通过 `HttpOnly + Secure + SameSite` Cookie 传递。禁止把供应商 Token 或共享 Bearer Secret 放进 `VITE_*` 或 LocalStorage。

### 5.2 文件、知识库与入库

- `file_objects`：bucket、object_key、original_name、mime_type、size、sha256、status。
- `knowledge_bases`：name、description、retrieval_config、embedding_profile、version。
- `documents`：kb_id、current_version_id、status、error_code。
- `document_versions`：file_id、parser/version、chunker/version、config、status。
- `chunks`：version_id、ordinal、page_start/end、heading_path、content、content_hash、token_count、bbox、metadata。
- `assets`：version_id、type(image/table/page)、file_id、page、bbox、caption、extracted_text。
- `chunk_asset_links`：chunk_id、asset_id、relation。
- `search_index_records`：chunk_id、index_kind、external_id、index_version、status、last_error。
- `jobs`、`job_events`、`outbox_events`：任务状态、阶段、尝试次数、事件和可靠投递。

不保存公开 MinIO URL，只保存 object key；读取前再次校验 workspace 权限并生成 5～15 分钟签名 URL。

### 5.3 会话、消息与统一证据

- `chat_sessions`：workspace_id、user_id、title、mode、status。
- `session_knowledge_bases`：session_id、kb_id。
- `turns`：session_id、client_request_id、status。
- `messages`：turn_id、role、status、content、model、token_usage、latency。
- `message_parts`：message_id、type(text/image/table/chart)、ordinal、content_json。
- `message_attachments`、`generation_runs`、`message_feedback`。
- `evidence`：kind、title、canonical_url、snapshot_file_id、locator、excerpt、content_hash、metadata。
- `message_citations`：message_id、evidence_id、ordinal、claim。

`locator` 统一表达 PDF 页码/bbox、Chunk、网页段落、SQL 表/行范围、新闻或政策 ID。这样知识库、网页、数据库和行业资讯使用同一引用系统，是新项目应超过两个旧项目的关键设计。

### 5.4 记忆、研究与知识图谱

- `memories`：user_id、scope、kind、content、source_message_id、confidence、status、expires_at。
- `research_runs`：query、status、phase、budget、max_iterations、cancel_requested_at。
- `research_steps`：step_key、agent_type、status、input/output_summary、token、cost、error。
- `research_checkpoints`：run_id、revision、state_json。
- `research_reports`：run_id、version、markdown、quality_score。
- `research_claims`：statement、confidence、verification_status。
- `claim_evidence`：claim_id、evidence_id、support_type。
- `graph_nodes`、`graph_edges`：从 claim/evidence/company/topic 派生，第一周仍存在 PostgreSQL，不引入 Neo4j。

记忆必须可查看、确认、编辑、停用和删除；默认不把全部聊天永久记住。原始 chain-of-thought 不得落库。

### 5.5 行业数据、Text2SQL 与图表

- `data_sources`、`collection_runs`、`source_items`：公共来源、外部 ID、URL、发布时间、采集时间、内容哈希。
- `news_items`、`policy_items`、`bidding_items`、`market_snapshots`：领域特有字段，避免把所有内容塞进无约束 JSON。
- `companies`、`industries`、`metric_observations`。
- `data_connections`：加密凭据引用、allowlisted schemas/tables、状态。
- `query_runs`：问题、generated_sql、validated_sql、状态、行数、结果对象、错误。
- `chart_specs`：query_run_id、chart_type、经过 Schema 校验的 ECharts option。

## 6. API、SSE 与异步一致性契约

### 6.1 REST 规则

- 统一前缀 `/api/v1`。
- 普通成功响应直接返回资源；列表使用 cursor pagination：`{items, next_cursor}`。
- 错误使用 `application/problem+json`，至少包含 `status`、`code`、`detail`、`trace_id`。
- 创建长任务的接口支持 `Idempotency-Key`，返回 `202`。
- OpenAPI 是前后端唯一契约源，生成 TypeScript 类型，不手写第二份 DTO。

关键端点范围：

```text
/auth/register|login|refresh|logout|me
/workspaces  /workspaces/{id}/members
/files/presign  /files/{id}/complete  /files/{id}/download-url
/knowledge-bases  /knowledge-bases/{id}/documents
/documents/{id}/chunks|assets|retry|reindex
/sessions  /sessions/{id}/messages|turns
/generations/{id}/events|cancel|retry
/search/hybrid  /search/web
/memories  /memories/search
/research-runs/{id}/events|report|cancel|resume
/data-connections/{id}/tables|test
/query-runs  /query-runs/{id}/chart
/industry/items|stats|collection-runs
/jobs/{id}/events|cancel|retry
```

### 6.2 SSE 信封

所有流复用同一版本化信封：

```json
{
  "schema_version": 1,
  "stream_id": "uuid",
  "sequence": 12,
  "occurred_at": "2026-07-23T10:00:00Z",
  "trace_id": "uuid",
  "type": "generation.delta",
  "payload": {}
}
```

必须满足：

- `sequence` 在一个 stream 内严格递增，一个 stream 只能有一个终态。
- 前端按 `(stream_id, sequence)` 去重，忽略未知事件类型以保持向前兼容。
- 支持 `Last-Event-ID` 断线续传，每 15 秒心跳；浏览器使用 `fetch` 读取流，以支持 Authorization 和 AbortController。
- Token delta 可放 Redis Streams 并设置 TTL；最终消息、引用、终态和关键进度必须进 PostgreSQL。

主要事件：

```text
generation.queued|started|retrieval.started|retrieval.completed
generation.tool.started|tool.completed|citation|delta
generation.completed|failed|cancelled

ingestion.accepted|stage.changed|progress|asset.created
ingestion.completed|failed|cancelled

research.started|plan.created|phase.changed|step.started
research.source.found|claim.extracted|chart.created|section.delta
research.checkpoint.saved|completed|failed|cancelled
```

### 6.3 文档入库状态和跨存储一致性

```text
uploaded → queued → validating → parsing → extracting_assets
→ chunking → embedding → vector_indexing → lexical_indexing → ready
                                      ↘ retrying / failed / cancelled
```

事务与任务流程：

1. API 创建 staging file，返回私有 MinIO 预签名上传参数。
2. 浏览器上传后调用 complete；服务端核验大小、MIME/magic bytes、哈希与对象元数据。
3. 同一 PostgreSQL 事务创建 Document、Version、Job 和 Outbox。
4. Dispatcher 投递 Celery；重复投递由 Job ID 和阶段幂等键消除。
5. Milvus/ES 使用确定性 ID `chunk_id:index_version`；两个索引成功后文档才进入 `ready`。
6. 删除先标记 `deleting`，Worker 清理两个索引和对象，最后标记 `deleted`。
7. 定时 reconciliation 比较 PostgreSQL 与外部存储，修复遗漏并报告孤儿。

检索结果从 Milvus/ES 返回后，必须回 PostgreSQL hydrate，再检查 workspace、active version 和 document status；不能只信索引中的权限字段。

### 6.4 RAG 基线

```text
问题规范化
→ workspace/KB 权限过滤
→ Query Embedding 与 BM25 并行召回
→ RRF 融合
→ Rerank
→ 去重与多样性控制
→ PostgreSQL hydrate
→ 关联图片/表格 Evidence
→ 上下文预算
→ LLM/VLM
→ 引用校验
→ SSE 回答
```

初始实验参数不是永久常量：Dense Top 40、BM25 Top 40、RRF `k=60`、Rerank Top 20、最终 6～10 个 Chunk、最多 3 张图片。任何参数变化必须由评测报告支持。

### 6.5 Deep Research 状态图

```text
scope → plan → parallel_retrieve(knowledge/web/industry/sql)
→ extract_claims → analyze → outline → write
→ verify → revise（最多 N 次）→ finalize
```

使用一个 typed `ResearchState` 和 PostgreSQL-backed checkpointer。每个节点结束后保存 checkpoint；恢复从最后成功节点继续。节点前的副作用必须幂等。硬限制包括最大步骤、并发、Token、费用、运行时间和工具 allowlist。

### 6.6 Text2SQL 安全边界

- 数据源使用独立只读账号，并限制 schema/table/column allowlist。
- 使用 sqlglot 解析完整 AST；仅允许安全 SELECT/CTE，检查 CTE 内部节点，禁止多语句、DML、DDL、COPY、CALL。
- 强制只读事务、statement timeout、最大返回行、最大扫描预算和审计。
- 图表只接受 Pydantic/JSON Schema 验证过的 ECharts 配置。
- Web 抓取在 DNS 解析后及每次跳转后阻止环回、私网、链路本地、保留地址；限制协议、响应大小、类型、跳转和超时。

## 7. 每日固定学习方式

每天按以下节奏执行：

- 60～90 分钟：学习当天概念，写 `docs/learning-log/day-N.md`，必须用自己的话解释。
- 30 分钟：先写用户故事、数据变化、API/SSE 契约、失败方式和当天验收样例。
- 4～5 小时：只做一个“页面 → API → 数据库/Worker → 页面结果”的纵向闭环。
- 60～90 分钟：单元、集成、权限、失败和浏览器测试。
- 30～60 分钟：真实演示、复盘、更新功能矩阵和 ADR、做一次有意义的 Git 提交。

学习不是照抄代码。每个关键模块应先由学习者说清楚输入、输出、状态、失败与安全边界，再编码；AI 可以解释、结对和审查，但不能用大段不可理解代码跳过学习。

任何功能只有同时具有迁移、权限与输入校验、正常/空/失败 UI、测试、日志/错误码、文档和真实用户路径，才可标记为完成。

## 8. Day 1：需求冻结、工程地基、身份与工作空间

### 学习主题

- HTTP、REST、SSE；FastAPI 依赖注入；SQLAlchemy Session 生命周期。
- React 服务端状态与本地 UI 状态；Docker Compose；Alembic。
- Access/Refresh Token、Argon2id、Cookie、安全密钥和租户隔离。
- 模块化单体、端口/适配器、ADR、Git 小步提交。

### 实现任务

1. 创建全新仓库，不把两个旧项目复制进来；把本文档复制为 `docs/master-plan.md`。
2. 创建第 4 节目录、`uv`/`pnpm` lock、严格类型检查、Ruff、mypy/pyright、ESLint、Prettier、pytest、Vitest、Playwright。
3. 写第一批 ADR：模块化单体、PostgreSQL 事实源、统一 Evidence、Celery + Redis、LangGraph 只用于 Research、鉴权方案。
4. Compose 先启动 PostgreSQL、Redis、MinIO；Milvus/ES/观测栈使用 profile，但当天验证它们能启动并有 healthcheck。中间件只绑定本机。
5. 实现 `/health/live` 和 `/health/ready`；ready 必须真实检查 PostgreSQL、Redis，不能固定成功。
6. 建立 Pydantic Settings、`.env.example`、开发/测试/生产边界；必需变量缺失时 fail fast，日志遮蔽 Token、密码、Cookie。
7. 完成注册、登录、`me`、Refresh 轮换、Logout；注册时创建默认 Workspace 与 owner membership。
8. 前端完成登录/注册、Auth Guard、基础导航、统一 API Client 和 OpenAPI 类型生成。
9. 建立 CI 快速通道：格式、lint、类型、单测、迁移 fresh upgrade、前端 build、Gitleaks、依赖扫描。

### 测试

- 注册、重复邮箱、错误密码、过期/伪造 Token、Refresh 重放、Logout。
- 未登录和用户 A 访问用户 B Workspace 的负向测试。
- 全新空库执行 `alembic upgrade head`；禁止用 `create_all` 建表。
- Playwright 完成“注册 → 登录 → 进入首页”。
- `/ready` 在数据库或 Redis 关闭时失败，`/live` 仍按进程状态响应。

### 当日产物

`docs/product-scope.md`、`docs/architecture.md`、6 份 ADR、`.env.example`、第一版 OpenAPI、CI、可启动的 Web/API/Worker/基础设施、`learning-log/day-1.md`。

### 验收门禁

- 新 clone 按 README 能启动；仓库、历史和前端构建产物没有有效密钥。
- 身份 E2E 和跨 Workspace 负向测试通过。
- 数据库只由 Alembic 创建；依赖版本进入 lockfile。
- 门禁失败时不得进入 Day 2。

### 复盘题

为什么每张业务表都要 `workspace_id`？为什么 Refresh Token 不应放 LocalStorage？为什么 Milvus 不是事实源？今天有没有为了赶进度加入特殊分支？

## 9. Day 2：可恢复的流式聊天与会话闭环

### 学习主题

- LLM Provider 与依赖反转；流式生成、Redis Streams、SSE 重连和背压。
- 数据库事务、幂等请求、AbortController、乐观 UI 与失败恢复。

### 实现任务

1. 定义 `LLMProvider`：`stream_chat`、`complete`、`embed`，统一模型名、超时、重试、Token/费用元数据。
2. 实现确定性 Fake Provider（只用于测试）和一个服务端 OpenAI-compatible Adapter。
3. 实现会话创建/列表/详情/重命名/删除、消息分页、停止生成、重试生成、自动标题。
4. 用户消息、Turn、Generation、Job、Outbox 先在一个事务落库；Worker 再生成并写 Redis Stream/最终消息。
5. 固定并验证 `generation.*` SSE 协议，支持 sequence 去重、Last-Event-ID、心跳、取消和唯一终态。
6. 模型失败时保存用户消息、已生成部分内容、失败码和可重试状态。
7. 前端完成会话侧栏、消息区、输入、流式输出、停止、重试、重命名和删除；Markdown 安全渲染。

### 测试

- Fake Provider 下事件顺序和确定性快照。
- 客户端断开、重连、重复事件、用户取消、Provider 超时/限流/半截响应。
- 会话分页、刷新后持久化、租户隔离、删除必须真实调用后端。
- 浏览器网络和构建产物中不存在供应商 Token。

### 当日产物

可独立工作的普通 AI 聊天、`docs/sse-protocol.md`、LLM Provider ADR、聊天契约测试、`learning-log/day-2.md`。

### 验收门禁

“新建会话 → 提问 → 流式显示 → 停止/重试 → 刷新查看历史 → 删除”全部通过；断线重连不重复文本；模型失败不丢用户消息。

### 复盘题

为什么 Message、Turn、Generation、Job 要分开？流中断后哪些数据在 PG、哪些在 Redis？Provider 能否不改业务代码地替换？

## 10. Day 3：多知识库、私有文件与可恢复异步入库

### 学习主题

- Presigned upload、MIME/magic bytes、SHA-256、对象存储权限。
- 任务幂等、Outbox、最终一致性、补偿与 reconciliation。
- PDF 页、标题、Chunk、bbox、图片、表格、版本化解析。

### 实现任务

1. 完成知识库 CRUD、文档列表/详情/删除/重试/重建索引。
2. 实现私有 MinIO 上传：文件名净化、扩展名 + Content-Type + magic bytes、大小/页数/解压后大小限制、SHA-256、短期签名。
3. 上传完成后立即返回 Job ID；解析、资产抽取、Chunk 和索引全部进入 Worker。
4. 实现第 6.3 节状态机，每阶段保存 idempotency key、attempt、错误码、时间戳和进度。
5. 定义 `DocumentParser.parse() -> ParsedDocument`。第一周真实支持 PDF、TXT、Markdown；Office、扫描 OCR、DeepDoc 作为正式 Adapter 续作。
6. Chunk 保存文档版本、页码、标题路径、token、bbox、parser/chunker 版本与图片/表格关联。初始 500～800 tokens、80～120 overlap，仅作为评测起点。
7. Milvus/ES 外部 ID 使用确定性 ID；实现 retry、cancel、reindex、异步 delete 与对账命令骨架。
8. 前端显示上传、阶段、失败原因、重试、文档页、Chunk、图片和表格预览。

### 测试

- 正常/空/损坏 PDF、伪扩展名、超限文件、重复文件、路径穿越、PDF/zip bomb。
- Worker 在每阶段故障、重启、重复投递、超时、取消；重复执行不产生重复 Chunk/索引/对象。
- 用户 A 不能查看用户 B 的 Job、文件、知识库或签名 URL。
- 删除过程中的外部失败能被补偿/对账发现，而不是静默留孤儿。

### 当日产物

可观察、可取消、可重试的入库流水线，`docs/ingestion-state-machine.md`、PDF 测试夹具、故障演练记录、对账命令、`learning-log/day-3.md`。

### 验收门禁

- 20 页测试 PDF 最终进入 `ready`，详情可追溯页码、Chunk 和资产。
- 上传请求不等待解析；Worker 强制重启后能恢复或安全重试。
- MinIO Bucket 不匿名公开；任一失败阶段在 PostgreSQL 中可解释。

### 复盘题

为什么需要 Document 和 DocumentVersion？跨 PG/MinIO/Milvus/ES 如何补偿？幂等键是什么？精确引用依赖哪些解析元数据？

## 11. Day 4：混合 RAG、结构化引用与多模态回答

### 学习主题

- Embedding、BM25、RRF、reranker、Query Rewrite、元数据过滤。
- Recall、MRR、nDCG、Citation precision/recall、忠实度和正确拒答。
- RAG Prompt Injection；文本、表格和图片的上下文预算。

### 实现任务

1. 实现 Milvus Dense、Elasticsearch BM25、RRF、可插拔 Reranker，并提供检索调试输出。
2. 每次检索强制过滤 workspace、选中 KB、ready 文档和 active index version；结果回 PG 二次授权/hydrate。
3. RAG 会话可选一个或多个知识库，生成结构化 Citation；点击引用打开真实页码、bbox、片段、图片或表格。
4. 文档内容被清楚包裹为不可信上下文，不得改变系统提示或调用未授权工具。
5. 实现证据门控：证据不足则拒答；生成后校验每个引用存在，不得伪造来源。
6. 选一份含图/表 PDF，将召回资产传给 VLM，完成至少一条图片或表格问答。
7. 建 20 条黄金题：12 条可回答、4 条无答案、2 条表格、2 条图片；到 Day 7 扩到 50 条。
8. 自动比较 Dense、BM25、RRF、RRF+Reranker，输出 JSON 与 Markdown 报告。

### 测试

- RRF 数学、去重、多样性、metadata filter、索引版本、跨租户零泄露。
- 每个 Citation 可反查；删除/重建索引后的对账。
- 恶意文档提示不能改变角色或工具权限；无答案题必须拒答。
- 固定 parser/model/config/seed，确保回归可解释。

### 当日产物

端到端多模态 RAG、检索调试面板、黄金数据集、评测脚本、`docs/retrieval-design.md`、`learning-log/day-4.md`。

### 验收门禁

- 小型黄金集 Recall@5 ≥ 0.80，MRR@10 记录基线。
- Citation 可解析率 100%，跨 Workspace 召回 0，无答案正确拒答率 ≥ 0.90。
- 至少一个图片/表格问题真实闭环；参数调整有实验报告而非凭感觉。

### 复盘题

错误来自召回、排序还是生成？RRF 为什么不能直接混加两种原始分数？阈值是否由数据得出？引用能否追到版本、页码和资产？

## 12. Day 5：工具平台、行业情报与安全 Text2SQL

### 学习主题

- Function Calling、JSON Schema、Tool Registry、ReAct 边界。
- SSRF、网页 Prompt Injection、来源追踪、采集去重与调度。
- SQL AST、只读事务、查询预算、可视化 Schema。

### 实现任务

1. 建统一 `ToolRegistry`：name、description、input/output schema、required_permission、timeout、budget、execute。
2. Tool Run 记录脱敏输入/输出摘要、状态、耗时、错误、来源、调用者、trace。
3. 注册 `knowledge_search`、`web_search`、`news_search`、`policy_search`、`bidding_search`、`stock_quote`、`database_schema`、`text_to_sql`、`render_chart`。
4. 当天真实接通 knowledge search、一个合规 Web/资讯 Provider、安全 Text2SQL；没有凭据的 Provider 明确返回未配置。
5. 实现第 6.6 节 SQL 防线；建立一个内置只读行业样例库，完成自然语言 → SQL → 表格 → 受校验图表。
6. 建 `source_items + 领域明细表`，所有外部内容记录来源、原链接、发布时间、采集时间、内容哈希和许可证/使用约束。
7. Celery Beat 建定时采集骨架：游标、幂等 external ID、内容哈希去重、指数退避、last success、dead-letter。
8. 前端完成资讯、政策、招投标、股票、数据库页面和 Tool 审计页；每项显示 `available/configuration_required/planned`，禁止假数据冒充可用。

### 测试

- 工具输入/输出 Schema、权限、超时、预算、重试、审计和模型越权请求。
- SSRF 拒绝 localhost、私网/保留 IP、非 HTTP(S)、恶意跳转、DNS rebinding、超大响应。
- Text2SQL 拒绝 INSERT/UPDATE/DELETE/DROP/COPY/CALL、多语句、危险 CTE，验证 timeout、行数和 allowlist。
- 外部数据重复采集不重复入库，来源链接可访问/可解释。

### 当日产物

Tool Registry/审计页、安全 Text2SQL + 图表样例、外部 Provider 契约、一个真实采集源、`docs/tool-security.md`、`docs/data-source-contract.md`、`learning-log/day-5.md`。

### 验收门禁

- 聊天可以调用知识检索和 Text2SQL，并展示来源/表格/图表。
- 破坏性 SQL 测试拒绝率 100%；模型不能绕过工具 allowlist。
- 未配置 Provider 明确失败，不返回 Mock 成功；真实外部内容都有来源和时间。

### 复盘题

SELECT 仍有哪些风险？Tool 与 Service 的边界是什么？模型如何越权、系统如何阻止？哪些能力是真实接通，哪些只有契约？

## 13. Day 6：用户可控记忆与可恢复 Deep Research

### 学习主题

- 短期上下文与长期记忆；置信度、来源、过期、删除和隐私。
- Agent 状态图、Checkpoint、Interrupt、预算、人工确认和取消传播。
- Planner、Retriever、Analyst、Writer、Verifier/Critic 的职责边界。

### 实现任务

1. 实现记忆创建、搜索、确认、编辑、停用、删除和用户开关；敏感或低置信内容不能自动永久保存。
2. 回答前检索相关记忆，并在可见调试信息中说明使用了哪些记忆；删除后不得再次使用。
3. 用 LangGraph 实现第 6.5 节一个正式 typed graph，不能定义图后又绕开图手工执行。
4. Research 每步保存输入/输出摘要、Evidence、Token、费用、耗时和 checkpoint；不保存原始思维链。
5. 支持 SSE 时间线、最大步骤/并发/Token/费用/时长、协作式取消、Worker 中断后恢复、最多 N 次 revise。
6. 研究报告的每个关键 Claim 关联 Evidence；从 Claim/Evidence 生成基础关系图和 ECharts 展示。
7. 前端完成 Research 创建、过程时间线、来源、审批/继续、取消、恢复、报告和图谱视图。
8. 有外部副作用或高成本操作时用 interrupt 请求人工确认；恢复后不得重复副作用。

### 测试

- Fake LLM/Tool 下图的事件序列确定可复现。
- 中间节点崩溃、Worker 重启、重复 resume、取消传播、预算耗尽、最大 revise。
- Prompt/tool 参数注入；其他 Workspace 不可读 Run/Checkpoint/Memory。
- 报告关键结论引用可解析；删除记忆后不参与下一次回答。

### 当日产物

可恢复 Deep Research MVP、长期记忆 MVP、研究时间线/报告/基础证据图、`docs/research-state-machine.md`、`docs/memory-policy.md`、`learning-log/day-6.md`。

### 验收门禁

- 一个问题经历 plan → 多源检索 → 分析 → 写作 → 核验 → 报告并显示全过程。
- 强制中断 Worker 后从最后 checkpoint 恢复；取消和预算真实生效。
- 报告没有无法解析的引用；记忆可见、可控、可删除。

### 复盘题

什么内容才有资格成为长期记忆？Checkpoint 哪些状态可重放？Critic 检查了什么？为什么副作用必须在 interrupt/resume 下幂等？

## 14. Day 7：全链路集成、评测、安全、可观测与发布

### 学习主题

- 测试金字塔、契约/E2E、RAG 评测；OpenTelemetry 日志/指标/Trace。
- STRIDE 威胁建模、备份恢复、镜像和 CI/CD 发布门禁。

### 实现任务

1. 完成首页、聊天、知识库、记忆、Research、新闻、政策、招投标、股票、数据库、设置路由；未完成项显示真实 readiness 状态。
2. 统一 loading/empty/error/forbidden/retry/cancelled/partial UI；SSE Hook 支持 abort、重连和 sequence 去重。
3. 完成单元、组件、真实依赖集成、OpenAPI/SSE contract、Playwright 和 RAG 回归；PR 不调用真实付费 API。
4. CI 执行 format、lint、typecheck、测试、前端 build、fresh migration、OpenAPI diff、Gitleaks、Semgrep、依赖/镜像扫描。
5. 加 JSON 日志、request_id/trace_id/job_id/run_id、OpenTelemetry；记录 API/Worker/检索/LLM 链路和 Token/费用，但不记录 Secret、完整 Prompt、全文文档、原图或 Cookie。
6. 安全收口：精确 CORS、Rate Limit、上传限制、私有对象、短签名 URL、SSRF、Markdown 消毒、审计、非 root 容器、安全头。
7. 生产式 Compose 包含 API/Web/Worker/Beat/反代/数据服务、healthcheck、restart、卷、资源限制、优雅关闭和 Alembic 迁移步骤。
8. 做 LLM 失效、Worker 重启、ES/MinIO 失效、重复任务、迁移失败演练；完成 PG/MinIO 备份恢复及从 PG 重建 Milvus/ES 的说明和至少一次演练。
9. 扩充到至少 50 条评测题，输出检索、引用、忠实度、拒答、延迟、费用报告；生成 feature matrix 和 backlog。

### 完整用户路径验收

```text
注册 → 登录 → 创建知识库 → 上传 PDF → 观察异步解析
→ 选择知识库提问 → 获得带页码/图片/表格引用的回答
→ 调用安全 Text2SQL 并看图表 → 管理记忆
→ 发起 Research → 中断并恢复 → 查看带引用报告和证据图
```

### 最低质量门禁

- 核心 domain/application 覆盖率 ≥ 90%，后端总体 ≥ 80%，前端关键 Hook/状态 ≥ 75%。
- Citation 可解析率 100%，跨租户泄露 0，高危 SQL 拒绝 100%。
- RAG 基线不低于 Day 4；相对已接受基线下降超过 2 个百分点则失败。
- 新环境可按 README 启动；`alembic upgrade head`、CI、secret scan 全绿。
- 至少完成一次备份—删除测试数据—恢复演练；发布能回退上一镜像。

### 当日产物

`README.md`、`docs/runbook.md`、`docs/security-model.md`、`docs/evaluation-report.md`、`docs/feature-matrix.md`、`docs/backlog.md`、演示录屏/截图、`learning-log/day-7.md` 和 `v0.1.0-learning-foundation` 标签。

### 复盘题

哪些能力是真可用、哪些是薄切片、哪些只是契约？故障会留下哪些孤儿？什么指标证明系统真的变好？下个迭代应由什么数据决定？

## 15. 七天后实现“两项目全部能力”的固定续作路线

七天结束不是功能清单结束。`docs/feature-matrix.md` 必须用以下状态：`complete`、`thin_slice`、`contract_only`、`blocked`、`planned`，禁止只写模糊百分比。

### 迭代 A：外部行业情报

- 分别接入合规的新闻、政策、招投标、股票 Provider；完成授权、配额、游标、补采、去重、来源快照和质量监控。
- 建公司/行业实体归一、订阅、告警、趋势指标和行业看板。
- 每个数据源都通过 contract test；禁止把一个 Provider 的私有字段泄漏进领域层。

### 迭代 B：文档智能

- DOCX、PPTX、XLSX、HTML、图片、批量导入、扫描 PDF OCR、复杂表格、版面模型。
- 在许可证允许的前提下实现 DeepDoc Adapter；比较解析准确率、页码/bbox 和表格/图片命中率。
- 文档版本、重新解析、索引滚动升级和批量 reconciliation。

### 迭代 C：分析与 Research 质量

- 多数据库 Text2SQL、语义层、指标口径、结果缓存和更严格成本预算。
- 更强的 Claim 核验、来源冲突处理、报告模板、导出和知识图谱。
- 若确需 Python 分析，新增独立一次性沙箱执行器；绝不在 API/Worker 进程内 `exec`。

### 迭代 D：企业与生产工程

- 细粒度 RBAC/ABAC、配额、审计导出、数据保留、隐私请求和可选 PostgreSQL RLS。
- 告警、容量规划、压力/故障测试、备份、灾难恢复、高可用和滚动发布。
- Nightly 全量 RAG/Agent 评测、人工抽检、反馈闭环和成本优化。

## 16. 全局 Definition of Done

任何功能只有满足以下全部条件，才能从 `thin_slice/planned` 改为 `complete`：

- 存在真实用户旅程，不是孤立接口或空页面。
- 正常、边界、失败、权限和恢复测试齐全。
- 有 Alembic migration、OpenAPI/SSE 契约和兼容策略。
- 有结构化日志、指标、Trace 和稳定错误码。
- 有数据所有权、删除、补偿、备份/恢复策略。
- 完成威胁与隐私检查，日志/前端没有 Secret 或敏感原文泄漏。
- RAG/Agent/性能相关功能进入可重复评测基线。
- README/Runbook 写清启动、限制、故障和回滚。
- 清除调试输出、硬编码、静默 Mock、临时旁路和重复正式链路。
- 在干净环境或 staging 完成演示。

测试结构建议：60% 领域单元测试、25% 组件/集成测试、10% 契约测试、5% 关键 E2E；RAG/Agent evaluation 作为独立门禁。Flaky test 必须修复，不能长期靠 rerun 掩盖。

## 17. 防偏航与明确禁止项

- 每天 WIP 上限为一个纵向切片；未过 Day 1～4 门禁，不进入花哨 Agent 或可视化。
- 禁止建立 `backend/app`、`backend/service` 两套入口，禁止 v1/v2/v3 多条正式链路并存。
- 禁止 `Base.metadata.create_all()`、手工 ALTER 或启动时自动改表。
- 禁止源码、`.env`、前端 `VITE_*`、日志、测试快照中出现真实服务端密钥。
- 禁止带凭据时使用 `allow_origins=["*"]`，禁止公开 MinIO Bucket。
- 禁止无 workspace scope 的 SQL、向量和关键词检索。
- 禁止请求内同步 OCR/索引/Research，禁止进程内 dict 保存任务、取消或研究状态。
- 禁止无限上传读入内存，禁止把外部 URL 抓取当作普通可信请求。
- 禁止未消毒 Markdown/HTML 和 `dangerouslySetInnerHTML`。
- 禁止执行模型生成代码、保存原始思维链、让模型自行扩大工具权限或预算。
- 禁止 1000 行万能 Service/Page，禁止在业务模块散落 Provider SDK。
- 禁止提交模型权重、运行日志、PID、缓存、生成报告和工具二进制。
- 禁止只在前端“删除”资源，禁止硬编码 localhost API，禁止长期公开对象 URL。
- 禁止用硬编码/Mock 数据把未完成的资讯、股票、政策或招投标页面伪装成可用。
- 七天内不引入 Kubernetes、微服务、Neo4j 或多套重叠的可观测平台。

若本机资源不足以同时运行 Milvus、ES 和观测栈，使用 Compose profiles 分时启动，但不得改变目标架构或用低质量实现冒充最终方案。若没有外部/付费 Provider，测试使用 Fake Adapter，正式接口必须返回未配置错误。若解析器兼容性阻塞，先通过 Parser Port 交付基础 PDF Adapter，再单独解决许可和依赖，不能把解析逻辑耦合进业务 Service。

## 18. 权威技术参考

- FastAPI Background Tasks：https://fastapi.tiangolo.com/tutorial/background-tasks/
- Python 版本生命周期：https://devguide.python.org/versions/
- Node.js Releases：https://nodejs.org/en/about/previous-releases
- SQLAlchemy Session/AsyncSession：https://docs.sqlalchemy.org/en/20/orm/session_basics.html
- Alembic Tutorial：https://alembic.sqlalchemy.org/en/latest/tutorial.html
- Celery Tasks/idempotency/retry：https://docs.celeryq.dev/en/stable/userguide/tasks.html
- LangGraph Persistence：https://docs.langchain.com/oss/python/langgraph/persistence
- LangGraph Interrupts：https://docs.langchain.com/oss/python/langgraph/interrupts
- MinIO Python presigned API：https://docs.min.io/aistor/developers/sdk/python/api/
- OpenTelemetry Python：https://opentelemetry.io/docs/languages/python/getting-started/
- TanStack Query：https://tanstack.com/query/latest/docs/framework/react/installation
- React Router：https://reactrouter.com/start/data/routing

## 19. 变更记录

| 版本 | 日期 | 变化 | 决策人 |
|---|---|---|---|
| 1.0.0 | 2026-07-23 | 首版：冻结产品边界、架构、七天学习/实现/测试门禁与后续全功能路线 | 待用户确认 |
