# 工程设计基线

本文件保存架构、数据模型与协议约束；原按天划分的学习计划及时间安排已移除。历史需求编号与验证证据保持不变，当前实现以代码、ADR 和专题文档为准。

## 1. Agent-first 目标、路线转向与诚实边界

产品正式命名为 **SEC Filing Verification Agent**，面向 SEC 披露公司监控与财务事实核验。学习主线仍是同一套 AI Agent Runtime/Harness、Tool Use、Memory、Evidence、Knowledge/RAG、durable execution 与 Eval；从 Knowledge Step 4 起，所有新增业务能力、数据集和发布验收都必须服务于 SEC `10-K/10-Q` 系列披露事实核验，不再扩张为通用行业平台。MVP 不代表覆盖所有海外市场或 `20-F/6-K`。

### 1.1 已完成范围冻结

本次转向不回写、降级或删除已经验收的基础能力：

| 范围 | 当前事实 | 本计划处理 |
|---|---|---|
| 身份与工程地基～Memory 与 Evidence | D1、D2、D3、D4 已按各自记录完成；D1-09 外部凭据处置和 Memory 与 Evidence 核心覆盖率债务仍保留 | 历史任务、提交、CI、DoD 和限制原文保留，不因业务转向重算 |
| Knowledge Step 1～3 | 私有上传、版本化解析资产、双索引写入/删除对账/Workbench 已合入 `main` | PR #9 与分支/PR/main CI 关闭其冻结验收，D5-01～D5-07 为 `complete`；不据此关闭 Step 4～5 的浏览器 DoD |
| Knowledge Step 4 | 固定 SEC fixture、Dense `knowledge_search`、typed calculator、filing/calculation Evidence 与 F0～F2 合同对照已合入 `main` | D5-08 为 `implemented_pending_verification`；缺 ready fixture 的浏览器 Dense/calculation/Evidence 反查，F0/F1 也不表述为 live/model 质量 |
| Knowledge Step 5 | 节点 Checkpoint、HITL、同 Run resume、副作用账本、Workbench 时间线与 L4 recovery eval 已合入 `main` | D5-09 为 `implemented_pending_verification`；缺同一 fixture 的暂停/审批/resume/刷新浏览器旅程，核验与监控 组合恢复门另行保留 |
| SEC 数据源～发布验收 | 实施批次已合入，发布未就绪 | SEC 数据源～发布验收 代码均已合入 `main`；发布验收 PR #16 的 push/PR/main 三层 CI 全绿，D10-03 与 Memory 与 Evidence 核心 90% 覆盖率债务已关闭。2026-09-01 后续收口把 SEC 数据源 `sec-source-v1` 提升为 24/24 并完成 SEC identity live smoke；这仍不关闭 财务检索与计算 Recall@5/Citation、核验与监控 专用浏览器/故障、系统评测 common-case/offline/live model/Runtime binding、D5-08/D5-09 浏览器 DoD、D1-09 外部凭据处置、真实大体积 bulk、正式恢复与 owner acceptance 发布硬门 |

Knowledge 前三步是后续 Filing RAG 的通用底座，不建立第二套“金融上传/解析/索引”链路。已有行业、政策、招投标、股票和 Text2SQL 实现作为已完成的 Runtime/Tool/Evidence 学习证据保留，但它们不再是新业务范围，也不作为 SEC Agent 能力的替代证据。

### 1.2 目标产品与发布边界

目标版本调整为：

```text
v0.2.0-sec-disclosure-verifier
```

MVP 只覆盖：

- 官方 SEC EDGAR 数据；
- `10-K`、`10-Q`、`10-K/A`，并为 `10-Q/A` 保留同一版本合同；
- 公司/CIK 解析、filing/accession/期间锁定、XBRL 结构化事实、Filing 文本/表格检索、确定性计算、跨期/修订差异和可定位引用；
- 中文提问与中文核验报告，证据保留 SEC 官方英文原文和稳定定位；
- 可选披露监控，只有创建/修改订阅等写操作进入持久 HITL 审批。

明确不做：实时行情、股价预测、估值模型、目标价、荐股、组合建议、自动交易、税务或审计意见。产品输出是可复核的披露事实和证据包，不是投资建议。

核心用户路径为：

```text
中文问题或待核验陈述
→ 明确公司、CIK、form、报告期间和 as_of
→ 锁定 accession、原始 filing 快照与 XBRL context
→ 结构化事实工具 + Filing Hybrid RAG
→ typed calculator 重算并核对单位/scale/期间/修订
→ Evidence/Claim verifier → 最多一次 bounded revise
→ verified / partial / conflict / insufficient_evidence
→ 可选人工批准创建披露监控 → 新 filing 到达后生成 diff/case
```

完整工程范围 结束时，学习者除能解释 Runtime、Harness、Context、Memory、Checkpoint、Trace、Artifact 与 Eval 外，还必须能用运行证据回答：

