# 行业智能平台七天 AI Agent 学习与开发主计划

> 计划编号：`IIP-MASTER-001`
>
> 版本：`1.6.0`
>
> 制定日期：`2026-07-23`
>
> 修订日期：`2026-08-12`
>
> 状态：Agent-first 执行基线
>
> 项目目录：`D:\industry_intelligence_platform`
>
> 参考项目：`D:\my_work_project`、`D:\industry_information_assistant\industry_information_assistant`

## 0. 本文档的权威性与使用方法

这不是一次性的聊天建议，而是新项目 Day 1～Day 7 的执行基线。新项目创建后，应在第一次提交中把本文档复制为 `docs/master-plan.md`，并长期纳入版本控制。

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
告诉我当前 Agent 演进级别、Runtime/Harness 门禁、尚未完成项和本次最小可评测纵向切片，
再开始操作。
```

## 1. Agent-first 七天目标与诚实边界

产品定位不变：面向行业研究与企业知识工作的“多模态行业智能工作台”。但本计划的学习和工程主线调整为 **AI Agent 的构建、运行、调试与评测**，而不是平均用力完成一个大而全的平台。

七天按每天 8～10 小时设计，目标是产出 `v0.1.0-agent-learning-foundation`。时间预算固定为：

- 85%：完整 Agent 核心栈——Runtime/Harness、Tool Use、Short/Long-term Memory、Context Engineering、Knowledge/RAG、Deep Research、durable execution 与 Eval。
- 15%：身份、租户、任务队列、存储、CI 和必要安全门禁。

这 85% 不是再按“主次能力”切割，而是沿依赖顺序学习同一个系统：Day 2 建 Runtime，Day 3 建 Tool/Harness，Day 4 建 Memory 与 durable Research，Day 5 建可被 Agent 使用的私有知识，Day 6 才在这些前提上学习 Hybrid RAG 与多模态 Context。RAG 排得较后是因为它依赖文件入库、Evidence、检索基线和评测数据，不代表它比 Runtime、Tool 或 Memory 次要。

七天结束时，学习者必须能用自己的话并用运行证据回答：

1. 一个模型调用如何演进为有界、可观测、可恢复的 Agent run。
2. Runtime、Harness、Context、State、Short/Long-term Memory、Checkpoint、Trace、Artifact 和 Eval 分别解决什么问题。
3. Tool Call 如何被选择、校验、执行、观察和计入预算，模型为何不能自行扩大权限。
4. Memory、Tool Observation、Knowledge Retrieval 和 RAG Context 如何协作，又为什么不能混为一体。
5. Deep Research 为什么先做单 Agent/单图演进，何时才有证据引入多 Agent。
6. 如何从完整轨迹、Evidence/Claim、恢复正确性、Memory/RAG 质量、成本和延迟判断 Agent 是否真的变好。

七天的核心交付不是“页面数量”，而是同一条 Agent 正式链路：

```text
直接回答 → 流式 Run → 有界工具循环 → Agent Memory
→ Evidence/Claim 账本 → 可恢复 Deep Research
→ 私有知识与 Hybrid/Multimodal RAG → 轨迹评测与回归
```

身份与 Workspace、Job/Outbox、私有文件、Provider 端口、CI 等仍是必要底座，但它们服务于 Agent 主线，不再占据同等学习篇幅。通用安全细节继续以 ADR、`docs/security-model.md`、`docs/tool-security.md` 和自动化门禁为权威来源；主计划只保留会直接改变 Agent 行为的边界。

冻结范围描述七天内要深入学习的内容。Agent 核心能力必须使用正式数据、真实 Runtime、可恢复状态和可重复评测，不得用页面、Mock 成功或名义上的“多 Agent”冒充完成。外围行业 Adapter 和企业级运维能力允许保持诚实的 `thin_slice` 或 `contract_only`，只要不会伪装成可用，也不阻断 Agent 主线。

若每天只能投入 3～4 小时，应把一个“日”扩展为两个自然日。任何 Agent 门禁未通过就顺延；不得通过删除场景、放宽预算、绕过 Harness、伪造工具结果或只看最终答案来赶进度。

## 2. 从参考项目中提炼 Agent 能力栈

| 能力 | 借鉴方向 | 新项目处理 | 七天目标 |
|---|---|---|---|
| Agent Runtime 与 Harness | 两项目均不足 | 建立统一 Run/Step/Event、Context、Tool、Budget、Checkpoint、Trace 与评测入口 | 普通聊天、工具调用和 Research 共用同一执行语义 |
| Agent Tool 与 Skill | 参考项目 | typed Tool Registry/Executor、Harness profile、Observation、Artifact、审批与预算 | Web、行业、Text2SQL 和知识工具共用正式 Tool loop |
| Short/Long-term Memory | 参考项目 | Thread state、用户可控长期记忆、写入/召回/遗忘策略和 Memory Eval | 聊天与 Research 均能可解释地使用、更新和删除 Memory |
| Agent Knowledge 与 Hybrid RAG | `my_work_project` | 文件/知识作为 Context Source 与 Tool；Dense + BM25 + RRF + rerank + Context Compiler | 文本、图片、表格形成可评测、可引用的 Agent Context 闭环 |
| Evidence、Claim 与多模态 Artifact | 两项目方向整合 | 统一 locator、来源、支持/反驳关系和引用校验 | Research 与普通回答共享 Evidence 语义 |
| Deep Research 工作流 | 参考项目 | 从 Tool Use 演进为一个可恢复状态图；多 Agent 仅在评测证明有净收益时引入 | 一个有边界、可评测、可恢复的真实 Research 闭环 |
| SSE、取消、Checkpoint、恢复 | 参考项目 | 版本化事件、持久状态、幂等副作用 | 完成基本闭环 |
| Agent Trace、Harness 与 Eval | 两项目均不足 | 场景、Fake/Replay、故障注入、轨迹评分和成本/延迟对比 | 每次 Runtime/Prompt/Tool 变化都有回归证据 |
| PDF 版面、OCR、图片、表格 | `my_work_project` | 解析器端口 + 多模态资产模型 | 为 Agent Knowledge/RAG 提供代表性真实数据 |
| Web 搜索与行业数据 | 参考项目 | Provider 端口、来源证据与代表性真实 Adapter | 为 Tool Use/Research 提供真实外部 Observation |
| 数据库浏览与 Text2SQL | 参考项目 | 只读账号、AST 校验、预算和受校验图表 | 为 Agent 提供一个真实结构化数据 Tool |
| 会话、附件、身份与 Workspace | 参考项目 | 提供 Thread、可信 Runtime Context 和租户范围 | 足以承载核心 Agent 用户旅程 |
| PostgreSQL、Milvus、Elasticsearch、MinIO | `my_work_project` | 分别承担业务事实、向量/关键词索引和 Artifact 存储 | 支撑 Agent 状态、Knowledge/RAG 与可靠恢复 |
| CI、迁移、任务可靠性与可观测 | 两项目均不足 | 作为 Agent 开发的支撑底座，不扩张为七天主角 | 足以稳定、可重复运行核心场景 |

支撑边界：

- 两个旧项目都只作为只读参考。借鉴职责、交互与通用架构思想，不直接复制参考项目受版权保护的源码、文案、图片或素材。
- 引入 DeepDoc/RAGFlow 等第三方代码前，先核对许可证、保留 NOTICE 和修改说明；不确定时采用端口适配或独立实现。
- 旧仓库暴露的凭据处置、许可证、身份与租户隔离继续作为 Day 1 门禁，但详细做法由专项审计和 ADR 管理，不在后续 Agent 学习中反复展开。

## 3. 不可静默偏离的技术决策

### 3.1 总体形态

采用“模块化单体 + 统一 Agent Runtime/Harness + 独立 Celery Worker + Celery Beat Scheduler”，七天版本不拆微服务：

```text
React Web ── REST / fetch-SSE ──► FastAPI Application Service
                                      │
                                      ▼
┌──────────────────────── Agent Harness ────────────────────────┐
│ Instructions / Context Compiler / Memory / Retrieval-RAG      │
│ Tool Registry / Skills / Approval / Artifact / Eval hooks     │
│                                                               │
│  ┌──────────────────── Agent Runtime ──────────────────────┐   │
│  │ Run/Step lifecycle · model/tool loop · typed state      │   │
│  │ event stream · stop reason · cancel/interrupt/resume    │   │
│  │ checkpoint · trace · usage/cost accounting             │   │
│  └─────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────┘
              │ model ports                       │ typed tools
              ▼                                   ▼
       Model Provider Adapter         knowledge/web/industry/sql/files
              │                                   │
              └──────────── Evidence / Artifact ──┘
                                      │
                                      ▼
PostgreSQL（Run、Step、业务事实、Job、Outbox、Checkpoint）
Redis（队列与短期流） · MinIO（Artifact） · Milvus/ES（可重建索引）
                                      ▲