1. 为什么 XBRL 结构化事实和 Filing RAG 必须是两条互相核对的证据通道。
2. 如何证明一次回答使用了正确公司、accession、form、fiscal period、unit、scale 和 `as_of`，并且没有未来信息泄漏。
3. 为什么计算必须进入版本化 typed calculator，而不能让模型在自然语言中不可审计地心算。
4. Agent loop 相对纯 Hybrid RAG 在复杂检索、计算、修订和冲突场景是否有净收益。
5. 哪些结论由确定性规则、公开 benchmark、人工抽样或 live evaluation 支持，以及每类证据不能证明什么。

身份与 Workspace、Job/Outbox、私有文件、Provider 端口、CI 等继续作为必要底座。Agent 核心能力必须使用正式数据、同一 Runtime、可恢复状态和可重复评测，不得用页面、Mock、冻结 replay、单次漂亮答案或名义上的多 Agent 冒充完成。

若一个“Day”无法在当天通过门禁，则顺延该 Day；不得删减场景、放宽预算、绕过 Harness、伪造工具结果或把 live/model 质量与本地确定性合同混报。D1-09 外部凭据处置仍阻断最终发布标签；Memory 与 Evidence 核心 Domain/Application/Research workflow 覆盖率债务仍须在最终发布门禁前从 85% 补到 90%。

## 2. 从参考项目中提炼 Agent 能力栈

| 能力 | 借鉴方向 | 新项目处理 | 当前计划目标 |
|---|---|---|---|
| Agent Runtime 与 Harness | 两项目均不足 | 建立统一 Run/Step/Event、Context、Tool、Budget、Checkpoint、Trace 与评测入口 | 普通聊天、工具调用和 Research 共用同一执行语义 |
| Agent Tool 与 Skill | 参考项目 | typed Tool Registry/Executor、Harness profile、Observation、Artifact、审批与预算 | Web、行业、Text2SQL 和知识工具共用正式 Tool loop |
| Short/Long-term Memory | 参考项目 | Thread state、用户可控长期记忆、写入/召回/遗忘策略和 Memory Eval | 聊天与 Research 均能可解释地使用、更新和删除 Memory |
| Agent Knowledge 与 Hybrid RAG | `my_work_project` | 文件/知识作为 Context Source 与 Tool；Dense + BM25 + RRF + rerank + Context Compiler | 文本、图片、表格形成可评测、可引用的 Agent Context 闭环 |
| SEC 披露数据底座 | SEC EDGAR 官方接口与原始 filing | CIK/issuer 解析、submissions、原始 filing/iXBRL 快照、XBRL facts、accession 与 point-in-time 版本锁定 | 官方披露可重放、可追溯，不依赖动态网页搜索作为事实真相 |
| 财务事实工具与核验 | 新项目领域能力 | typed SEC tools、typed calculator、期间/单位/scale/修订核对、Filing RAG 与结构化事实交叉验证 | 每个数字、公式和变化都能反查输入事实、计算和 filing Evidence |
| Evidence、Claim 与多模态 Artifact | 两项目方向整合 | 统一 locator、来源、支持/反驳关系和引用校验 | Research 与普通回答共享 Evidence 语义 |
| Deep Research 工作流 | 参考项目 | 从 Tool Use 演进为可恢复状态图；从 Knowledge Step 4 起以 SEC scope、filing 选择、事实分解、核对与最多一次 revise 为正式业务图 | 一个有边界、可评测、可恢复的财务核验闭环 |
| SSE、取消、Checkpoint、恢复 | 参考项目 | 版本化事件、持久状态、幂等副作用 | 完成基本闭环 |
| Agent Trace、Harness 与 Eval | 两项目均不足 | 通用场景与 SEC 专项场景分层；Fake/Replay、故障注入、公开 benchmark adapter、时点泄漏和轨迹评分 | 每次 Runtime/Prompt/Tool 变化都有回归证据，且不把通用 Runtime 绿灯冒充金融能力 |
| PDF 版面、OCR、图片、表格 | `my_work_project` | 解析器端口 + 多模态资产模型 | 为 Agent Knowledge/RAG 提供代表性真实数据 |
| Web 搜索与行业数据 | 参考项目 | Provider 端口、来源证据与代表性真实 Adapter | 为 Tool Use/Research 提供真实外部 Observation |
| 数据库浏览与 Text2SQL | 参考项目 | 只读账号、AST 校验、预算和受校验图表 | 为 Agent 提供一个真实结构化数据 Tool |
| 会话、附件、身份与 Workspace | 参考项目 | 提供 Thread、可信 Runtime Context 和租户范围 | 足以承载核心 Agent 用户旅程 |
| PostgreSQL、Milvus、Elasticsearch、MinIO | `my_work_project` | 分别承担业务事实、向量/关键词索引和 Artifact 存储 | 支撑 Agent 状态、Knowledge/RAG 与可靠恢复 |
| 披露监控与 Case | 新项目领域能力 | 复用 Schedule/Job/Outbox、filing watermark、幂等 diff、持久审批与 Case | 新 filing 到达后可恢复地产生变化证据，不重复通知或写操作 |
| CI、迁移、任务可靠性与可观测 | 两项目均不足 | 作为 Agent 开发的支撑底座，不扩张为业务主角 | 足以稳定、可重复运行核心场景 |