Celery Beat → Application Service → Job/Outbox → Dispatcher → Celery Worker
```

项目内术语冻结如下；不同生态对这些词的用法可能重叠，但本项目不混用：

- **Agent loop**：`model → action/tool call → observation/tool result → model`，直到 final 或明确 stop reason。
- **Agent Runtime**：生产执行语义，负责 Run/Step、typed state、循环推进、事件、预算、取消、中断、恢复、Checkpoint 和 Trace。
- **Agent Harness**：构建在 Runtime 上的 Agent 工作环境，负责 instructions、Context Compiler、Tool/Skill 组合、guardrail/审批策略、Artifact、压缩策略和 Eval hook；它不能另写第二套 loop。
- **Evaluation Harness**：以同一 Runtime/Harness 运行确定性 Fake、冻结响应 Replay、故障注入和 Scorer，不替代生产入口。
- **LangGraph**：Deep Research 内部使用的低层 orchestration runtime adapter，负责图路由、durable state、Checkpoint 与 Interrupt；它必须映射到项目统一 Run/Event/Checkpoint 语义，不形成第二套公共 Runtime API。
- **Celery/Job Runtime**：负责代码在哪个进程可靠执行、lease/fencing/retry；不决定 Agent 下一步思考或调用哪个工具。
- **Sandbox**：未来执行代码、Shell 或文件写操作时的隔离环境。七天主线不开放通用代码/Shell 工具，因此不为展示概念而造一个假 Sandbox。

理由：Agent Runtime 解决“这次智能任务如何推进”，Celery 解决“它在哪里可靠运行”，Application Service 负责业务事实和权限，三者必须分层。PostgreSQL 保持唯一可信状态；Redis/Milvus/ES 是可恢复执行或派生层。长时 Research 由 Job 启动，但其 Plan、Action、Observation、Checkpoint 和终止原因属于 Agent Runtime。

### 3.2 技术栈

| 层 | 固定选择 |
|---|---|
| 运行时 | Python 3.13.x；Node.js 24 LTS；Day 1 把精确补丁版本写入 `.python-version`、`.nvmrc` |
| Python 管理 | `uv`，提交 `uv.lock`，生产安装使用 frozen lock |
| Web 包管理 | `pnpm` workspace，提交 `pnpm-lock.yaml`，CI 使用 frozen lock |
| 后端 | FastAPI、Pydantic v2、SQLAlchemy 2 async、psycopg 3、Alembic |
| 异步任务 | Celery 5、Redis、Celery Beat、独立 Outbox Dispatcher；触发事实、状态与业务结果落 PostgreSQL |
| 前端 | React 19、TypeScript strict、Vite、React Router、TanStack Query |
| UI 状态 | Zustand 只保存短期 UI 状态，绝不复制服务端业务数据 |
| UI/可视化 | Ant Design、ECharts |
| 安全渲染 | `react-markdown` + `rehype-sanitize`；确需 HTML 时再经过 DOMPurify |
| 数据库 | PostgreSQL 16，所有表结构变化只用 Alembic |
| 对象存储 | MinIO，Bucket 默认私有，只保存 object key，访问用短期签名 URL |
| Agent Memory | Thread/checkpoint short-term memory + PostgreSQL long-term user memory；显式写入、召回、遗忘与评测策略 |
| Agent Knowledge/RAG | MinIO/Parser → Milvus Dense + Elasticsearch BM25 → RRF/rerank → Context Compiler/Evidence |
| 模型接入 | Provider-neutral Port；至少一个 OpenAI-compatible Adapter；供应商 SDK 不进入 Runtime 核心 |
| Agent Runtime | 项目内正式执行层；普通回答、工具循环和 Research 共享 Run/Step/Event/Budget 语义 |
| Agent Harness | 项目内组合与评测层；Tool/Skill、Context、Approval、Artifact、Fake/Replay/Scorer |
| Research workflow | LangGraph 只用于需要持久图状态的 Deep Research，不渗透普通 CRUD/简单聊天 |
| Text2SQL | `sqlglot` AST 校验 + 数据源只读账户 + 查询预算 |
| API 契约 | `/api/v1` OpenAPI 为唯一契约源，生成 TypeScript 类型 |
| 测试 | pytest、pytest-asyncio、HTTPX、Testcontainers、Vitest、RTL、MSW、Playwright |
| 可观测 | JSON 日志、OpenTelemetry、Prometheus；Grafana/Tempo/Loki 为可选 profile |
| 部署 | Docker Compose 单机学习/预发布；第一周不引入 Kubernetes |

版本规则：主技术选型不可随意替换；具体依赖精确版本以 Day 1 兼容性试验后生成的 lockfile 为准。依赖文件禁止大量无上限 `>=`，Compose 镜像使用明确 tag，发布阶段可进一步锁 digest。

### 3.3 十条 Agent-first 核心原则

1. 普通回答、Tool Use 和 Deep Research 共用同一 Runtime 入口；不得让聊天直连 Provider、Research 才走 Runtime。
2. 每个 Run 必须有 typed state、单调 Step sequence、版本化 Event、明确 final/stop reason 和完整 Trace。
3. Harness 只组合 Runtime、Context、Tool、Skill、Policy 与 Eval；不得变成第二套生产执行器。
4. Context、State、Short/Long-term Memory、Checkpoint、Trace 和 Artifact 分开建模；Memory 是 Agent 核心状态能力，不是 Prompt 拼接技巧，上下文压缩不等于记忆。
5. Tool 是受 Application Service 约束的 typed capability；WorkspaceScope、权限、预算和审批来自可信 Runtime Context，不来自模型参数。
6. 复杂度必须由评测换取：先单调用，再单工具、有界循环、Research 图，最后才评估多 Agent；角色名称默认表示节点职责，不等于独立 Agent。
7. Tool Observation、Memory 与 Knowledge/RAG 是同等重要但语义不同的 Context 来源；都要保留 provenance、版本和预算，未经规范化与授权不能提升为 Evidence。
8. 长任务、重试、恢复和副作用必须有预算、deadline、幂等键与持久 Checkpoint；Agent Runtime 与 Celery 的重试不能互相冒充。
9. 不保存模型原始 chain-of-thought；保存用户可见结论、Evidence、Artifact、结构化决策结果和简短 reasoning summary。
10. PostgreSQL、租户隔离、Secret、输入校验和 CI 作为底线持续生效，但只在阻断 Agent 正确性时进入主线讨论；详细规则留在专项 ADR/文档。

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
│  │  │  │  ├─ agent_runtime/       # run/step/state/events/context/budget/checkpoint
│  │  │  │  ├─ agent_harness/       # tools/skills/policies/artifacts/eval hooks
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
│  │  │  ├─ workflows/            # LangGraph research workflow adapter
│  │  │  └─ workers/              # Celery app/tasks/beat、dispatcher/reconciler 入口
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
│  ├─ scenarios/                    # Harness 场景、冻结响应与故障配置
│  └─ reports/                      # 轨迹、结果、Evidence、成本/延迟对比
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

以上目录表达职责，不要求立刻机械创建空包。推荐的业务模块内部结构是 `domain.py`、`ports.py`、`service.py`、`adapters/`、`router.py`；只有出现真实职责时才增加文件。Runtime 内部建议按 `run_service`、`state`、`events`、`context`、`tool_executor`、`policies`、`checkpoints` 拆分，Harness 按 `scenarios`、`fakes`、`replay`、`faults`、`scorers`、`reports` 拆分；实际命名服从仓库已有模式，禁止为了看起来完整制造空抽象。

主要依赖方向（`A → B` 表示 A 依赖 B）：

```text
全部租户模块 → identity
agent_runtime → provider ports / jobs / evidence；不依赖具体 Adapter 或 Web Router
agent_harness → agent_runtime / tools / memory / knowledge / retrieval / approval / evaluation ports
knowledge / conversation / evidence → files
ingestion / jobs / parser ports → knowledge
knowledge / vector / lexical / evidence → retrieval
conversation → agent_harness / evidence；简单 CRUD 不进入 Agent loop
research → agent_harness / retrieval / industry / data_explorer / evidence
industry → jobs / evidence / connector ports
evaluation → agent_harness；只读观察 Runtime / conversation / retrieval / research
```

`research` 不能直接调用 Provider SDK 或具体 Milvus/ES/MinIO 客户端；`conversation` 不能绕开 Runtime 调模型；`evaluation` 不得改变线上回答路径。Harness 的 Replay 只重放冻结的外部边界结果，不宣称模型本身确定。每个 HTTP 请求、Celery task 和并发协程各自拥有独立 SQLAlchemy Session，不能共享 AsyncSession。

## 5. 核心数据模型

统一使用 UUID、UTC `timestamptz`、`created_at`、`updated_at`；可恢复删除的资源增加 `deleted_at`。所有租户数据带 `workspace_id`，并建立组合索引或唯一约束。

### 5.1 Agent Run、会话与统一证据

- `chat_sessions`：workspace_id、user_id、title、default_mode、industry_id、status。
- `session_knowledge_bases`：session_id、kb_id。
- `turns`：session_id、client_request_id、status。
- `messages`：turn_id、role、status、content、model、token_usage、latency。
- `message_parts`：message_id、type(text/image/table/chart)、ordinal、content_json。
- `message_attachments`、`message_feedback`。
- `agent_runs`：run_type、thread/session、status、current_phase、runtime/harness version、budget snapshot、stop_reason、trace_id。
- `agent_steps`：run_id、sequence、kind(model/tool/approval/checkpoint/final)、status、input/output summary、usage、latency、error_code。
- `agent_events`：run_id、sequence、event_type、schema_version、payload、occurred_at；作为 SSE/recovery 的 append-only 业务事件源。
- `tool_calls`：step_id、tool/schema version、sanitized_arguments_hash、approval、status、result/evidence refs；不保存 Secret。
- `agent_checkpoints`：run_id、revision、state_schema_version、state_json、resume metadata。
- `run_artifacts`：run_id、kind(report/table/chart/file/evidence_set)、object/resource reference、content hash、version；普通 final message 不重复写成 Artifact。
- `evidence`：kind、title、canonical_url、snapshot_file_id、locator、excerpt、content_hash、metadata。
- `message_citations`：message_id、evidence_id、ordinal、claim。

`agent_runs` 是普通回答、工具循环与 Research 的统一执行事实，`research_runs` 是它的领域扩展，不再建立第二套互不兼容的模型调用历史。`locator` 统一表达 PDF 页码/bbox、Chunk、网页段落、SQL 表/行范围、新闻或政策 ID，使知识库、网页、数据库和行业资讯共用 Evidence。

### 5.2 Runtime 状态、记忆、研究与评测

- `memories`：user_id、scope、kind、content、source_message_id、confidence、status、expires_at。
- `research_runs`：agent_run_id、query、research brief、phase、coverage target、max_iterations、cancel_requested_at。
- `research_plans`：run_id、revision、questions、dependencies、status；计划变化显式版本化。
- `research_reports`：run_id、version、markdown、quality_score。
- `research_claims`：statement、confidence、verification_status。
- `claim_evidence`：claim_id、evidence_id、support_type。
- `graph_nodes`、`graph_edges`：从 claim/evidence/company/topic 派生，第一周仍存在 PostgreSQL，不引入 Neo4j。
- `evaluation_cases`：dataset/version、input、expected behavior、available tools、budget、deterministic fixture refs。
- `evaluation_results`：case/run/runtime/harness/model/prompt version、trajectory/output/evidence/recovery score、usage、cost、latency。

下列概念必须在数据和代码中分开：

- **LLM Context**：当前一次模型调用真正看到的有限窗口。
- **Runtime Context**：可信的 user/workspace、依赖、预算与能力对象，不直接序列化给模型。
- **Session/Thread 与 Short-term Memory**：跨多个 Run 的消息历史和 checkpointed thread state；不等于当前 Context window。
- **State**：同一个 Run 内随 Step 变化的计划、消息、中间结果和引用。
- **Checkpoint**：可恢复 State 快照；用于继续同一次执行。
- **Long-term/User Memory**：跨 Thread 可检索的用户事实或偏好；可见、可编辑、可删除。
- **Event**：按序持久的业务变化；Event log/Checkpoint 可支持恢复与 SSE replay。
- **Trace**：实际发生过什么；用于调试和评测，但不用于恢复。
- **Artifact**：除普通 final message 外，报告、表格、图表、文件和引用集等可交付结果。
- **Eval**：Scorer/Grader 对输出或轨迹赋分，并在 Dataset/Experiment 上比较；它不等于 Trace、测试或审批。
- **Compaction**：缩短 LLM Context；不等于 Memory，也不等于 Checkpoint。

记忆默认不把全部聊天永久保存。State/Checkpoint 只保存恢复所需业务状态与资源引用，不保存 Secret、完整原始材料或模型原始 chain-of-thought。

### 5.3 支撑性身份、Workspace 与审计

- `users`、`workspaces`、`workspace_members`：提供可信 Principal、WorkspaceScope 和角色事实。
- `refresh_sessions`：提供可撤销的浏览器会话；精确令牌、Cookie 和改密契约以 ADR 0006 为准。
- `user_industry_preferences`：只决定默认产品上下文，不代替 Workspace 权限。
- `audit_logs`：actor、action、resource_type/id、trace_id、sanitized_metadata。

身份层只向 Runtime Context 提供经过服务端验证的 user/workspace/capability，不把认证材料送入 LLM Context。密码学、Cookie、CORS 与会话重放的详细规则由 ADR 和身份测试维护，不在 Agent 主线重复展开。

### 5.4 Agent Knowledge/RAG 数据与可恢复入库

- `file_objects`：bucket、object_key、original_name、mime_type、size、sha256、status。
- `knowledge_bases`、`documents`、`document_versions`：检索配置、当前版本、parser/chunker version 和状态。
- `chunks`、`assets`、`chunk_asset_links`：页码、标题、内容哈希、bbox、图片/表格关联。
- `search_index_records`：可重建索引中的确定性外部 ID、版本、状态和错误。
- `jobs`、`job_events`、`outbox_events`：后台任务、lease/fencing、事件与可靠投递。
- `schedules`、`schedule_occurrences`：数据库时间、时区、misfire 和幂等 occurrence。

Knowledge Base、Document/Chunk/Asset 与检索记录是 Agent Knowledge/RAG 的核心事实和 Evidence 来源；`file_objects`、Job/Outbox 为它们提供存储与可恢复执行支撑。MinIO/Milvus/ES 的访问与一致性细节由各 Adapter/Runbook 维护，不进入 Runtime 领域模型。

### 5.5 行业数据、Text2SQL 与图表

- `data_sources`、`collection_runs`、`source_items`：公共来源、外部 ID、URL、发布时间、采集时间、内容哈希。
- `news_items`、`policy_items`、`bidding_items`、`market_snapshots`：领域特有字段，避免把所有内容塞进无约束 JSON。
- `companies`、`industries`、`metric_observations`。
- `data_connections`：加密凭据引用、allowlisted schemas/tables、状态。
- `query_runs`：问题、generated_sql、validated_sql、状态、行数、结果对象、错误。
- `chart_specs`：query_run_id、chart_type、经过 Schema 校验的 ECharts option。

## 6. Agent 执行、API、SSE 与异步一致性契约

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
/agent-runs/{id}/events|cancel|resume|artifacts|trace
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
  "type": "agent.model.delta",
  "payload": {}
}
```