支撑边界：

- 两个旧项目都只作为只读参考。借鉴职责、交互与通用架构思想，不直接复制参考项目受版权保护的源码、文案、图片或素材。
- 引入 DeepDoc/RAGFlow 等第三方代码前，先核对许可证、保留 NOTICE 和修改说明；不确定时采用端口适配或独立实现。
- 旧仓库暴露的凭据处置、许可证、身份与租户隔离继续作为 身份与工程地基 门禁，但详细做法由专项审计和 ADR 管理，不在后续 Agent 学习中反复展开。

## 3. 不可静默偏离的技术决策

### 3.1 总体形态

采用“模块化单体 + 统一 Agent Runtime/Harness + 独立 Celery Worker + Celery Beat Scheduler”，完整工程范围 不拆微服务：

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
       Model Provider Adapter         knowledge/sec/xbrl/filing/calculator
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
- **Sandbox**：未来执行代码、Shell 或文件写操作时的隔离环境。当前主线不开放通用代码/Shell 工具，因此不为展示概念而造一个假 Sandbox。

理由：Agent Runtime 解决“这次智能任务如何推进”，Celery 解决“它在哪里可靠运行”，Application Service 负责业务事实和权限，三者必须分层。PostgreSQL 保持唯一可信状态；Redis/Milvus/ES 是可恢复执行或派生层。长时 Research 由 Job 启动，但其 Plan、Action、Observation、Checkpoint 和终止原因属于 Agent Runtime。

### 3.2 技术栈

| 层 | 固定选择 |
|---|---|
| 运行时 | Python 3.13.x；Node.js 24 LTS；身份与工程地基 把精确补丁版本写入 `.python-version`、`.nvmrc` |
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
| Agent Knowledge/RAG | MinIO/Parser → Milvus Dense + Elasticsearch BM25 → RRF/rerank → Context Compiler/Evidence；SEC 场景另以 XBRL typed facts 作为结构化并行通道 |
| SEC 数据接入 | 官方 EDGAR submissions/XBRL API + 原始 filing/iXBRL；PostgreSQL 保存身份、版本和 lineage，MinIO 保存不可变原件快照 |
| 模型接入 | Provider-neutral Port；至少一个 OpenAI-compatible Adapter；供应商 SDK 不进入 Runtime 核心 |
| Agent Runtime | 项目内正式执行层；普通回答、工具循环和 Research 共享 Run/Step/Event/Budget 语义 |
| Agent Harness | 项目内组合与评测层；Tool/Skill、Context、Approval、Artifact、Fake/Replay/Scorer |
| Research workflow | LangGraph 只用于需要持久图状态的 Deep Research，不渗透普通 CRUD/简单聊天 |
| Text2SQL | `sqlglot` AST 校验 + 数据源只读账户 + 查询预算 |
| API 契约 | `/api/v1` OpenAPI 为唯一契约源，生成 TypeScript 类型 |
| 测试 | pytest、pytest-asyncio、HTTPX、Testcontainers、Vitest、RTL、MSW、Playwright |
| 可观测 | JSON 日志、OpenTelemetry、Prometheus；Grafana/Tempo/Loki 为可选 profile |
| 部署 | Docker Compose 单机部署；不引入 Kubernetes |

版本规则：主技术选型不可随意替换；具体依赖精确版本以 身份与工程地基 兼容性试验后生成的 lockfile 为准。依赖文件禁止大量无上限 `>=`，Compose 镜像使用明确 tag，发布阶段可进一步锁 digest。

### 3.3 十四条 Agent-first 核心原则

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
11. SEC 原始 filing 是外部披露核验依据；PostgreSQL 是系统业务事实源。必须保存 CIK、accession、form、报告期间、抓取时间、官方 URL 与内容哈希，不能把动态网页摘要当作 filing 快照。
12. 财务事实先锁 company/CIK、accession、`as_of`、period、unit、scale、concept/context，再进入计算或比较；模型不得自行把 fiscal period、calendar period、instant/duration 或修订前后事实混在一起。
13. 所有派生数值必须由版本化 typed calculator 执行并保存输入 Evidence、公式、舍入和结果；自然语言模型只负责选择/解释，不充当不可审计计算器。
14. 固定 replay 证明 Runtime/合同，公开 benchmark 证明特定覆盖面，live evaluation 证明固定模型和实时工具的当前能力；三者必须分报，任何 LLM judge 都不能成为唯一硬门禁。

## 4. 目标 Monorepo 与模块边界

```text
sec-filing-verification-agent/
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
│  │  │  │  ├─ disclosures/          # SEC filer/filing/snapshot/XBRL facts
│  │  │  │  ├─ financial_verification/ # scope/calculation/reconciliation/monitor
│  │  │  │  ├─ data_explorer/
│  │  │  │  ├─ jobs/
│  │  │  │  └─ evaluation/
│  │  │  ├─ ports/                # llm/parser/embed/vector/lexical/object/web/sec/data
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
│  ├─ datasets/                     # 通用、FinQA/TAT-QA adapter、SEC temporal manifests
│  ├─ scenarios/                    # Harness 场景、冻结响应与故障配置
│  └─ reports/                      # 轨迹、结果、Evidence、成本/延迟对比
├─ infra/
│  ├─ compose/
│  ├─ docker/
│  └─ observability/
├─ docs/
│  ├─ adr/
│  ├─ architecture/
│  ├─ engineering-records/
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
disclosures → jobs / files / evidence / SEC ports；不依赖具体 HTTP SDK
financial_verification → disclosures / retrieval / tools / evidence；不实现第二套 Agent loop
evaluation → agent_harness；只读观察 Runtime / conversation / retrieval / research / financial_verification
```

`research` 不能直接调用 Provider SDK 或具体 Milvus/ES/MinIO/SEC HTTP 客户端；`disclosures` 通过 SEC Port 复用统一受控 egress、缓存、限流与快照合同；`financial_verification` 只提供领域服务与 typed Tool，不复制 Retriever、ToolExecutor 或 Runtime。`conversation` 不能绕开 Runtime 调模型；`evaluation` 不得改变线上回答路径。Harness 的 Replay 只重放冻结的外部边界结果，不宣称模型本身确定。每个 HTTP 请求、Celery task 和并发协程各自拥有独立 SQLAlchemy Session，不能共享 AsyncSession。

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
- `agent_events`：run_id、sequence、event_type、schema_version、payload、occurred_at；作为持久业务事件与恢复事实源。Token delta 可以只进入带 TTL 的短期流，但关键进度、快照、引用和终态必须持久化。
- `tool_calls`：step_id、tool/schema version、sanitized_arguments_hash、approval、status、result/evidence refs；不保存 Secret。
- `agent_checkpoints`：run_id、revision、state_schema_version、state_json、resume metadata。
- `run_artifacts`：run_id、kind(report/table/chart/file/evidence_set)、object/resource reference、content hash、version；普通 final message 不重复写成 Artifact。
- `evidence`：kind、title、canonical_url、snapshot_file_id、locator、excerpt、content_hash、metadata。
- `message_citations`：message_id、evidence_id、ordinal、claim。

`agent_runs` 是普通回答、工具循环与 Research 的统一执行事实，`research_runs` 是它的领域扩展，不再建立第二套互不兼容的模型调用历史。`locator` 统一表达 PDF 页码/bbox、Chunk、网页段落、SQL 表/行范围、新闻或政策 ID，使知识库、网页、数据库和行业资讯共用 Evidence。

### 5.2 Runtime 状态、记忆、研究与评测

- `thread_memory_states`：thread_id、summary、message_refs、compaction_revision、freshness、schema_version；承载 Short-term Memory 投影，不替代 Run Checkpoint。
- `memories`：user_id、scope、kind、current_revision_id、confidence、status、expires_at；作为可查询的 Long-term Memory 当前投影。
- `memory_revisions`：memory_id、version、content、provenance/source_ref、write_reason、policy_decision、editor、validity/status；保留修改、冲突、停用和删除的可审计历史。
- `context_manifests`：run_id、step_id、source_kind/id/version、included、decision_reason、token_count、budget_snapshot；记录哪些 Memory、Knowledge、Observation/Evidence 和 Artifact 实际进入 Context。
- `research_runs`：agent_run_id、query、research brief、phase、coverage target、max_iterations、cancel_requested_at。
- `research_plans`：run_id、revision、questions、dependencies、status；计划变化显式版本化。
- `research_reports`：run_id、version、markdown、quality_score。
- `research_claims`：statement、confidence、verification_status。
- `claim_evidence`：claim_id、evidence_id、support_type。
- `graph_nodes`、`graph_edges`：从 claim/evidence/company/topic 派生，第一周仍存在 PostgreSQL，不引入 Neo4j。
- `evaluation_cases`：dataset/version、input、expected behavior、available tools/toolset version、budget、deterministic fixture refs。
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
- `search_index_records`：可重建索引中的确定性外部 ID、vector/lexical index version、Embedding Provider/model/dimension/normalization、状态和错误。
- `jobs`、`job_events`、`outbox_events`：后台任务、lease/fencing、事件与可靠投递。
- `schedules`、`schedule_occurrences`：数据库时间、时区、misfire 和幂等 occurrence。

Knowledge Base、Document/Chunk/Asset 与检索记录是 Agent Knowledge/RAG 的核心事实和 Evidence 来源；`file_objects`、Job/Outbox 为它们提供存储与可恢复执行支撑。MinIO/Milvus/ES 的访问与一致性细节由各 Adapter/Runbook 维护，不进入 Runtime 领域模型。

### 5.5 行业数据、Text2SQL 与图表