必须满足：

- `sequence` 在一个 stream 内严格递增，一个 stream 只能有一个终态。
- 前端按 `(stream_id, sequence)` 去重，忽略未知事件类型以保持向前兼容。
- 每个业务事件固定使用 `id: <stream 内 sequence>`、`event: <type>`、`data: <versioned JSON envelope>`；支持 `Last-Event-ID` 断线续传，每 15 秒用不推进游标的 comment 心跳；浏览器使用 `fetch` 读取流，以支持 Authorization 和 AbortController。
- Token delta 可放 Redis Streams 并设置 TTL；最终消息、引用、终态和关键进度必须进 PostgreSQL。

所有 Agent 能力优先映射到统一事件，再按产品需要增加兼容视图：

```text
agent.run.queued|started|paused|resumed|completed|failed|cancelled
agent.step.started|completed|failed
agent.model.started|delta|completed
agent.tool.requested|approval_required|started|completed|failed
agent.evidence.added|claim.updated|artifact.created|checkpoint.saved

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
7. 定时对账（reconciliation）比较 PostgreSQL 与外部存储，修复遗漏并报告孤儿。

检索结果从 Milvus/ES 返回后，必须回 PostgreSQL 重新加载，再检查 workspace、active version 和 document status；不能只信索引中的权限字段。

### 6.4 Agent Runtime、Harness 与 Deep Research 演进

Deep Research 不从“多个角色名字”或“多 Agent”开始。项目使用同一组研究问题与评测指标逐层演进；只有前一级暴露出可复现限制，并且下一层在质量、恢复或成本上有净收益，才增加复杂度。

| 层级 | 执行形态 | 新增学习重点 | 进入下一层的证据 |
|---|---|---|---|
| L0 直接回答 | 一次模型调用，不用工具 | Provider、结构化输出、Run/Event/Trace、usage 基线 | 输出、失败和 stop reason 可记录 |
| L1 单工具 | 模型请求一个 allowlisted Tool | Action/Observation、Schema、错误回传 | 选择、参数、结果和失败可审计 |
| L2 有界循环 | `plan/decide → act → observe → update/stop` | max steps、deadline、Token/费用、取消与无效循环 | 预算真实生效，终止原因明确 |
| L3 证据账本 | Observation 规范化为 Evidence，再产生 Claim | locator、去重、support/refute/uncertain | 关键 Claim 均能定位来源 |
| L4 可恢复研究图 | typed graph + checkpoint + interrupt/resume | 长任务、分支、幂等副作用、人工审批 | 强制终止后恢复且不重复副作用 |
| L5 核验与有限修订 | `verify → bounded revise → finalize` | 引用、覆盖、矛盾、质量/成本权衡 | 相对 L0/L2 基线有可测收益 |
| L6 可选多 Agent | orchestrator-workers / specialist handoff | 上下文隔离、并行、合并冲突 | 收益显著高于延迟、Token 和调试成本 |

L6 不是七天硬指标。Planner、Retriever、Analyst、Writer、Verifier 首先是同一个正式状态图中的节点职责；并行检索是受控并发，也不自动等于多 Agent。评测结论完全可以是“当前不需要多 Agent”。

Agent Runtime 的最小接口语义：

```text
AgentRuntime.run(command) -> AsyncIterator[AgentEvent]
ContextCompiler.compile(run, step) -> ModelInput
ToolRegistry.resolve(name, runtime_context) -> TypedTool
ToolExecutor.execute(call, runtime_context) -> ToolResult
ApprovalPolicy.evaluate(call) -> allow | deny | interrupt
CheckpointStore.save/load(run_id, revision)
TrajectoryRecorder.record(event)
```

Guardrail 是自动、可判定的输入/输出/Tool 校验；Approval 是在副作用前持久暂停并等待人类 allow/deny；Eval 是事后或离线评分。三者不得用一个“安全检查”抽象混在一起。

Harness 负责把 instructions、Task Board/Todo、Context Compiler/Compaction、Tools/Skills、Artifact workspace、Guardrails、Approval、Budget、可选 handoff/subagent policy 与 Eval hook 组合成一个可运行 Agent profile。生产和测试必须调用同一个 `AgentRuntime.run`；测试 Harness 只替换 Provider/Tool 边界、注入故障和运行 Scorer。Replay 重放冻结的外部响应，不宣称真实模型可确定重放。

有界内循环：

```text
context → model decision → validate action → execute tool → observation
       ↖ update state/plan ← record observation ← normalize result
                         └→ final / stop(reason)