本节记录 工具执行 已完成的通用行业与 SQL 学习切片；从 Knowledge Step 4 起不再扩张其 Provider 或页面范围。

- `data_sources`、`collection_runs`、`source_items`：公共来源、外部 ID、URL、发布时间、采集时间、内容哈希。
- `news_items`、`policy_items`、`bidding_items`、`market_snapshots`：领域特有字段，避免把所有内容塞进无约束 JSON。
- `companies`、`industries`、`metric_observations`。
- `data_connections`：加密凭据引用、allowlisted schemas/tables、状态。
- `query_runs`：问题、generated_sql、validated_sql、状态、行数、结果对象、错误。
- `chart_specs`：query_run_id、chart_type、经过 Schema 校验的 ECharts option。

### 5.6 SEC 披露、财务计算与监控

- `sec_filers`：CIK、规范名称、ticker/exchange 映射、身份状态和官方来源版本；ticker 不是稳定主键。
- `sec_filer_aliases`：历史名称、ticker、匹配类型、有效时间和解析置信度；歧义必须进入澄清，不能静默猜公司。
- `sec_filings`：filer、accession、form、filing date、accepted at、report date、`public_available_at`、可见性依据/策略版本、primary document、amendment/base filing 关系、官方 URL 和版本化 current projection；`accession` 在来源语义内唯一，官方 correction/deletion 通过新 source version 推进 projection。
- `sec_filing_documents`：accession 下的官方 document identity、sequence、filename/type 与 canonical URL；document identity 与实际抓取字节分开建模。
- `sec_source_snapshots`：filing document、submissions/companyfacts/companyconcept response、raw iXBRL 或 XBRL instance XML 的 append-only 不可变 MinIO ref、content hash、retrieved at、`source_version_available_at`/有效区间/依据、Adapter/source version 和异常状态；更新创建新快照，不覆盖已用于回答的原件。
- `workspace_sec_imports`：Workspace 对 canonical filing/source snapshot 的授权绑定、导入状态和 Knowledge DocumentVersion；`resolve_filer/list_filings` 可在认证 Workspace 内读公共 discovery catalog，facts/text/bytes 读取必须通过 import 绑定重新授权。
- `sec_xbrl_contexts`：entity、period instant/start/end、dimensions、fiscal year/period、frame、来源 snapshot 和 context hash；仅 raw iXBRL/instance XML 承诺精确原始 context。
- `sec_xbrl_facts`：taxonomy/concept、label、value、unit、source-specific nullable decimals/scale/context/dimensions、filed、accession、form、source kind/snapshot 与 fact locator；aggregate locator 与 raw locator 分型，不能补造聚合 API 未提供的原始字段。
- `financial_calculations`：operator/schema version、rounding policy、result/unit、状态和创建 Step；`financial_calculation_inputs` 连接每个输入值与 XBRL/表格/文本 Evidence，禁止只保存最终数字。
- `disclosure_monitors`：workspace、filer、forms、关注事实/章节、watermark、schedule 和 approval policy；`disclosure_cases` 保存一次新 filing/amendment diff、Evidence、状态与去重键。

`AgentRun`、`ResearchBrief`、Context manifest 和 EvalCase 在 SEC profile 下必须记录版本化 `FilingSelectionScope v1`：`as_of`、目标 filer/CIK 候选、允许 forms、报告期间和 amendment policy；选定后再物化 accession-bound `FinancialScope`。现有 Knowledge `FinancialScope v1` 保持 replay 兼容，不能原地扩字段改变旧语义。`latest` 只能是解析后落入 Trace 的显式选择结果，不能作为不可重放的隐式默认值。SEC 是外部披露来源，PostgreSQL 仍是系统业务事实源；MinIO 保存回答时实际使用的不可变原件，Milvus/Elasticsearch 只保存可重建的 filing 文本索引。

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
/workspaces/{workspace_id}/sec/filers/resolve
/workspaces/{workspace_id}/sec/filers/{cik}/filings
/workspaces/{workspace_id}/sec/filing-imports
/workspaces/{workspace_id}/sec/filings/{accession}/documents|facts|sections|diff
/financial-verifications  /financial-verifications/{id}/report|trace
/disclosure-monitors  /disclosure-cases
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

disclosure.filing.discovered|snapshot.saved|xbrl.normalized|index.ready
disclosure.verification.scoped|fact.selected|calculation.completed
disclosure.conflict.detected|report.finalized|monitor.approval_required
disclosure.case.created|failed
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

L6 不是 完整工程范围 硬指标。Planner、Retriever、Analyst、Writer、Verifier 首先是同一个正式状态图中的节点职责；并行检索是受控并发，也不自动等于多 Agent。评测结论完全可以是“当前不需要多 Agent”。

Agent Runtime 的最小接口语义：

```text
AgentRuntime.run(command) -> AsyncIterator[AgentEvent]
ContextCompiler.compile(run, step) -> ModelInput
ToolRegistry.resolve(name, runtime_context) -> TypedTool
ToolExecutor.execute(call, runtime_context) -> ToolResult
ApprovalPolicy.evaluate(call, policy_context) -> allow | deny | interrupt
CheckpointStore.save(run_id, expected_revision, state) -> Checkpoint
CheckpointStore.load(run_id, revision | latest) -> Checkpoint
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

演进映射调整为：Agent Runtime 完成 L0 与 Runtime/Harness v0；工具执行 完成 L1–L2 和 Tool/Harness v1；Memory 与 Evidence 完成可治理 Short/Long-term Memory、Evidence/Claim 与 L3；Knowledge 前三步完成 Knowledge 入库底座，后两步以冻结 SEC filing 夹具完成 Dense 查询、typed calculator 与 durable L4；SEC 数据源 接入官方 EDGAR 与 point-in-time 数据合同；财务检索与计算 完成 XBRL + Filing Hybrid Retrieval 和可审计计算；核验与监控 完成 SEC Evidence-aware L5、最多一次 bounded revise、监控 HITL 与恢复；系统评测 运行专项 benchmark 和消融；发布验收 只做发布收口。L6 多 Agent 仍不是硬指标，只有 系统评测 证明净收益后才允许单独提案。

### 6.5 Harness 内的工具边界

- 数据源使用独立只读账号，并限制 schema/table/column allowlist。
- 使用 sqlglot 解析完整 AST；仅允许安全 SELECT/CTE，并强制只读事务、timeout、最大返回行和扫描预算。
- 图表只接受 Pydantic/JSON Schema 验证过的 ECharts 配置。
- 每个 Tool 声明 schema、capability、WorkspaceScope、timeout、budget、side-effect class 和 approval policy；模型只能请求，Harness profile 提供 surface/policy，Runtime 校验并由 ToolExecutor 执行。
- Web/文件/SQL 返回都按不可信 Observation 处理；只有规范化、授权、记录来源后才能成为 Evidence。
- 通用 SSRF、上传、Cookie、CORS 与对象存储细节不在本计划重复展开，分别由 `docs/tool-security.md`、安全 ADR 和自动化测试持续约束。
- 当前计划不开放通用 Shell/代码执行 Tool；未来若需要，必须先有真正的 Sandbox、网络/文件权限和独立 ADR。
- SEC read Tool 必须由服务端注入当前 `as_of`、allowed forms、WorkspaceScope、请求预算和 SEC client policy；模型只能提交公司/期间等业务参数，不能覆盖官方 host、User-Agent、缓存、rate limit 或 accession 选择约束。
- `finance.calculate@v1` 只接受受控运算符、十进制定点值、unit/scale、rounding policy 和 Evidence refs；禁止执行任意 Python、表达式、SQL 或模型生成代码。
- `monitor.subscribe@v1` 是写 Tool，必须在持久 Approval 后执行，并以 workspace/filer/forms/rule hash 作为幂等边界；查询、计算和 diff 默认只读。

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

### 6.7 Agent Knowledge/RAG 与 SEC 双通道 Context

RAG 是 Agent 的一等 Context 能力，但 SEC 财务事实核验不能退化为“把 filing 切块后聊天”。正式 profile 同时使用两条可独立评分的证据通道：

```text
结构化通道：CIK + accession + concept + context + unit + period
→ SEC XBRL API / raw iXBRL fact → PostgreSQL 规范化 → typed fact Evidence

叙述通道：锁定 accession 的 filing HTML/iXBRL/表格
→ Dense + BM25 → RRF → rerank → PostgreSQL 重新加载
→ section/table/text Evidence