```

Deep Research 外层图：

```text
clarify_scope → write_research_brief → plan → research_loop
→ normalize_evidence → synthesize_claims → outline → draft
→ verify → revise（最多 N 次）/ human_review → finalize
```

`ResearchState` 至少包含 schema version、run/scope、plan/current node、pending actions、Evidence/Claim/Artifact refs、预算使用量、step/revise 计数、审批状态、取消标记、stop reason 与脱敏错误摘要。节点成功后保存 Checkpoint。外部副作用遵循“持久化意图/幂等键 → 执行 → 持久化结果”；resume 先检查已有结果。Human-in-the-loop 只用于预算扩展、高成本或不可逆操作、策略规定的冲突，不在普通节点堆形式化审批。

七天映射为：Day 2 完成 L0；Day 3 完成 L1–L2 和 Tool/Harness v1；Day 4 完成可治理 Short/Long-term Memory 与 L3–L5；Day 5 建 Agent Knowledge 与 Dense baseline；Day 6 在不改变 Runtime 的前提下完成 Hybrid/Multimodal RAG；Day 7 用统一轨迹与 Context Eval 决定是否做一个 L6 对照实验。

### 6.5 Harness 内的工具边界

- 数据源使用独立只读账号，并限制 schema/table/column allowlist。
- 使用 sqlglot 解析完整 AST；仅允许安全 SELECT/CTE，并强制只读事务、timeout、最大返回行和扫描预算。
- 图表只接受 Pydantic/JSON Schema 验证过的 ECharts 配置。
- 每个 Tool 声明 schema、capability、WorkspaceScope、timeout、budget、side-effect class 和 approval policy；模型只能请求，Harness profile 提供 surface/policy，Runtime 校验并由 ToolExecutor 执行。
- Web/文件/SQL 返回都按不可信 Observation 处理；只有规范化、授权、记录来源后才能成为 Evidence。
- 通用 SSRF、上传、Cookie、CORS 与对象存储细节不在本计划重复展开，分别由 `docs/tool-security.md`、安全 ADR 和自动化测试持续约束。
- 七天内不开放通用 Shell/代码执行 Tool；未来若需要，必须先有真正的 Sandbox、网络/文件权限和独立 ADR。

### 6.6 Agent Memory 与 Context 契约

Memory 是 Agent 核心能力，并分为两层：Short-term Memory 保存 Thread 内的消息历史、摘要和 checkpointed state；Long-term/User Memory 保存跨 Thread 可复用、可治理的事实、偏好、目标和经验。Knowledge/RAG 保存外部文档与领域知识；三者都能进入 Context，但所有权、生命周期和评测标准不同。

```text
写入：candidate/explicit request → provenance + scope + confidence
→ policy/用户确认 → create/update/merge/reject

召回：current goal + thread state → scoped candidates → rank/deduplicate
→ conflict/freshness check → context budget → context manifest