两路 Evidence → unit/scale/period/amendment reconcile
→ Context budget → calculator / model → Claim/Citation verifier
```

结构化通道优先处理标准、可确定定位的财务事实；叙述通道补足 MD&A、Risk Factors、Notes、表格语义、自定义标签和脚注。两者发生冲突时不得按“结构化一定正确”或“文本更新”静默选边，必须保留 accession/context、形成 `conflict` 并说明采用或拒绝某一事实的规则。

SEC `companyfacts/companyconcept` 聚合数据只覆盖其官方合同支持的 XBRL 事实，不代表 custom tag、叙述脚注或原始 filing 全覆盖。`frames` 只可用于候选发现或横截面对照，不能作为精确 fiscal period 比较的最终上下文；最终核验必须锁定 accession 和原始 context。初始 Dense/BM25/RRF/rerank 参数仍是实验值，任何永久参数必须由 SEC 专项数据集报告支持。

### 6.8 SEC Tool surface、point-in-time 与正式 Agent loop

MVP 的 typed Tool surface 固定为：

| Tool | 只读/写 | 主要输出 | 关键拒绝条件 |
|---|---|---|---|
| `sec.resolve_filer@v1` | 只读 | CIK、规范公司名、ticker/alias 候选和匹配依据 | 多候选或低置信时返回 ambiguous，不自动选第一个 |
| `sec.list_filings@v1` | 只读 | 截至 `as_of` 可见的 form/accession/accepted/report date 列表 | cutoff 后 filing、form 不允许或 amendment 关系不明 |
| `sec.get_xbrl_facts@v1` | 只读 | concept/context/unit/period/value/accession/fact locator | 精确 context 不匹配、unit/period 歧义或仅有 cutoff 后事实 |
| `sec.search_filing@v1` | 只读 | 锁定 accession 内的检索候选与 `retrieval_profile_version`；SEC 数据源=`dense-v1`，财务检索与计算=`hybrid-v1` | 未锁 accession、快照未就绪或跨 Workspace/版本 |
| `sec.read_filing_section@v1` | 只读 | section/table/text Evidence 与原始 locator | section/locator 不存在或快照 hash 不匹配 |
| `finance.calculate@v1` | 只读 | operator、输入 Evidence、公式、rounding、result/unit | 任意代码、无来源输入、unit 不兼容或除零 |
| `sec.diff_filings@v1` | 只读 | 两个已锁 accession 的事实/章节变化及 Evidence | 公司/期间不可比、base/amendment 关系不明 |
| `monitor.subscribe@v1` | 写 | 持久 monitor、schedule、watermark 与 audit ref | 未审批、重复订阅、无权限或范围过宽 |

SEC 数据源 只交付前五个 SEC 只读 Tool；`finance.calculate@v1` 的 Knowledge fixture 实现保留但不计入 SEC 数据源 完成声明，正式计算/核对、diff 与 monitor 分别在 财务检索与计算～核验与监控 验收。`sec.search_filing@v1` 保持 typed input/output 向前兼容，用显式 `retrieval_profile_version` 区分候选策略，禁止把 SEC 数据源 Dense 结果写成 Hybrid 证据。

正式执行循环为：

```text
确认问题与输出边界
→ resolve filer/CIK
→ 锁定 as_of、form、report period、accession 与 amendment policy
→ 将待核验陈述拆成原子 Claim、所需事实和公式
→ 并行调用 XBRL facts 与 Filing RAG read tools
→ typed calculator 执行派生计算
→ reconcile company/period/unit/scale/context/source
→ Evidence-aware verifier
→ 最多一次补检索或 revise
→ verified / partial / conflict / insufficient_evidence
→ 可选持久审批后创建 monitor
```

Gold trajectory 不要求模型走唯一精确序列，而是定义 required milestones/tools、allowed/forbidden actions、参数约束、部分顺序、调用/成本预算、stop reason 和最终数据库状态。模型可以用等价顺序完成只读检索，但必须先解析 filer 并锁 accession，才能选择事实、计算和引用；任何未授权写 Tool、cutoff 后信息或无 Evidence 数值都直接失败。

SEC Adapter 只在服务端运行。所有请求使用包含应用标识和联系邮箱的 `User-Agent`，所有进程合计限制在官方 Fair Access 上限以内并预留余量；启用缓存、条件请求、429/5xx 有界退避和 nightly bulk 优先策略。`data.sec.gov` 不支持浏览器 CORS，因此前端不得直连。每个 live 结果必须落原始响应或 filing snapshot、官方 URL、retrieved at、content hash 和 Adapter version，才能进入可重放 Eval。

## 7. 全局 Definition of Done

任何 冻结工程范围 目标从过程状态改为 `complete` 前，都必须逐条评审下列 Definition of Done 的适用性：凡适用项必须全部通过；标记 `N/A` 必须写明与该目标无关的具体理由和复核人，不能用来逃避实现。面向用户的业务能力不得把真实用户旅程、服务端权限、正常/失败/恢复测试或安全检查标为 `N/A`；工程、文档和治理目标可以用等价的开发者/运维旅程与自动化校验替代，只有确实不改变数据库、HTTP/SSE 或运行时行为时，才可把对应 migration、契约或遥测项记为 `N/A`。`thin_slice` 和 `contract_only` 不是质量豁免：

- 存在真实用户旅程，不是孤立接口或空页面。
- 正常、边界、失败、权限和恢复测试齐全。
- 有 Alembic migration、OpenAPI/SSE 契约和兼容策略。
- 有结构化日志、指标、Trace 和稳定错误码。
- 有数据所有权、删除、补偿、备份/恢复策略。
- 完成威胁与隐私检查，日志/前端没有 Secret 或敏感原文泄漏。
- 审核第三方源码、素材、模型、数据源和依赖的许可证与使用条款；需要时保留 NOTICE、归属和修改说明，许可证不明或不兼容时不得引入。
- RAG/Agent/性能相关功能进入可重复评测基线。
- README/Runbook 写清启动、限制、故障和回滚。
- 清除调试输出、硬编码、静默 Mock、临时旁路和重复正式链路。
- 在干净环境或 staging 完成演示。

测试结构建议：60% 领域单元测试、25% 组件/集成测试、10% 契约测试、5% 关键 E2E；RAG/Agent evaluation 作为独立门禁。Flaky test 必须修复，不能长期靠 rerun 掩盖。

### 7.1 Agent 能力追加 Definition of Done

对于 Agent 能力，Agent 核心能力除满足全局 Definition of Done 外，还必须同时满足：

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
- SEC profile 的 company/CIK、accession、form、report period、`as_of`、unit/scale/context 和 amendment 选择全部进入 typed contract、Trace 与 Scorer。
- 每个派生数字由 typed calculator 生成并连接输入 Evidence；fabricated source/accession/number/formula 和 future leakage 在确定性发布集均为 0。
- fixed replay、public benchmark 与 live repeated-run 使用独立 manifest/报告；外部数据集的版本、split、license、checksum 和允许用途可审计。

Agent 测试比例不作为目标本身。优先级是：领域/策略不变量 → Runtime/Tool/Memory/Knowledge-RAG 集成 → Harness 回归 → 少量关键 E2E。Flaky 场景必须定位到数据、并发或模型边界，不能长期靠 rerun 掩盖。

## 8. 防偏航与明确禁止项

- 此处 工程基础 执行门禁指新仓工程与新仓 Secret 边界；参考仓历史凭据处置 D1-09 是 正式发布 发布标签的独立阻断项，不允许被忽略或伪装关闭。
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
- 禁止提交模型权重、运行日志、PID、缓存、工具二进制，以及含敏感原文或未经审阅的大体积原始生成物；允许提交经审阅、去敏且可复现的评测摘要和基线报告。
- 禁止只在前端“删除”资源，禁止硬编码 localhost API，禁止长期公开对象 URL。
- 禁止用硬编码/Mock 数据把未完成的资讯、股票、政策或招投标页面伪装成可用。
- 冻结工程范围 不引入 Kubernetes、微服务、Neo4j 或多套重叠的可观测平台。
- 禁止输出股价预测、估值、目标价、荐股、组合建议、自动交易或把披露事实核验包装为审计/投资意见。
- 禁止让模型心算后直接持久化派生数字，或使用任意代码执行替代 `finance.calculate@v1`。
- 禁止混用 CIK/公司、accession、fiscal/calendar period、instant/duration、unit/scale、base/amendment；任何歧义必须澄清、冲突或拒答。
- 禁止使用 SEC `frames` 的近似对齐做精确公司期间核验，或把 companyfacts 当作 custom tag、脚注和原始 filing 的完整替代。
- 禁止在 point-in-time case 中读取 cutoff 后 filing、最新缓存、未来 companyfacts 或由答案派生的索引。
- 禁止把动态 benchmark、单次 live run、公开 leaderboard 或 LLM judge 总分作为唯一发布证据。

若本机资源不足以同时运行 Milvus、ES 和观测栈，使用 Compose profiles 分时启动，但不得改变目标架构或用低质量实现冒充最终方案。若没有外部/付费 Provider，测试使用 Fake Adapter，正式接口必须返回未配置错误。若解析器兼容性阻塞，先通过 Parser Port 交付基础 PDF Adapter，再单独解决许可和依赖，不能把解析逻辑耦合进业务 Service。

### 8.1 Agent 专项防偏航规则

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

若本机资源不足，使用 Compose profiles 分时启动派生服务，优先保证 Runtime/Harness/PG/Redis 场景可重复；分时启动不等于删减核心能力，进入 Day 5 后仍必须运行 Knowledge/SEC/RAG 的正式 Scenario 与 Eval。没有付费 Provider 时使用 Fake/冻结响应完成快速回归，并让正式 Adapter 明确显示未配置；不得把 Fake 结果作为真实模型质量证据。SEC 官方 read API 不需要模型凭据，但 live smoke 仍必须遵守 Fair Access，并与固定回归分报。

## 9. 权威技术参考

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
- SEC EDGAR API：https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- SEC Accessing EDGAR Data / Fair Access：https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data
- SEC Developer Resources：https://www.sec.gov/about/developer-resources
- FinQA 数据与任务：https://finqasite.github.io/ 、https://github.com/czyssrs/FinQA
- TAT-QA 数据与任务：https://github.com/NExTplusplus/TAT-QA
- FinSearchComp ICLR 2026：https://proceedings.iclr.cc/paper_files/paper/2026/hash/4d42358702dff82e1436550a05ade260-Abstract-Conference.html
- FinanceBench：https://github.com/patronus-ai/financebench
- Berkeley Function Calling Leaderboard：https://gorilla.cs.berkeley.edu/leaderboard
- ToolSandbox：https://machinelearning.apple.com/research/toolsandbox-stateful-conversational-llm-benchmark
- tau-bench：https://github.com/sierra-research/tau-bench
- AgentDojo：https://github.com/ethz-spylab/agentdojo

这些资料用于校准概念、来源合同和评测覆盖，不代表自动引入对应 SDK 或数据。SEC 文档定义 live Adapter 边界；公开 benchmark 各自只覆盖一部分能力并受其许可证/数据权利约束。依赖或数据集进入仓库前仍需 dataset card/ADR/来源复核，项目 Runtime/Harness 必须保持 Provider-neutral，系统也不能把外部 benchmark 的 LLM judge 当成产品事实。