治理：inspect → correct → disable/delete → cache/index invalidation
→ 后续 Run 验证不再注入
```

Memory write 不能由模型一句“请记住”就绕过策略；必须记录来源、scope、置信度、写入原因和用户控制结果。Memory retrieval 不能只做相似度 Top-K，还要考虑当前任务、时效、冲突、重复、敏感度和 Context 预算。发生冲突时保留版本与不确定性，不静默覆盖。

Memory Eval 至少覆盖：写入准确率、召回 precision/utility、无关或错误记忆污染率、冲突处理、Token 成本、用户修改后生效率和删除后残留率。删除后仍进入 Context 是 P0 回归失败。Memory 与 RAG 的对照实验必须分别报告：Memory 是否改善连续任务和个性化，RAG 是否改善外部事实与引用；不能只看最终答案总分。

### 6.7 Agent Knowledge/RAG 与 Context Engineering

RAG 是 Agent 的核心 Context 能力，不是独立问答外挂。它负责在有限模型窗口内为当前 Step 选择、压缩并组织外部知识；Tool loop 决定何时检索，Knowledge/RAG 负责如何取得可靠候选，Context Compiler 决定哪些 Evidence 真正进入模型。

```text
问题规范化
→ workspace/KB 权限过滤
→ Query Embedding 与 BM25 并行召回
→ RRF 融合
→ Rerank
→ 去重与多样性控制
→ PostgreSQL 重新加载并授权
→ 关联图片/表格 Evidence
→ 上下文预算
→ LLM/VLM
→ 引用校验
→ SSE 回答
```

初始实验参数不是永久常量：Dense Top 40、BM25 Top 40、RRF `k=60`、Rerank Top 20、最终 6～10 个 Chunk、最多 3 张图片。任何参数变化必须由评测报告支持。

RAG 安排在 Day 6 是依赖顺序：它需要 Day 3 的 Tool/Observation、Day 4 的 Memory/Evidence/Claim 与 Eval、Day 5 的版本化 Knowledge/Asset；不是因为 RAG 次要。完成后它与 Tool、Memory 一起成为 Harness profile 的一等 Context 能力。

## 7. 每日固定学习方式

每天使用“假设 → 场景 → 实现 → Trace → Scorer → 结论”的 Agent 学习循环：

1. 45～60 分钟：用自己的话解释当天 Runtime/Harness 概念，并写一个可证伪假设，例如“加入 plan 会提升多源覆盖，但增加步骤与成本”。
2. 30～45 分钟：先写 Harness scenario，冻结输入、可用工具、预算、预期终止原因和评分规则。
3. 3～4 小时：只增加一个 Runtime/Harness 能力或一个真实 Agent Tool，不同时重写多个层。
4. 60～90 分钟：查看完整 Trace，做正常、失败、取消、预算耗尽、恢复和重复副作用测试。
5. 60 分钟：运行确定性规则与小型数据集，比较结果、轨迹、Evidence、Token、费用和延迟；LLM-as-judge 只能作为补充。
6. 30 分钟：记录“保留/回退/继续实验”的结论，更新 feature matrix、ADR 和 `learning-log/day-N.md`。

每天的代码时间遵循第 1 节 85/15 权重；85% 的 Agent 核心时间在 Runtime/Harness、Tool、Memory、Knowledge/RAG、Deep Research 与 Eval 之间按当天依赖顺序分配，不再按重要性分层。学习不是照抄 Agent 框架示例，也不是通过增加 Agent 数量制造进展。每个关键增量必须先说清输入、State 变化、Event、终止条件、失败恢复与评分方式，再编码。

每天至少保存三类证据：一条成功 Trace、一条失败或恢复 Trace、一份可比较 Eval 报告。只展示最终答案、UI 动画或一次偶然成功不能标记完成。

## 8. Day 1：Agent-ready 工程地基与运行契约

迁移说明：若现有仓库已完成身份、Workspace、Job/Outbox 和 CI，不重写这些底座；只补齐本节的 Agent 契约、Fake/Harness scenario 与分层文档，然后重新验证门禁。

### 学习主题

- 模型调用、Agent loop、Runtime、Harness、Workflow 与 Worker Runtime 的区别。
- Run、Step、Event、State、Checkpoint、Trace、Artifact 的生命周期。
- Provider Port、Tool Port、确定性 Fake、场景驱动开发和版本化契约。
- FastAPI/SQLAlchemy/Celery/React 的最小基础；身份与 Workspace 只作为可信 Runtime Context 来源。

### 实现任务

1. 冻结第 3 节术语与分层，写 ADR：Provider-neutral、Runtime/Harness 边界、LangGraph 仅用于 Research、PostgreSQL 事实源。
2. 建立第 4 节仓库、lockfile、严格类型、迁移、测试与最小 CI；Compose 默认只启动 PG/Redis/MinIO，其余使用 profile。
3. 定义版本化 `AgentRun`、`AgentStep`、`AgentEvent`、`RunArtifact` 与 `RunBudget` 领域契约；先不实现自主循环。
4. 定义 `ModelProvider`、`TrajectoryRecorder`、`CheckpointStore` 和 `AgentRuntime` Port；供应商 SDK 只能在 Adapter。
5. 建立 Harness scenario 格式：input、runtime/harness/model/prompt version、allowed tools、budget、expected stop reason、scorers。
6. 实现确定性 Fake Model/Fake Tool 和最小 Harness CLI，使同一场景两次运行得到相同 Event 骨架；明确这只是边界可重复，不宣称真实模型确定。
7. 完成后续 Agent 所需的身份/Workspace、OpenAPI Client、私有配置和最小 Web shell；复杂会话安全细节按 ADR 实现和测试，不作为当天教学主线。
8. 建立 Job/Outbox、Worker lease/fencing 与事件底座，使长 Agent Run 可以后台执行；当天只证明一次可靠投递、取消和崩溃恢复。
9. 把 Trace ID、Run ID、Step sequence 和稳定错误码贯穿 API/Worker；日志默认不记录 Secret、完整 Prompt、全文材料或原始思维链。
10. 写 `docs/agent-runtime.md` 与第一条 Scenario，画出 Application Service、Agent Runtime、Harness、LangGraph、Celery 的依赖方向。

本节引入的 Runtime/Harness 分层会改变 ADR 0005 的依据与 Research 图。进入 Day 2 前必须同步该 ADR：保持“LangGraph 仅用于 Research”，并明确节点角色不等于独立 Agent、并行检索不等于多 Agent、普通回答和 Research 共用 Runtime。

### 测试

- Agent 领域不变量：sequence、唯一终态、预算非负、typed state/version 与 Artifact 引用。
- 同一 Scenario 使用两个 Runtime 入口是禁止项；生产与 Harness 必须调用同一个入口。
- Fake Model 正常、格式错误、超时、取消；Trace 中能解释每一步且无 Secret/原始 chain-of-thought。
- 后台 Run 在重复消息、Worker 终止和过期 lease 下只有一个正式结果；Checkpoint 与 Trace 不混用。
- 身份/Workspace 的关键正常与跨租户负向测试、fresh migration、API/Web smoke 和 secret scan 作为基础门禁通过。

### 当日产物

`docs/agent-runtime.md`、Agent 架构 ADR、第一版 Run/Step/Event 契约、Harness scenario schema、Fake Model/Tool、Trace snapshot、基础 Web/API/Worker/CI、`learning-log/day-1.md`。既有产品范围、安全审计和运维文档继续维护，但不是本日主要学习产物。

### 验收门禁

- 固定 Scenario 两次运行产生相同事件类型/顺序和 final artifact，Trace 可读且不泄密。
- Runtime 与 Harness 没有第二套 Agent loop，Celery task 只调用正式 Application/Runtime 入口。
- 新 clone 能启动基础依赖并通过迁移、身份/Workspace smoke、核心检查和 Secret 扫描。
- 无法解释 Run/Step/Checkpoint/Trace 差异时不得进入 Day 2。

### 复盘题

为什么 Agent Runtime 不是 Celery Worker？Harness 为什么不能另写一套 loop？Checkpoint、Trace 和 Memory 分别回答什么问题？Fake 的确定性边界在哪里？

## 9. Day 2：Agent Runtime v0——流式 Run 与直接回答

### 学习主题

- 一次模型调用如何成为正式 Run，而不是 Router 里的 SDK 调用。
- LLM Context 与 Runtime Context；结构化输出、流式 Step、usage、stop reason。
- SSE 重连、取消、背压、部分结果与 Run/Message/Artifact 的关系。

### 实现任务

1. 完成 `ModelProvider.stream/complete` 与 OpenAI-compatible Adapter；统一模型、usage、费用、超时和 Provider error，不把 Provider retry 伪装成 Runtime retry。
2. 实现 L0 `DirectAnswerProfile`：一次无工具模型调用复用 Runtime 的 Run/Model Step/Event/stop reason 信封，但明确它只是 baseline model run，尚不具备动态 Agent loop。
3. 实现 `ContextCompiler` v0：system instructions、用户问题、会话摘要、可信 Runtime Context 分层；记录 context manifest 与版本，不保存供应商 Secret。
4. 实现结构化最终输出和流式 delta；模型格式不合法时进入明确失败/修复策略，不用字符串猜测状态。
5. 固定 `agent.*` SSE：sequence、Last-Event-ID、心跳、snapshot、取消、唯一终态；断线只恢复输出，不重新执行已提交的模型 Step。
6. 会话、Turn、AgentRun、final Message/可选 Artifact 与 Job/Outbox 原子关联；模型失败保留用户输入、已提交 Evidence/Artifact 和可解释错误。
7. Harness 增加流式 record/replay、Provider timeout/限流/半截响应、客户端断线和取消故障注入。
8. 前端完成最小会话旅程、流式输出、停止/重试、Run 状态与 Trace 开发视图；Tool、Memory、Knowledge/RAG 仍属核心能力，按依赖顺序在 Day 3～6 逐步接入。

### 测试

- L0 固定题的 Event/Trace 与结构化 final 快照；只有生成报告/表格/文件等额外交付物时才创建 Artifact。
- 断开、重连、重复事件、取消、Provider timeout/限流/半截响应都产生稳定 stop reason。
- Context Compiler 不把 Runtime 依赖、Secret 或其他 Workspace 内容放入 LLM Context。
- 同一 Run 重连不重复模型调用；新 retry 创建可关联的新 attempt/Run，而非篡改历史。
- 会话刷新/删除与最小浏览器旅程通过。

### 当日产物

Agent Runtime v0、DirectAnswerProfile、Context Compiler v0、统一 SSE/Trace、流式故障 Scenario、普通聊天薄 UI、`learning-log/day-2.md`。

### 验收门禁

- “新建会话 → 创建 Run → 流式回答 → 停止/重试 → 刷新恢复”全部经过同一 Runtime。
- Trace 能解释 Context 版本、Model Step、usage、stop reason 和 final output/可选 Artifact；断线不重复执行。
- Harness 能重现至少三种 Provider 故障，且 UI/事件不把失败伪装成成功。

### 复盘题

为什么 L0 复用 Runtime 信封却还不是 Agent loop？Runtime Context 为什么不能原样送入模型？流中断后哪些事实可恢复、哪些只能重新尝试？

## 10. Day 3：Agent Harness v1——有界 Tool Use 与真实能力

### 学习主题

- Function Calling、Action/Observation、Tool Registry/Executor、Plan–Act–Observe。
- Harness profile、Tool/Skill、预算、deadline、审批、Artifact workspace 与轨迹评分。
- 真实 Web/行业查询和 Text2SQL 如何成为 typed capability，而不是模型特权。

### 实现任务

1. 建立 `ToolRegistry` 与 `ToolExecutor`：name/version、description、typed input/output、capability、scope、timeout、cost class、side-effect class、approval policy。
2. Runtime 实现 L1 单工具与 L2 有界循环；每轮只接受结构化 action，执行前验证，Observation 归一化后再注入下一次模型 Context。
3. 冻结所有停止条件：final、max_steps、deadline、token/cost budget、cancelled、tool_denied、tool_error、no_progress；禁止无限自省。
4. Harness 增加 Tool fake、参数 matcher、timeout/error/duplicate result 注入、trajectory scorer，并输出“为什么调用/为什么停止”的报告。
5. 真实接通一个 Web Search/行业来源工具和一个 Text2SQL 链路；知识检索先保留 Tool 契约，Day 5/6 再接真实私有数据。
6. Text2SQL 由工具内部完成 schema discovery、只读 AST 校验、预算、表格 Artifact 和受校验 Chart Artifact；模型不能拿到数据库连接或执行任意 SQL。
7. 外部结果统一转为带来源、时间、locator、content hash 的 Observation；只有 Day 4 的 Evidence Normalizer 才能将其提升为 Evidence。
8. Tool/Skill 的差异写入文档：Tool 是一次 typed capability；Skill 是由 instructions、可用 Tools、Context 策略和输出契约组成的 Harness 配置，不是隐式任意代码。
9. 实现 `ContextCompiler` v1，把 Tool Observation 按来源、长度和预算安全地注入下一步模型输入；不把工具原始大响应无界拼接进 Context。
10. 行业页面和定时采集只实现代表性真实薄切片；前端优先完成 Run Trace/Tool Inspector，Provider 未配置明确返回 readiness，不为凑页面伪造数据。

### 测试

- Tool 选择、Schema、scope、deadline、budget、approval 与参数越权；拒绝后模型不能换名字绕过。
- L2 正常完成、无进展、工具超时/失败、预算耗尽、取消和重复 Observation；每条都有稳定 stop reason。
- Text2SQL 破坏性语句拒绝，Web 工具的 SSRF/响应限制由专项合同测试覆盖；主线测试聚焦 Agent 无法绕过工具边界。
- Harness 场景比较 L0、L1、L2 的完成率、步骤、工具正确率、成本和延迟。
- 真实来源有 Evidence-ready 元数据；未配置 Provider 失败而非 Mock 成功。

### 当日产物

Agent Harness v1、Tool Registry/Executor、L1/L2 Runtime、Tool Inspector、一个真实 Web/行业 Tool、Text2SQL + 表格/图表 Artifact、trajectory report、`docs/agent-harness.md`、`learning-log/day-3.md`。

### 验收门禁

- 同一问题能展示 L0 与 L2 的轨迹差异；Agent 正确调用至少两个不同 Tool，并生成可追溯 Artifact。
- max steps、deadline、Token/费用预算和取消真实生效；任何 Run 都有明确终止原因。
- 模型不能绕过 Tool allowlist/Schema/WorkspaceScope；破坏性 SQL 拒绝率 100%。
- Harness 报告包含工具选择/参数有效性、任务结果、步骤、费用和延迟，而不只评分最终文案。

### 复盘题

Tool、Skill、Application Service 和 Harness 各自负责什么？Observation 为什么还不是 Evidence？什么时候继续循环，什么时候应停止？

## 11. Day 4：Agent Memory、Evidence 与可恢复 Deep Research

### 学习主题

- Short-term/Long-term Memory 的写入、召回、更新、反馈、遗忘与评测，以及它们与 Context、State、Checkpoint、Knowledge 的边界。
- Evidence Ledger、Claim–Evidence、coverage、矛盾与不确定性。
- Durable workflow、typed state、Checkpoint、Interrupt/Resume、幂等副作用。
- Planner/Retriever/Analyst/Writer/Verifier 是节点职责还是独立 Agent；何时复杂度有净收益。
- Trace、Eval、Approval、Guardrail、Memory 与 Context 的区别。

### 实现任务

1. 在固定场景上保存 L0/L2 的 Run、Observation、Context manifest、任务结果、Token、费用和延迟，作为 Memory/Research 增量的共同基线。
2. 实现 Long-term Memory 写入闭环：explicit/candidate → provenance/scope/confidence → policy/用户确认或编辑 → create/update/merge/reject；不得默认永久保存全部聊天。
3. 实现 Memory 召回与治理：按目标、scope、时效、冲突和预算检索，记录实际注入清单；支持反馈、停用、修改和删除，并建立 precision/utility/污染/删除残留 Scorer。
4. 实现问题澄清与 research brief；原始问题、确认范围、排除项和完成标准显式保存，不能让 Planner 静默改题。
5. 实现 L3 Evidence Ledger：Observation 经授权、规范化、去重和 locator 校验后成为 Evidence；Claim 标注 support/refute/uncertain。
6. 复用 Day 3 Runtime/Harness，在 LangGraph 内实现唯一正式 typed Research graph；节点调用 Runtime/Domain Port，不直接调用 Provider SDK 或复制 tool loop。
7. 实现 L4：版本化 Checkpoint、取消、deadline/budget、interrupt/approval、resume 和副作用幂等；Worker 终止不丢正式状态。
8. 实现 L5：按 Claim 支持度、引用、覆盖、矛盾和未决问题核验，bounded revise 后输出 complete/partial/uncertain Report。
9. Harness/UI 同时呈现 Memory manifest 与 Plan、Action、Observation、Evidence、Claim、Checkpoint、Approval、stop reason；增加 Memory 冲突/删除、节点故障、重复 resume、审批和预算场景。
10. 写一次 L6 实验假设：只有哪些可测瓶颈与收益证据出现时，Day 7 才值得实验 specialist agents/handoff；此时不提前承诺采用。

### 测试

- L0 → L2 → L5 同题对照；规则 Scorer 与人工抽样能解释质量、成本和延迟变化。
- Short-term 与 Long-term Memory 不混写；候选写入、用户修正、冲突、无关召回、跨 Thread 使用和删除残留均有固定场景。
- Memory 的 provenance、scope、实际 Context 注入和反馈可追踪；删除后下一 Run 不再引用，错误 Memory 不得静默覆盖新事实。
- 每个关键 Claim 有真实 Evidence 或明确 uncertain；Verifier 不能只评价文风。
- 任一主要节点终止 Worker 后从最后 Checkpoint 恢复；重复 resume 不重复 Tool Call/副作用。
- budget、cancel、approval allow/deny/timeout、最大 revise 和 partial report 均有固定 Scenario。
- Harness/LangGraph/Runtime 只有一条正式执行链；跨 Workspace Run/Checkpoint/Memory 不可读。
- Memory Scorer 与 Research Scorer 分开报告，也能评估 Memory 对 Research 质量、Token 和污染率的影响。

### 当日产物

可治理的 Short/Long-term Memory 闭环、Memory Eval、L3–L5 Deep Research、Evidence/Claim Ledger、版本化 Checkpoint、HITL、研究时间线/报告/证据图、演进对照报告、`docs/memory-policy.md`、`docs/research-state-machine.md`、`learning-log/day-4.md`。

### 验收门禁

- 一个问题可依次展示 L0、L2、L5 的结果、Trace 与指标，而非直接展示“多 Agent”。
- Memory 的写入原因、来源、scope、实际注入、用户修改/删除都可追踪；删除后后续 Run 不再引用。
- Tool surface 与策略来自 Harness profile，Tool Call 由 Runtime 校验并经 ToolExecutor 执行；所有关键 Claim 关联 Evidence，stop reason/未确定项可解释。
- 强制中断后恢复、跨刷新审批、重复 resume、取消和预算全部真实生效且无重复副作用。
- 多 Agent 不是门禁；如采用，必须提供相对单图基线的显著净收益证据。

### 复盘题

为什么多个节点不等于多个 Agent？Observation 如何成为 Evidence？Checkpoint 能恢复什么、不能恢复什么？Verifier 的可判定标准是什么？

## 12. Day 5：Agent 知识工具——私有文件与可恢复入库

### 学习主题

- 知识库作为 Context Source 与 typed Tool，而不是独立于 Agent 的另一套回答系统。
- Document/Version/Chunk/Asset/Evidence 的可追溯关系；解析与检索配置版本。
- 长入库任务的 stage/checkpoint/idempotency，以及 Agent 面对 not-ready/partial/failed 知识的语义。

### 实现任务

1. 完成知识库/文档最小管理和私有上传；文件校验、短签名 URL 与租户权限复用专项基础设施，不把它们扩成当天主课。
2. 上传完成立即创建 Job；解析、资产抽取、Chunk、Embedding/索引在 Worker 分阶段执行，每阶段可观察、幂等、重试/取消。
3. 定义版本化 `DocumentParser -> ParsedDocument`，用数字 PDF、扫描 PDF、含图片/表格 PDF、TXT/Markdown 的代表性 fixture 验证正式 Adapter。
4. Chunk/Asset 保留文档版本、页码、标题、bbox、content hash、parser/chunker version，使 ToolResult 可转为可解析 Evidence。
5. 先实现最小 Dense Top-K baseline，再实现 `knowledge_search` Tool 与 `KnowledgeContextSource`；只返回 ready/active 版本和 Evidence-ready locator，Runtime/Harness 不感知向量客户端。BM25/RRF/rerank 留到 Day 6 做可比较增量。
6. 为 Knowledge Tool 定义 not_ready、no_result、partial_index、dependency_failed、permission_denied 的稳定 Observation，不将空结果自动解释成“文档没有相关信息”。
7. 把知识库加入 Research Harness profile：同一 Day 4 graph 不改节点/loop，只增加 Tool/Context 配置即可使用私有知识。
8. Harness 增加入库中断、重复 Job、解析失败、索引不可用、旧版本和删除后查询 Scenario；记录 Agent 的工具选择与错误解释是否正确。
9. 前端展示上传阶段、失败/重试、文档页/Chunk/Asset，并在 Run Trace 中显示使用的文档版本与 Evidence。
10. 对一个研究题比较“无私有知识”与“加入私有知识”两次 Run，记录新增 Claim 支持、无关召回、Token、费用和延迟。

### 测试

- 代表性正常/损坏/超限文件与 Parser fixture；更完整上传安全矩阵保留在专项测试。
- Worker 各主要阶段故障、重启、重复投递、取消；不产生重复 Chunk/索引/Artifact。
- Knowledge Tool 的 workspace/version/status 过滤、Evidence locator 与 dependency failure。
- Research 不改 Runtime/graph 即可消费新 Tool；知识未就绪或无结果时不伪造 Evidence。
- 文档删除/新版本后，旧 Evidence 可追溯但不会被新 Run 当作 active Context。

### 当日产物

可观察的入库流水线、Knowledge Tool/Context Source、版本化 Evidence locator、私有知识对照 Eval、`docs/ingestion-state-machine.md`、Parser fixture、`learning-log/day-5.md`。

### 验收门禁

- 代表性文档进入 ready，Research 通过同一 Harness/Runtime 检索并引用真实页码/Chunk/Asset。
- 上传不等待解析，Worker 强制中断后恢复或安全重试，失败状态对 Agent 与用户都可解释。
- 对照报告证明加入私有知识带来的 Evidence/质量变化；不能只证明“向量库返回了结果”。

### 复盘题

知识库是 Tool、Context Source 还是 Memory？为什么 Runtime 不应知道 Milvus？Agent 如何区分 no result、not ready 和 dependency failed？

## 13. Day 6：Agent Context Engine——Hybrid RAG 与多模态 Evidence

### 学习主题

- Retrieval 与 Agent Context Compiler 的边界；检索候选不等于最终模型上下文。
- Memory、Tool Observation 与 RAG Evidence 的所有权、时效、冲突和 Context 预算协调。
- Dense/BM25/RRF/rerank、Evidence budget、引用与正确拒答。
- 文本、表格、图片如何成为统一 Evidence/Artifact，并参与 Research Eval。

### 实现任务

1. 实现 Dense、BM25、RRF 与可插拔 reranker；每层输出稳定的 Retrieval Trace，不把调试分数混入用户答案。
2. 实现 `ContextCompiler` v2：合并 system/instructions、会话摘要、Memory、Tool Observation、Evidence 与 Artifact refs，按来源/多样性/Token 预算裁剪。
3. 检索强制过滤 Workspace、KB、ready/active version，结果回 PG 二次加载；这些是 Tool/Context Source 内部职责，不散落在 Prompt。
4. 实现 Query rewrite/分解的可对照策略；原始用户目标始终保留，改写结果和收益进入 Trace/Eval。
5. 实现 Evidence gate：支持度不足或冲突未解决时拒答/标 uncertain；Citation 必须指向真实 Evidence locator，生成后做结构校验。
6. 将图片/表格 Asset 作为多模态 Evidence 输入 VLM；Context manifest 记录使用的 Asset 版本和预算，不把整个文档无界塞入模型。
7. 建至少 20 条 Agent/RAG 数据集：可回答、无答案、表格、图片、多源冲突；每例同时评分 retrieval、evidence、final 与 trajectory。
8. Harness 自动比较 no-context、Memory-only、Dense、Hybrid、Memory+Hybrid，以及 Direct Answer/Research profile，分别报告 Memory 和 RAG 的边际收益与相互污染。
9. 把 Day 3 的 Web/industry/sql 与 Day 5 的 knowledge 组合进受预算控制的 Harness profile；Runtime 与 graph 保持不变，Day 6 只替换 Context/Tool 检索策略。
10. 记录一次策略回退：如果更复杂检索或改写没有净收益，保留较简单版本，并在报告中解释。

### 测试

- RRF、去重/多样性、过滤、active version 与 Context budget 的确定性测试。
- 每个 Citation/Claim 可反查 Evidence；索引重建和文档版本变化不产生悬空活跃引用。
- 不可信文档不能改变 Tool/Scope/Budget；这些约束由可信 Runtime Context 和 Harness profile 决定。
- no answer、冲突、图片、表格和 Tool failure 场景；固定数据/配置/冻结响应使回归可解释。
- Memory 与 RAG 事实冲突、过期 Memory、新文档覆盖旧事实和 Context budget 竞争；输出必须保留来源并显式处理不确定性。
- 比较报告同时呈现 Recall/MRR、引用支持度、任务完成率、Token/费用/延迟，不能只优化单一检索分数。

### 当日产物

Context Compiler v2、端到端多模态 Evidence、统一 Agent Memory/RAG 数据集、Memory/RAG 消融与策略对照报告、检索/Context Trace 面板、`docs/retrieval-design.md`、`learning-log/day-6.md`。

### 验收门禁

- 小型数据集记录 Recall@5/MRR@10 基线；Citation 可解析率 100%，跨 Workspace 召回 0。
- 无答案正确拒答率 ≥ 0.90；至少一条图片和一条表格任务形成真实 Evidence→Claim→Artifact 闭环。
- Research 接入 RAG 后相对 Day 4 基线的质量、成本和延迟变化有报告；参数选择来自实验而非感觉。
- 报告能分别回答 Memory、Dense、Hybrid 和组合 Context 是否改善任务；RAG 虽在 Day 6 学习，仍按 P0 Agent 核心能力验收。

### 复盘题

Memory、Retrieval result、Evidence 和 LLM Context 有何不同？冲突时信谁、如何保留 provenance？错误来自召回、Context 编译、推理还是引用？

## 14. Day 7：Agent Eval、演进决策与完整交付

### 学习主题

- Offline/online Eval、trajectory grading、数据集版本与基线比较。
- Agent 故障模型、恢复正确性、HITL、成本/延迟/质量权衡。
- Runtime/Harness/Prompt/Model/Tool 版本如何进入 Trace、回归与发布门禁。

### 实现任务

1. 冻结 Agent Eval schema：case/dataset、runtime/harness/model/prompt/tool/context version、budget、trace、artifact、scores 与人工备注。
2. 扩充到至少 50 个场景，覆盖直接回答、工具选择、Memory 写入/召回/冲突/遗忘、Text2SQL、Research、私有知识、RAG、多模态、审批、取消、恢复和故障。
3. 建组合 Scorer：任务完成、Tool 选择/参数、Memory precision/utility/污染/删除残留、Evidence 支持、引用解析、stop reason、恢复/副作用、Token/费用/延迟；LLM judge 仅作辅助。
4. 做消融/演进对照：L0/L2/L5；无 Memory/Memory；无 RAG/Dense/Hybrid/Memory+Hybrid；单图/可选多 Agent；报告质量收益是否值得复杂度与成本。
5. 完成 record/replay 与 fault suite：Provider timeout/限流、Tool failure、Worker hard stop、重复 resume、Checkpoint schema 不兼容、依赖不可用和取消竞态。
6. 完成 HITL 全链路：高成本/副作用 Tool 暂停，刷新/Worker 重启后仍可 approve/deny；审批事实和结果进入 Trace。
7. 统一 Web 的 Run timeline、Tool/Evidence/Claim/Artifact、partial/uncertain、retry/resume UI；外围页面只保证真实 readiness，不追求数量。
8. 用 OpenTelemetry/结构化日志串起 request/job/run/step/tool/evidence；做 Token、费用、延迟、错误和恢复成功率面板。
9. CI 运行确定性 Harness 快速集与离线回归；付费/慢模型评测作为受控任务。保留 format、type、migration、contract、secret scan 和关键 E2E，不把发布治理扩成当天主角。
10. 完成一键本地演示和 Runbook，生成 Agent evolution/evaluation 报告；根据证据明确冻结或回退 Runtime/Harness 策略。

### 完整用户路径验收

```text
登录 → 创建研究 Run → Runtime 先澄清并生成 research brief
→ Agent 调用 Web/Text2SQL/私有知识工具 → 形成 Evidence/Claim
→ 高成本步骤请求审批 → 中断 Worker → 从 Checkpoint 恢复
→ 生成带页码/网页/表格/图片引用的 Report Artifact
→ 在 Harness 中查看完整 Trace，并与 L0/L2 基线比较质量、费用和延迟
```

### 最低质量门禁

- Agent Runtime/Harness 核心 domain/application 覆盖率 ≥ 90%；关键 Web 状态和端到端旅程有测试。
- 所有 Run 唯一终态/stop reason，Tool schema/allowlist/预算不可绕过，重复副作用数为 0。
- Citation 可解析率 100%，跨租户泄露 0，Memory 删除残留率 0，无答案正确拒答率 ≥ 0.90；恢复场景成功率 100%。
- Day 7 评测不得低于已接受 Day 4/6 基线；任何策略退化必须阻断或写明回退决定。
- 新环境可按 README 运行核心 Scenario；migration、contracts、CI 和 secret scan 通过。

### 当日产物

`README.md`、`docs/agent-runtime.md`、`docs/agent-harness.md`、`docs/research-evolution-report.md`、`docs/agent-evaluation-report.md`、Harness 数据集/报告、Runbook、演示证据、`learning-log/day-7.md` 和 `v0.1.0-agent-learning-foundation` 标签。

### 复盘题

哪一层复杂度真正改善了结果？多 Agent 是否有净收益？哪个 Trace 揭示了最重要的失败？哪些指标会阻止一次“看起来更聪明”的回归？

## 15. Agent-first 能力与证据审计

`docs/feature-matrix.md` 继续记录全部产品能力，并使用 `complete`、`thin_slice`、`contract_only`、`blocked`、`planned` 等事实状态。但七天是否完成，首先由 Agent 主线证据决定，而不是由页面数量或外围 Adapter 数量决定。

Day 7 采用两级门禁：

- **核心 Agent 能力**：Runtime、Harness、Tool Use、Short/Long-term Memory、Context Engineering、Knowledge/RAG、Research、Recovery/HITL 和 Eval 必须在冻结范围内达到 `complete`。
- **支撑产品能力**：身份/Workspace、Job/Outbox、会话、文件和最小 Web 旅程必须足以真实承载核心 Run；行业长尾 Adapter、企业级运维与非核心页面允许保持诚实 `thin_slice/contract_only`，但不得伪装成可用。

任何核心项只有最终答案、Mock、截图或一次手工成功而没有 Trace、失败/恢复场景和 Eval 报告，均视为未完成。

### 15.1 必须覆盖的目标能力组

| 优先级 | 能力组 | 实现日 | 必须提交的证据 |
|---|---|---|---|
| P0 | Agent Runtime | Day 1～3 | 同一正式入口、typed state、Run/Step/Event、明确 stop reason、预算与可读 Trace |
| P0 | Agent Harness | Day 1～3 | Scenario、Fake/Replay、Fault injection、Tool/Context/Approval 组合和报告 CLI |
| P0 | Tool-using Agent | Day 3 | L1/L2 轨迹、正确 Tool/参数、预算终止、Web/Text2SQL 真实 Artifact、越权拒绝 |
| P0 | Agent Memory | Day 4 | Short/Long-term Memory 分层；写入、召回、冲突、反馈、治理与 Eval；删除后 Context 残留为 0 |
| P0 | Deep Research | Day 4 | L0/L2/L5 对照、Evidence/Claim、typed graph、bounded revise、带引用 Report |
| P0 | Durable/HITL | Day 4、Day 7 | Worker 中断恢复、重复 resume 零副作用、跨刷新 approval、取消/预算耗尽 |
| P0 | Agent Knowledge/RAG | Day 5～6 | 同一 Runtime 无改造接入私有知识、Hybrid Retrieval 与多模态 Evidence；Context manifest、引用、拒答和策略对照报告 |
| P0 | Agent Evaluation | Day 2～7 | ≥50 场景；trajectory/result/evidence/runtime 四层指标；版本与回归基线 |
| P1 | 身份、Workspace、Job/Outbox、会话、文件 | Day 1～5 | 核心 Run 所需真实链路、跨租户负向、迁移、后台执行和最小 E2E |
| P2 | 行业长尾 Adapter、外围页面、企业运维 | Day 3～7 | 至少代表性真实薄切片；其余准确标记 readiness/状态，不阻断 P0 学习主线 |

### 15.2 双向审计方法

1. 从 Scenario 出发，追到 Harness profile、Runtime/Workflow、Tool/Context、Application Service、Artifact 和 Scorer，确认只有一条正式链路。
2. 从每个 Run 反查 runtime/harness/model/prompt/tool/context version、Event、Trace、Checkpoint、Evidence、final output 与可选 Artifact。
3. 对每个复杂度增量比较前一层基线；没有净收益就回退，不把“更像 Agent”当作收益。
4. 运行完整 Research 旅程、fresh migration、API/SSE contract、跨租户负向、fault suite、Agent/RAG 回归和 secret scan。
5. 在 feature matrix 中分别记录 P0 门禁与 P1/P2 支撑状态；外围未完成项可顺延，但不得改变 P0 结果的真实性。

生产级成熟度不属于本次七天能力审计，本计划不展开其他阶段。

## 16. Agent Definition of Done

Agent 核心能力标为 `complete` 前必须同时满足：

- 生产与测试 Harness 调用同一 Runtime/Workflow，不存在图外或 Router 直连 Provider 的旁路。
- State、Event、Tool input/output、Artifact 和 Checkpoint 有版本化 typed contract；Run 有唯一终态和 stop reason。
- Tool capability、WorkspaceScope、预算、deadline、审批和 Secret 来自可信 Context，模型不能修改。
- Tool、Memory 与 Knowledge/RAG 都有独立 typed contract、provenance、版本、预算和 Scorer；它们在 Context Compiler 汇合，但不互相冒充。
- 成功、工具失败、Provider 失败、取消、预算耗尽、中断/恢复和重复请求都有 Scenario；副作用可证明幂等。
- Trace 足以解释 Context manifest、模型/工具 Step、Evidence/Claim、usage 和结果，但不保存原始 chain-of-thought 或敏感原文。
- 至少有规则/确定性 Scorer、人工抽样和可重复数据集；LLM judge 不能是唯一判据。
- 复杂策略有前后基线，报告同时包含质量、轨迹、Evidence、恢复、Token/费用和延迟。
- 真实用户可看到进度、partial/uncertain、审批、取消/恢复和 Artifact；错误不会被伪装成成功。
- 支撑层通过迁移、权限、契约、基础可观测和关键 E2E；README 说明启动、限制和回退。

测试比例不作为目标本身。优先级是：领域/策略不变量 → Runtime/Tool/Memory/Knowledge-RAG 集成 → Harness 回归 → 少量关键 E2E。Flaky 场景必须定位到数据、并发或模型边界，不能长期靠 rerun 掩盖。

## 17. Agent 防偏航与最小底线

- 禁止普通聊天、Research、Harness 各写一套 model/tool loop；所有正式执行必须经过同一 Runtime。
- 禁止把 Celery、LangGraph、Session、Context window、Memory、Checkpoint、Trace 混为同一层。
- 禁止因为增加角色、节点或并行分支就宣称“演进”；复杂度必须有对照 Eval 的净收益。
- 禁止无限循环、无限 revise、隐式 Tool retry 或无上限 Context；每个 Run 必须有 budget/deadline/stop reason。
- 禁止模型自行扩大 Tool、WorkspaceScope、预算或审批结果；Observation 不得未经校验直接成为 Evidence。
- 禁止执行模型生成的通用代码/Shell；未来开放前必须有真正 Sandbox 和独立 ADR。
- 禁止保存原始 chain-of-thought、Secret、完整敏感材料；只保存恢复和评测所需的 State、Evidence、Artifact 与摘要。
- 禁止用 Mock success、单次漂亮答案或 LLM judge 单一分数代替真实 Tool、失败恢复和组合指标。
- 禁止在业务模块散落 Provider SDK、在 LangGraph 节点复制 Application Service，或让 Harness 变成第二套生产入口。
- 通用工程底线仍然有效：Alembic、WorkspaceScope、私有 Secret、输入/输出限制、依赖锁定、关键 CI；细节由专项 ADR/文档管理，不在主计划重复堆叠。

若本机资源不足，使用 Compose profiles 分时启动派生服务，优先保证 Runtime/Harness/PG/Redis 场景可重复；分时启动不等于删减核心能力，进入 Day 5/6 后仍必须运行 Knowledge/RAG 的正式 Scenario 与 Eval。没有付费 Provider 时使用 Fake/冻结响应完成快速回归，并让正式 Adapter 明确显示未配置；不得把 Fake 结果作为真实模型质量证据。

## 18. 权威技术参考

- Runtime、Framework 与 Harness 概念：https://docs.langchain.com/oss/python/concepts/products
- OpenAI Function Calling / Tool loop：https://developers.openai.com/api/docs/guides/function-calling
- OpenAI Agents SDK：https://developers.openai.com/api/docs/guides/agents
- OpenAI Deep Research：https://developers.openai.com/api/docs/guides/deep-research
- OpenAI Agent Evals：https://developers.openai.com/api/docs/guides/agent-evals
- OpenAI Guardrails、Approval 与 Human review：https://developers.openai.com/api/docs/guides/agents/guardrails-approvals
- Anthropic Building Effective Agents：https://www.anthropic.com/engineering/building-effective-agents
- Anthropic Managed Agents 的 Session/Harness/Sandbox 分层：https://www.anthropic.com/engineering/managed-agents
- LangGraph Context：https://docs.langchain.com/oss/python/concepts/context
- LangGraph Persistence：https://docs.langchain.com/oss/python/langgraph/persistence
- LangGraph Interrupts：https://docs.langchain.com/oss/python/langgraph/interrupts
- Celery Tasks/idempotency/retry：https://docs.celeryq.dev/en/stable/userguide/tasks.html
- SQLAlchemy Session/AsyncSession：https://docs.sqlalchemy.org/en/20/orm/session_basics.html
- OpenTelemetry Python：https://opentelemetry.io/docs/languages/python/getting-started/

这些资料用于校准概念，不代表自动引入对应 SDK。尤其是 OpenAI Agents SDK、LangChain/LangGraph 与 Anthropic Agent SDK 都可以作为参考模型；依赖变更仍需 ADR，项目 Runtime/Harness 必须保持自己的 Provider-neutral 领域边界。

## 19. 变更记录

| 版本 | 日期 | 变化 | 决策人 |
|---|---|---|---|
| 1.0.0 | 2026-07-23 | 首版：冻结产品边界、架构与七天学习/实现/测试门禁 | 待用户确认 |
| 1.1.0 | 2026-08-03 | 明确七天内高质量完成能力矩阵全部目标，冻结范围只限制广度；第 15 节改为七天完整性审计，生产级成熟度不在本计划展开 | 用户 |
| 1.2.0 | 2026-08-03 | 补齐双向目标审计、DoD 适用性与许可证门禁；冻结 Beat→Outbox 可靠调度、SSE wire contract、SSRF 连接前校验和三类 PDF 独立验收 | 用户授权的质量完善 |
| 1.3.0 | 2026-08-03 | 冻结 Refresh/CSRF 同 successor 响应恢复、改密撤销全部 Session、四角色动作矩阵；补齐 Job lease/fencing/AOF/未启动对账、持久 Schedule 停机补跑与 D1-12 可靠异步底座 | 用户授权的安全与可靠性完善 |
| 1.4.0 | 2026-08-12 | 仅调整 Day 3～Day 6 的执行顺序：工具/行业/Text2SQL、记忆/Deep Research、知识库/入库、混合 RAG/多模态引用；技术范围、任务、测试和门禁保持不变 | 用户 |
| 1.5.0 | 2026-08-12 | 将七天计划重构为 Agent-first：补充统一 Runtime/Harness、Context/State/Memory/Checkpoint/Trace 边界、L0～L6 Deep Research 演进和轨迹评测；保留必要工程底座，压缩非 Harness 安全与外围平台篇幅 | 用户 |
| 1.6.0 | 2026-08-12 | 明确 Tool、Short/Long-term Memory、Knowledge/RAG 均属于 P0 Agent 核心栈；增加 Memory 契约与 Eval、Memory/RAG 消融门禁，并说明 RAG 仅因依赖顺序安排在 Day 6，不降低优先级 | 用户 |
