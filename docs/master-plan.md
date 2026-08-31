# 行业智能平台 Day 1～10 AI Agent 学习与开发主计划：SEC 披露与财务事实核验

> 计划编号：`IIP-MASTER-001`
>
> 版本：`2.2.12`
>
> 制定日期：`2026-07-23`
>
> 修订日期：`2026-08-31`
>
> 状态：SEC 财务事实核验 Agent 执行基线
>
> 项目目录：`D:\industry_intelligence_platform`
>
> 参考项目：`D:\my_work_project`、`D:\industry_information_assistant\industry_information_assistant`

## 0. 本文档的权威性与使用方法

这不是一次性的聊天建议，而是项目 Day 1～Day 10 的执行基线。Day 1～Day 4 及 Day 5 已发生的实现事实属于历史基线；从 Day 5 Step 4 起，后续产品与评测主线转为 SEC 上市公司披露与财务事实核验 Agent。本文档长期纳入版本控制。

以后每次继续开发，人与 AI 都必须先做四件事：

1. 阅读本计划、当前 `docs/feature-matrix.md`、相关 ADR 和上一天的学习日志。
2. 明确当前处于第几天、哪一道验收门禁，以及上一项是否真正通过。
3. 只实现当前纵向切片；不得顺手引入未评审的新框架或第二套链路。
4. 完成后更新测试结果、功能状态、技术债和学习日志，再进入下一项。

决策优先级为：用户最新明确指令 > 经用户确认的 ADR > 本主计划 > 临时实现笔记。任何影响技术栈、数据所有权、安全边界、模块职责或 Day 1～Day 10 范围的变化，都必须：

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

## 1. Agent-first 目标、路线转向与诚实边界

产品从通用“多模态行业智能工作台”收敛为 **SEC 披露公司监控与财务事实核验工作台**。学习主线仍是同一套 AI Agent Runtime/Harness、Tool Use、Memory、Evidence、Knowledge/RAG、durable execution 与 Eval；变化的是从 Day 5 Step 4 起，所有新增业务能力、数据集和发布验收都必须服务于 SEC `10-K/10-Q` 系列披露事实核验，而不再继续扩张通用行业页面。MVP 不代表覆盖所有海外市场或 `20-F/6-K`。

### 1.1 已完成范围冻结

本次转向不回写、降级或删除已经验收的基础能力：

| 范围 | 当前事实 | 本计划处理 |
|---|---|---|
| Day 1～Day 4 | D1、D2、D3、D4 已按各自记录完成；D1-09 外部凭据处置和 Day 4 核心覆盖率债务仍保留 | 历史任务、提交、CI、DoD 和限制原文保留，不因业务转向重算 |
| Day 5 Step 1～3 | 私有上传、版本化解析资产、双索引写入/删除对账/Workbench 已合入 `main` | PR #9 与分支/PR/main CI 关闭其冻结验收，D5-01～D5-07 为 `complete`；不据此关闭 Step 4～5 的浏览器 DoD |
| Day 5 Step 4 | 固定 SEC fixture、Dense `knowledge_search`、typed calculator、filing/calculation Evidence 与 F0～F2 合同对照已合入 `main` | D5-08 为 `implemented_pending_verification`；缺 ready fixture 的浏览器 Dense/calculation/Evidence 反查，F0/F1 也不表述为 live/model 质量 |
| Day 5 Step 5 | 节点 Checkpoint、HITL、同 Run resume、副作用账本、Workbench 时间线与 L4 recovery eval 已合入 `main` | D5-09 为 `implemented_pending_verification`；缺同一 fixture 的暂停/审批/resume/刷新浏览器旅程，Day 8 组合恢复门另行保留 |
| Day 6～Day 10 | 尚未完成 | Day 6～Day 9 代码均已合入 `main`，最近的 Day 9 PR #15 push/PR/main 三层 CI 全绿；这不关闭能力缺口。Day 6 `sec-source-v1` 仍为 22/24，Day 7 Recall@5/Citation、Day 8 专用浏览器/故障、Day 9 common-case/offline/live/Runtime binding、D5-08/D5-09 浏览器 DoD、Day 4 核心 90% 与 D1-09 外部凭据处置均进入 Day 10 发布硬门 |

Day 5 前三步是后续 Filing RAG 的通用底座，不建立第二套“金融上传/解析/索引”链路。已有行业、政策、招投标、股票和 Text2SQL 实现作为已完成的 Runtime/Tool/Evidence 学习证据保留，但它们不再是新业务范围，也不作为 SEC Agent 能力的替代证据。

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

Day 1～Day 10 结束时，学习者除能解释 Runtime、Harness、Context、Memory、Checkpoint、Trace、Artifact 与 Eval 外，还必须能用运行证据回答：

1. 为什么 XBRL 结构化事实和 Filing RAG 必须是两条互相核对的证据通道。
2. 如何证明一次回答使用了正确公司、accession、form、fiscal period、unit、scale 和 `as_of`，并且没有未来信息泄漏。
3. 为什么计算必须进入版本化 typed calculator，而不能让模型在自然语言中不可审计地心算。
4. Agent loop 相对纯 Hybrid RAG 在复杂检索、计算、修订和冲突场景是否有净收益。
5. 哪些结论由确定性规则、公开 benchmark、人工抽样或 live evaluation 支持，以及每类证据不能证明什么。

身份与 Workspace、Job/Outbox、私有文件、Provider 端口、CI 等继续作为必要底座。Agent 核心能力必须使用正式数据、同一 Runtime、可恢复状态和可重复评测，不得用页面、Mock、冻结 replay、单次漂亮答案或名义上的多 Agent 冒充完成。

若一个“Day”无法在当天通过门禁，则顺延该 Day；不得删减场景、放宽预算、绕过 Harness、伪造工具结果或把 live/model 质量与本地确定性合同混报。D1-09 外部凭据处置仍阻断最终发布标签；Day 4 核心 Domain/Application/Research workflow 覆盖率债务仍须在最终发布门禁前从 85% 补到 90%。

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
| Deep Research 工作流 | 参考项目 | 从 Tool Use 演进为可恢复状态图；从 Day 5 Step 4 起以 SEC scope、filing 选择、事实分解、核对与最多一次 revise 为正式业务图 | 一个有边界、可评测、可恢复的财务核验闭环 |
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
- 旧仓库暴露的凭据处置、许可证、身份与租户隔离继续作为 Day 1 门禁，但详细做法由专项审计和 ADR 管理，不在后续 Agent 学习中反复展开。

## 3. 不可静默偏离的技术决策

### 3.1 总体形态

采用“模块化单体 + 统一 Agent Runtime/Harness + 独立 Celery Worker + Celery Beat Scheduler”，Day 1～Day 10 不拆微服务：

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
| 部署 | Docker Compose 单机学习/预发布；第一周不引入 Kubernetes |

版本规则：主技术选型不可随意替换；具体依赖精确版本以 Day 1 兼容性试验后生成的 lockfile 为准。依赖文件禁止大量无上限 `>=`，Compose 镜像使用明确 tag，发布阶段可进一步锁 digest。

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

本节记录 Day 3 已完成的通用行业与 SQL 学习切片；从 Day 5 Step 4 起不再扩张其 Provider 或页面范围。

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

`AgentRun`、`ResearchBrief`、Context manifest 和 EvalCase 在 SEC profile 下必须记录版本化 `FilingSelectionScope v1`：`as_of`、目标 filer/CIK 候选、允许 forms、报告期间和 amendment policy；选定后再物化 accession-bound `FinancialScope`。现有 Day 5 `FinancialScope v1` 保持 replay 兼容，不能原地扩字段改变旧语义。`latest` 只能是解析后落入 Trace 的显式选择结果，不能作为不可重放的隐式默认值。SEC 是外部披露来源，PostgreSQL 仍是系统业务事实源；MinIO 保存回答时实际使用的不可变原件，Milvus/Elasticsearch 只保存可重建的 filing 文本索引。

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

L6 不是 Day 1～Day 10 硬指标。Planner、Retriever、Analyst、Writer、Verifier 首先是同一个正式状态图中的节点职责；并行检索是受控并发，也不自动等于多 Agent。评测结论完全可以是“当前不需要多 Agent”。

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

演进映射调整为：Day 2 完成 L0 与 Runtime/Harness v0；Day 3 完成 L1–L2 和 Tool/Harness v1；Day 4 完成可治理 Short/Long-term Memory、Evidence/Claim 与 L3；Day 5 前三步完成 Knowledge 入库底座，后两步以冻结 SEC filing 夹具完成 Dense 查询、typed calculator 与 durable L4；Day 6 接入官方 EDGAR 与 point-in-time 数据合同；Day 7 完成 XBRL + Filing Hybrid Retrieval 和可审计计算；Day 8 完成 SEC Evidence-aware L5、最多一次 bounded revise、监控 HITL 与恢复；Day 9 运行专项 benchmark 和消融；Day 10 只做发布收口。L6 多 Agent 仍不是硬指标，只有 Day 9 证明净收益后才允许单独提案。

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
| `sec.search_filing@v1` | 只读 | 锁定 accession 内的检索候选与 `retrieval_profile_version`；Day 6=`dense-v1`，Day 7=`hybrid-v1` | 未锁 accession、快照未就绪或跨 Workspace/版本 |
| `sec.read_filing_section@v1` | 只读 | section/table/text Evidence 与原始 locator | section/locator 不存在或快照 hash 不匹配 |
| `finance.calculate@v1` | 只读 | operator、输入 Evidence、公式、rounding、result/unit | 任意代码、无来源输入、unit 不兼容或除零 |
| `sec.diff_filings@v1` | 只读 | 两个已锁 accession 的事实/章节变化及 Evidence | 公司/期间不可比、base/amendment 关系不明 |
| `monitor.subscribe@v1` | 写 | 持久 monitor、schedule、watermark 与 audit ref | 未审批、重复订阅、无权限或范围过宽 |

Day 6 只交付前五个 SEC 只读 Tool；`finance.calculate@v1` 的 Day 5 fixture 实现保留但不计入 Day 6 完成声明，正式计算/核对、diff 与 monitor 分别在 Day 7～Day 8 验收。`sec.search_filing@v1` 保持 typed input/output 向前兼容，用显式 `retrieval_profile_version` 区分候选策略，禁止把 Day 6 Dense 结果写成 Hybrid 证据。

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

## 7. 每日固定学习方式

每天使用“假设 → 场景 → 实现 → Trace → Scorer → 结论”的 Agent 学习循环：

1. 45～60 分钟：用自己的话解释当天 Runtime/Harness 概念，并写一个可证伪假设，例如“加入 plan 会提升多源覆盖，但增加步骤与成本”。
2. 30～45 分钟：先写 Harness scenario，冻结输入、可用工具、预算、预期终止原因和评分规则。
3. 3～4 小时：围绕当天唯一主能力推进一个可验收纵向切片；契约、测试与学习型可视化可以协同实现，但不得在同一切片中重写多个执行语义层。
4. 60～90 分钟：查看完整 Trace，做正常、失败、取消、预算耗尽、恢复和重复副作用测试。
5. 60 分钟：运行确定性规则与小型数据集，比较结果、轨迹、Evidence、Token、费用和延迟；LLM-as-judge 只能作为补充。
6. 30 分钟：记录“保留/回退/继续实验”的结论，更新 feature matrix、ADR 和 `learning-log/day-N.md`。

从 Day 5 Step 4 起，每个切片必须同时预留领域实现、确定性评测和可靠性/安全证据，不能先写完整功能再到 Day 9 临时补题。学习不是照抄 Agent 框架示例，也不是通过增加 Agent 数量制造进展。每个关键增量必须先说清输入、State 变化、Event、终止条件、失败恢复、point-in-time 边界与评分方式，再编码。

每天至少保存三类证据：一条成功 Trace、一条失败或恢复 Trace、一份可比较 Eval 报告。只展示最终答案、UI 动画或一次偶然成功不能标记完成。

Day 4 已冻结的 50 条通用 Scenario 继续证明 Runtime/Memory/Evidence/L3 回归，但不能证明金融能力。新增数据集独立版本化：Day 5 建 `sec-fixture-v1`；Day 6 建 `sec-source-v1`；Day 7 建 `sec-tool-v1`；Day 8 建 `sec-verification-v1`；Day 9 冻结 `sec-temporal-v1`、中英配对集和公开 benchmark manifests；Day 10 只补跑发生版本变化的配置。固定 CI quick suite、离线 release suite 与定时 live suite 分开报告。复杂前端可以按 typed contract 并行实现，但页面、真实数据链路和 Workbench 反查仍属于正式交付，不能用 Mock 可视化代替理解。

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
7. 完成注册、登录、`me`、修改密码、Refresh 轮换、Logout；改密验证当前密码并在同一事务撤销全部旧 Session；注册时创建默认 Workspace 与 owner membership，按 ADR 0006 落实四角色动作矩阵、自提权禁止和最后一个 owner 保护。
8. 前端完成登录/注册、用户资料、修改密码、Auth Guard、基础导航、统一 API Client 和 OpenAPI 类型生成。
9. 建立 CI 快速通道：格式、lint、类型、单测、迁移 fresh upgrade、前端 build、Gitleaks、依赖扫描。
10. 建立可测试的异步底座：API/Beat 事务写 Job + Outbox、独立 Dispatcher、Redis AOF、late ACK/worker-lost 配置、Job lease/heartbeat/fencing，以及“已发布但未 started”和 hard timeout 的独立对账重投。

### 测试

- 注册、重复邮箱、错误密码、过期/伪造 Token、Refresh/CSRF 同步轮换、首次响应丢失后重发同一 successor、grace 内外重放、same-site Cookie 与 Logout；修改密码后旧密码及全部旧 Access/Refresh Session 立即失效，失败/并发不产生部分状态。
- 未登录和用户 A 访问用户 B Workspace 的负向测试；owner/admin/member/viewer 动作矩阵、自提权、admin 越权和并发移除最后 owner 测试。
- 全新空库执行 `alembic upgrade head`；禁止用 `create_all` 建表。
- Playwright 完成“注册 → 登录 → 进入首页 → 修改密码 → 旧会话失败 → 新密码重新登录 → Logout”。
- `/ready` 在数据库或 Redis 关闭时失败，`/live` 仍按进程状态响应。
- Dispatcher 发布前后崩溃、Redis 接受后丢消息、Broker 断线、重复 Worker、soft/hard timeout 和过期 lease 都能被对账恢复，并只产生一个业务结果。

### 当日产物

`docs/product-scope.md`、`docs/architecture.md`、6 份 ADR、`docs/security/credential-exposure-audit.md`、`.env.example`、第一版 OpenAPI、CI、可启动的 Web/API/Outbox Dispatcher/Worker/Beat/基础设施、`learning-log/day-1.md`。

### 验收门禁

- 新 clone 按 README 能启动；仓库、历史和前端构建产物没有有效密钥。
- 身份 E2E 和跨 Workspace 负向测试通过。
- 数据库只由 Alembic 创建；依赖版本进入 lockfile。
- 门禁失败时不得进入 Day 2。

### 复盘题

为什么每张业务表都要 `workspace_id`？为什么 Refresh Token 不应放 LocalStorage？为什么 Milvus 不是事实源？今天有没有为了赶进度加入特殊分支？

## 9. Day 2：Agent Runtime v0、基础 Harness 与流式直接回答

### 学习主题

- 模型调用、Agent loop、Runtime、Harness、Workflow 与 Worker Runtime 的职责边界。
- Run、Step、Event、State、Checkpoint、Trace、final output 与 Artifact 的生命周期。
- Provider-neutral Port、确定性 Fake、Scenario/EvalCase 与版本化执行契约。
- LLM Context 与 Runtime Context；结构化输出、usage、stop reason 与流式恢复。

### 实现任务

1. 冻结 Provider-neutral、Runtime/Harness 与 Application Service/Celery/LangGraph 的依赖方向，更新 Agent 架构 ADR 和 ADR 0005；Day 1 的身份/Workspace 提供可信 Runtime Context，Job/Outbox/Celery 只承载执行。
2. 定义版本化 `AgentRun`、`AgentStep`、`AgentEvent`、`RunState`、`RunArtifact` 与 `RunBudget`；冻结 sequence、唯一终态、stop reason、预算、引用和 optimistic revision 不变量。
3. 定义 `ModelProvider`、`TrajectoryRecorder`、`CheckpointStore`、`ToolExecutor` 与 `AgentRuntime` Port；实现通用 Checkpoint envelope、schema version、optimistic revision/CAS 和不兼容版本拒绝，Day 5 再完成 LangGraph state 映射、interrupt/resume 与真实恢复。
4. 冻结 `Scenario/EvalCase v1`：输入、runtime/harness/model/prompt/context version、available tools/toolset version（L0 可为空）、budget、expected stop reason、scorers、trace/artifact refs 与人工备注；建立 Fake Model 和最小 Harness CLI，生产与 Harness 调用同一 Runtime。
5. 实现 OpenAI-compatible `ModelProvider.stream/complete` Adapter 与 L0 `DirectAnswerProfile`；统一 usage、费用、超时和 Provider error，明确一次无工具模型调用只是 baseline model run。
6. 实现 `ContextCompiler v0`：system instructions、用户问题、会话摘要和可信 Runtime Context 分层；持久化 context manifest 与版本，不保存 Provider Secret。
7. 实现结构化 final output 和 `agent.*` SSE：sequence、Last-Event-ID、心跳、snapshot、取消、背压与唯一终态；断线只恢复已提交 Event，不重复当前 Model Step。
8. 将 Conversation/Turn、AgentRun、final Message/可选 Artifact 与 Day 1 的 Job/Outbox 建立正式关联；当天证明原子创建、可靠投递、取消和失败后用户输入不丢，不提前承诺 graph resume。
9. 贯通 Trace ID、Run ID、Step sequence、Context manifest、usage 与稳定错误码；建立可复用的 Agent Learning Workbench，展示 Run/Step 时间线、Context 组成、Token/费用和 stop reason。
10. Harness 覆盖正常、格式错误、timeout、限流、半截响应、客户端断线和取消；形成首批不少于 5 个版本化 Scenario、record/replay 样例和 Trace snapshot。

Day 2 的支撑产品切片同步完成会话新建/列表/详情/消息分页/重命名/删除/自动标题、附件上传/状态/关联/删除和安全 Markdown。文本附件通过统一 File/Parser Port 做有界真实提取，图片执行 MIME/magic bytes、尺寸与 metadata 校验并形成受控的模型输入引用；不支持、伪类型、超限或解析失败必须显式失败，Day 5 复用并扩展同一 Parser/File 链路。每个 Turn 持久化 `none/web/local/both`、行业与 KB 选择；Day 2 正式启用 `none`，`web` 在 Day 3、`local/both` 在 Day 5 接入相应 Tool profile，未就绪模式必须返回稳定 readiness 错误，不能用 Mock 结果冒充可用。复杂聊天页面由独立前端实现工作流按 OpenAPI/SSE 契约并行交付。

### 测试

- Run/Step/Event/State/Budget 不变量、唯一终态、revision 和 Artifact 引用；Checkpoint envelope/CAS、版本兼容与职责边界。
- 固定 Scenario 两次执行的 Event 类型/顺序一致；Fake 只证明边界可重复，不冒充真实模型确定。
- L0 结构化 final、Provider 格式错误/timeout/429/半截响应、取消和稳定 stop reason。
- SSE 非法/重复/缺口/超前游标、断开重连、慢客户端与“不重复 Model Step”。
- Context Compiler 不把 Runtime 依赖、Secret 或其他 Workspace 内容放入 LLM Context。
- 会话、Run、Message、Job/Outbox 原子性以及刷新、删除和最小浏览器旅程。
- 会话 CRUD/分页/自动标题；真实文本/图片 fixture、附件越权/伪类型/超限/解析失败/删除与刷新恢复；四种模式快照/readiness，以及 Markdown XSS/恶意链接。
- Workbench 只读取正式 Event/Trace，不建立 Mock 展示链路，也不展示原始 chain-of-thought。

### 当日产物

Agent Runtime v0、`Scenario/EvalCase v1`、Fake Model、Harness CLI、DirectAnswerProfile、Context Compiler v0、Checkpoint 基础契约、统一 SSE/Trace、完整会话/附件/Markdown 支撑切片、Run/Context Learning Workbench、≥5 条版本化 Scenario 及代表性 Trace snapshot、`docs/agent-runtime.md`、Agent 架构 ADR、`learning-log/day-2.md`。

### 验收门禁

- “新建会话 → 创建 Run → 流式回答 → 停止/重试 → 刷新恢复”全部经过同一 Runtime，Harness 没有第二套 loop。
- 固定 Scenario 的 Event 骨架和 final output 可重复，至少三类 Provider 故障不会被伪装成成功。
- Trace 能解释 Context version、Model Step、usage、stop reason 和 final output/可选 Artifact；断线不重复执行。
- 会话、消息、附件和模式快照在刷新/失败后仍完整；尚未接通的 `web/local/both` 明确失败，不出现假搜索结果。
- 无法解释 Runtime、Harness、Worker、Checkpoint 与 Trace 的职责差异时，不得进入 Day 3。

### 复盘题

为什么 Agent Runtime 不是 Celery Worker？为什么 L0 复用 Runtime 信封却还不是 Agent loop？Runtime Context 为什么不能原样送入模型？Checkpoint、Trace 与 Memory 分别回答什么问题？

## 10. Day 3：Agent Harness v1——有界 Tool Use 与真实能力

### 学习主题

- Function Calling、Action/Observation、Tool Registry/Executor、Plan–Act–Observe。
- Harness profile、Tool/Skill、预算、deadline、审批、Artifact workspace 与轨迹评分。
- `ToolExecutor` 作为 Runtime Port 与 Harness/Tool Adapter 实现之间的依赖反转。
- 真实 Web/行业查询和 Text2SQL 如何成为 typed capability，而不是模型特权。

### 实现任务

1. 建立 `ToolDefinition`、`ToolCall`、`ToolResult/Observation`、`ToolRegistry` 与 `ToolExecutor` Adapter：name/version、typed input/output、capability、scope、timeout、cost class、side-effect class、approval policy；冻结 `ApprovalRequest/Decision` 和副作用幂等键契约。Day 3 只执行基于可信 `policy_context` 的静态 allow/deny，并以 `approval_required` Event/stop reason 结束；持久 interrupt/resume 留到 Day 5。
2. Runtime 实现 L1 单工具与 L2 有界循环；每轮只接受结构化 action，执行前验证，Observation 归一化后再注入下一次模型 Context。
3. 冻结所有停止条件：final、max_steps、deadline、token/cost budget、cancelled、tool_denied、tool_error、no_progress；禁止无限自省。
4. Harness 增加 Fake Tool、参数 matcher、timeout/error/duplicate result 注入和 trajectory scorer；Scenario 数据集累计不少于 10 条，并输出“为什么调用/为什么停止”的报告。
5. 真实接通一个 Web Search/行业来源工具和一个 Text2SQL 链路；知识检索先保留 Tool 契约，Day 5 Step 4 再接正式私有数据与 Dense 查询。
6. Text2SQL 由工具内部完成 schema discovery、只读 AST 校验、预算、表格 Artifact 和受校验 Chart Artifact；模型不能拿到数据库连接或执行任意 SQL。
7. 外部结果统一转为带来源、时间、locator、content hash 的 Observation/EvidenceCandidate；只有 Day 4 的 Evidence Normalizer 才能将其提升为 Evidence。
8. Tool/Skill 的差异写入文档：Tool 是一次 typed capability；Skill 是由 instructions、可用 Tools、Context 策略和输出契约组成的 Harness 配置，不是隐式任意代码。
9. 实现 `ContextCompiler` v1，把 Tool Observation 按来源、长度和预算安全地注入下一步模型输入；不把工具原始大响应无界拼接进 Context。
10. 扩展 Agent Learning Workbench 的 Tool Inspector，展示 Action、参数验证、Observation、预算、拒绝原因与 Artifact；保留行业资讯/政策/招投标/行情、数据库浏览、表格和图表等完整前端交互，所有页面复用正式 Tool/Event 数据与 Day 1 的 Schedule/Job 基线。

通用 SSRF、上传、网络 egress 与页面安全继续由专项合同和全局 Definition of Done 验收；当天学习重点是模型无法绕过 capability、WorkspaceScope、Schema、预算与审批。

### 测试

- Tool 选择、Schema、scope、deadline、budget、approval 与参数越权；Approval 决策和幂等键契约可确定验证，拒绝后模型不能换名字绕过。
- L2 正常完成、无进展、工具超时/失败、预算耗尽、取消和重复 Observation；每条都有稳定 stop reason。
- Text2SQL 破坏性语句拒绝，Web 工具的 SSRF/响应限制由专项合同测试覆盖；主线测试聚焦 Agent 无法绕过工具边界。
- Harness 场景比较 L0、L1、L2 的完成率、步骤、工具正确率、成本和延迟。
- 真实来源有 Evidence-ready 元数据；未配置 Provider 失败而非 Mock 成功。
- Tool Inspector 与复杂业务页面只消费正式 Tool/Event/Artifact，刷新后仍能解释一次调用为何执行、拒绝或停止。

### 当日产物

Agent Harness v1、Tool Registry/Executor、L1/L2 Runtime、Tool Inspector、行业/数据库/图表页面、一个真实 Web/行业 Tool、Text2SQL + 表格/图表 Artifact、≥10 条累计 Scenario、trajectory report、`docs/agent-harness.md`、`learning-log/day-3.md`。

### 验收门禁

- 同一问题能展示 L0 与 L2 的轨迹差异；Agent 正确调用至少两个不同 Tool，并生成可追溯 Artifact。
- max steps、deadline、Token/费用预算和取消真实生效；任何 Run 都有明确终止原因。
- 模型不能绕过 Tool allowlist/Schema/WorkspaceScope；破坏性 SQL 拒绝率 100%。
- Harness 报告包含工具选择/参数有效性、任务结果、步骤、费用和延迟，而不只评分最终文案。

### 复盘题

Tool、Skill、Application Service 和 Harness 各自负责什么？Observation 为什么还不是 Evidence？什么时候继续循环，什么时候应停止？

## 11. Day 4：Agent Memory、Evidence 与 Deep Research L3

> 执行状态（2026-08-22）：Day 4 五个纵向步骤、正式 Trace/Eval/DoD 复核与项目所有者授权收口均已完成。[PR #7](https://github.com/hrw991009/industry-intelligence-platform/pull/7) 已合入 `main`，合并提交 [`c0b854e`](https://github.com/hrw991009/industry-intelligence-platform/commit/c0b854e64ef1966b76cdcc38c41a507959c836cb) 的 [CI 32549438592](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/32549438592) 通过全部 7 个适用 Job，因此 D4-01～D4-07 与步骤 1～5 统一为 `complete`，允许进入 Day 5。核心 Domain/Application/Research workflow 85% 覆盖率是当时登记的 Day 7 前债务；计划 2.0.0 将最终发布收口延至 Day 10，但仍必须达到 90%，它不是永久豁免。详细证据、复盘和限制见 [Day 4 学习日志](learning-log/day-4.md) 与 [能力矩阵](feature-matrix.md)。

### 学习主题

- Short-term/Long-term Memory 的写入、召回、更新、反馈、遗忘与评测，以及它们与 Context、State、Checkpoint、Knowledge 的边界。
- Evidence Ledger、Claim–Evidence、coverage、矛盾与不确定性。
- Research brief、typed state 与 Planner/Retriever/Analyst/Writer 节点职责；节点不自动等于独立 Agent。
- Memory Scorer、Evidence Scorer、Trace、Eval 与 Context manifest 的区别。

### 实现任务

1. 复用 Day 2/3 已保存的 L0/L2 Run、Observation、Context manifest、任务结果、Token、费用和延迟；Scenario 数据集累计不少于 20 条，当天不重新制作基线。
2. 定义 `ShortTermMemoryState`：Thread 消息引用、摘要、compaction revision、freshness 和注入 manifest；短期记忆可以参与 Context，但不能未经决策提升为长期记忆，也不冒充 Checkpoint。
3. 实现 Long-term Memory 写入闭环：explicit/candidate → provenance/scope/confidence → policy/用户确认或编辑 → create/update/merge/reject；不得默认永久保存全部聊天。
4. 实现 Memory 召回与治理：按目标、scope、时效、冲突、敏感度和预算检索；记录实际注入，支持 feedback、停用、修改、过期和删除。
5. 建立独立 Memory Scorer：write accuracy、retrieval precision/utility、污染率、冲突处理、Token 成本和 deletion residual；用户修改/删除后的下一 Run 必须立即生效。
6. 实现问题澄清与 `ResearchBrief`：原始问题、确认范围、排除项、完成标准和预算显式保存，不能让 Planner 静默改题。
7. 实现 L3 Evidence Ledger：Observation/EvidenceCandidate 经授权、规范化、去重和 locator 校验后成为 Evidence；Claim 标注 support/refute/uncertain、coverage 与 conflict，并冻结后续 Verifier 将消费的 typed input 与规则评分口径。
8. 复用 Day 3 Runtime/Harness，在 LangGraph 内实现唯一正式 typed Research L3 graph；节点调用 Runtime/Domain Port，不直接调用 Provider SDK、不复制 tool loop，当天只形成带 Evidence/Claim 的可解释草稿。
9. Harness 增加 Memory 候选/冲突/删除、Research scope、Evidence 缺失/矛盾和预算场景；Memory Scorer 与 Research Scorer 分开报告，也计算 Memory 对研究质量和 Context 成本的影响。
10. 扩展 Agent Learning Workbench：完整展示 Memory 候选、确认、召回、冲突、修改、停用、删除和实际注入；同时展示 Plan–Action–Observation–Evidence–Claim 图、coverage 与不确定项。

### 测试

- L0 → L2 → L3 同题对照；规则 Scorer 与人工抽样能解释质量、成本和延迟变化。
- Short-term 与 Long-term Memory 不混写；候选写入、用户修正、冲突、无关召回、跨 Thread 使用和删除残留均有固定场景。
- Memory 的 provenance、scope、实际 Context 注入和反馈可追踪；删除后下一 Run 不再引用，错误 Memory 不得静默覆盖新事实。
- 每个关键 Claim 有真实 Evidence 或明确 uncertain；Evidence/Research Scorer 必须评价支持度、coverage 与 conflict，不能只评价文风。
- Research brief 不静默改题；Evidence/Claim 的 locator、support/refute/uncertain、coverage 和 conflict 可确定性验证。
- Harness/LangGraph/Runtime 只有一条正式执行链；跨 Workspace Run/Memory/Evidence 不可读。
- Memory Scorer 与 Research Scorer 分开报告，也能评估 Memory 对 Research 质量、Token 和污染率的影响。
- Memory 与 Evidence–Claim 可视化读取正式 manifest/Event，修改或删除后界面和下一 Run 同步变化。

### 当日产物

可治理的 Short/Long-term Memory 闭环、Memory Eval、ResearchBrief、Deep Research L3、Evidence/Claim Ledger、Memory/Context manifest 面板、研究时间线与 Evidence–Claim 图、L0/L2/L3 对照报告、`docs/memory-policy.md`、`docs/research-state-machine.md`、`learning-log/day-4.md`。

### 验收门禁

- 一个问题可依次展示 L0、L2、L3 的结果、Trace 与指标，而非直接展示“多 Agent”。
- Memory 的写入原因、来源、scope、实际注入、用户修改/删除都可追踪；删除后后续 Run 不再引用。
- Tool surface 与策略来自 Harness profile，Tool Call 由 Runtime 校验并经 ToolExecutor 执行；所有关键 Claim 关联 Evidence，scope、coverage 与未确定项可解释。
- Memory 与 Evidence 学习界面能回答“什么被写入、为何召回、什么进入 Context、哪些 Claim 由哪些来源支持”。
- Durable Checkpoint/HITL 属于 Day 5，Verifier 与 bounded revise 属于 Day 8 后续成熟度，不得用当天的普通状态持久化冒充完成。

### 复盘题

为什么多个节点不等于多个 Agent？Short-term Memory、Long-term Memory、State 与 Context 有何区别？Observation 如何成为 Evidence？为什么 L3 还不是完整可恢复研究？

## 12. Day 5：Agent Knowledge 与 Durable Research L4

> 执行状态（2026-08-26）：Day 5 五步已由 [PR #9](https://github.com/hrw991009/industry-intelligence-platform/pull/9) 合入 `main`；分支 push CI `32920879147`、PR CI `32924323618` 与合并提交 `a38d0ae` 的 CI `32924732755` 均通过 7 个适用 Job，D5-01～D5-07 已关闭为 `complete`。但既有记录明确没有 ready SEC fixture 的 Dense/calculation Evidence 浏览器全链，也没有同一 fixture 暂停/审批/resume/刷新旅程，故 D5-08、D5-09 与 Day 5 总门禁保持 `implemented_pending_verification`。项目所有者随后明确要求开始 Day 6 Step 1，只调整该步骤的执行顺序，不关闭或豁免 Day 5 DoD。live SEC、L5、Monitor、后台审批超时扫描和 Day 8 跨刷新/Worker 重启组合门仍未实现。详细事实见 [Day 5 学习日志](learning-log/day-5.md)。

### 学习主题

- 知识库作为 Context Source 与 typed Tool，而不是独立于 Agent 的另一套回答系统。
- Document/Version/Chunk/Asset/Evidence 的可追溯关系；解析与检索配置版本。
- 长入库任务的 stage/checkpoint/idempotency，以及 Agent 面对 not-ready/partial/failed 知识的语义。
- Agent Checkpoint 与入库 stage checkpoint 的共同原则和不同状态语义；Interrupt/Resume、HITL 与幂等副作用。
- 冻结 SEC filing 夹具如何复用通用 Knowledge 底座，并在 live EDGAR 接入前先验证 accession/period/unit、typed calculator 和财务 Evidence lineage。

### 实现任务

1. 完成多知识库、文档管理和私有上传；保留完整创建/编辑/删除、上传校验、短签名 URL 与 Workspace 权限交互，通用文件安全由专项合同验收，不占当天 Agent 学习主线。
2. 上传完成立即创建 Job；解析、OCR/资产抽取、Chunk、Embedding、vector indexing、lexical indexing 和跨存储删除在 Worker 分阶段执行，每阶段可观察、幂等、重试/取消并可由 Reconciler 修复。只有两类索引写入成功才进入 ready；Day 5 Step 4 只启用 Dense 查询，Hybrid Retrieval 在 Day 7 启用。
3. 定义版本化 `DocumentParser -> ParsedDocument`，用数字 PDF、扫描 PDF、含图片/复杂表格 PDF、TXT/Markdown 的代表性 fixture 验证正式 Adapter。
4. Document/Version/Chunk/Asset 保留页码、标题、bbox、content hash、parser/chunker/index version，使 ToolResult 可转为可解析 Evidence，并支持旧版本追溯与 active version 切换。
5. Step 4 在现有 Embedding/index-write 基础上实现唯一 Dense Top-K、`knowledge_search` 与 `KnowledgeContextSource`；首个正式数据包使用经许可审核、固定 accession/hash 的 SEC `10-K/10-Q` fixture。只返回 ready/active 版本和 Evidence-ready filing locator，Runtime/Harness 不感知 Embedding、Milvus 或对象存储客户端。
6. Step 4 定义版本化 `FinancialScope` 与 `finance.calculate@v1`：至少锁 CIK、accession、form、report period、`as_of`、unit/scale；计算器只接受受控 Decimal 运算和 Evidence refs。定义 not_ready、no_result、partial_index、dependency_failed、permission_denied、ambiguous_filer、period_mismatch 的稳定 Observation。
7. Step 4 把 Knowledge/Calculator Tool 加入同一 Research Harness profile，使一个冻结 filing 问题形成“Dense candidate → Evidence → typed calculation → Claim/L3 draft”链路；不得修改 Day 4 Runtime/Tool loop，也不得把 fixture/replay 冒充 live SEC 质量。
8. Step 5 复用 Day 2 Checkpoint envelope/CAS，将带 `FinancialScope` 的 LangGraph state 映射到统一 `AgentRun/Event/Checkpoint`，实现 L4 interrupt/resume、取消和版本映射；Agent Checkpoint 与 ingestion stage checkpoint 分开建模。
9. Step 5 复用 Day 3 Approval/幂等契约，实现持久 `ApprovalRequest/Decision`、副作用账本、allow/deny/timeout、审计和恢复；仅对公司/期间歧义确认或未来写 Tool 建立真实 interrupt，Worker 终止或重复 resume 不重复 Tool Call、计算或 Artifact。
10. 新增 `sec-fixture-v1` 场景与 Workbench：覆盖 accession/period/unit、计算、无答案、索引失败、旧版本、节点 hard stop、重复 resume/decision 和预算；展示 filing locator、计算输入/公式、Checkpoint/HITL 和恢复位置。Day 4 的 50 条通用 Scenario 原样保留，不能用其数量替代新领域覆盖。

### 测试

- 代表性正常/损坏/超限文件与 Parser fixture；更完整上传安全矩阵保留在专项测试。
- Worker 各主要阶段故障、重启、重复投递、取消；不产生重复 Chunk/索引/Artifact。
- Knowledge Tool 的 workspace/version/status 过滤、Evidence locator 与 dependency failure。
- Embedding Provider 的维度/归一化/批处理/超时/版本契约，以及同输入在确定 Fake 下的可重复索引记录。
- Research 不改 Runtime/graph 即可消费 Knowledge/Calculator Tool；知识未就绪、无结果、公司或期间歧义时不伪造 Evidence。
- 每个 SEC fixture 固定 CIK/accession/form/report period/`as_of`/hash；错误 accession、cutoff 后 fixture、unit/scale 不兼容和无 Evidence 计算全部拒绝。
- calculator 的公式、Decimal 输入、rounding、unit 和结果可确定重放；模型输出数字不能绕过 Tool 成为 verified Claim。
- 文档删除/新版本后，旧 Evidence 可追溯但不会被新 Run 当作 active Context。
- Research 主要节点 hard stop 后从最后成功 Checkpoint 恢复；重复 resume、allow/deny/timeout 和取消不重复副作用。
- LangGraph state 与 Checkpoint schema/version 映射不兼容时明确拒绝或迁移，不使用 Trace 或入库 stage 状态冒充 Agent Checkpoint。
- 知识/Checkpoint 学习界面刷新后仍能解释文档版本、Agent 使用的 locator、暂停原因、审批结果和恢复位置。

### 当日产物

完整知识库/文档界面、可观察入库流水线、SEC fixture 数据包、Knowledge Tool/Context Source、typed calculator、Dense baseline、版本化 filing Evidence locator、Deep Research L4、Checkpoint/HITL 时间线、`sec-fixture-v1` 与恢复对照 Eval、`docs/ingestion-state-machine.md`、Checkpoint 契约、Parser fixture、`learning-log/day-5.md`。

### 验收门禁

- 代表性文档进入 ready；固定 SEC filing fixture 通过同一 Harness/Runtime 检索并引用真实 accession/section/page/Chunk/Asset。
- 上传不等待解析，Worker 强制中断后恢复或安全重试，失败状态对 Agent 与用户都可解释。
- 对照报告证明加入 filing Evidence 与 typed calculator 后的事实/计算变化；不能只证明“向量库返回了结果”或“模型给出了相同数字”。
- Research 强制中断后可从版本化 Checkpoint 恢复；allow/deny、重复 resume、取消和预算真实生效，重复副作用数为 0；跨刷新与 Worker 重启的组合验收在 Day 8 完成，并在 Day 10 复跑发布回归。
- Workbench 能沿“CIK/accession → 文档版本 → Chunk/Asset → Evidence/计算输入 → Research Step/Checkpoint”导航，而不是只展示状态标签。

### 复盘题

知识库是 Tool、Context Source 还是 Memory？Agent Checkpoint 与 ingestion stage checkpoint 为什么不能共用一个状态模型？为什么 Runtime 不应知道 Milvus？冻结 SEC fixture 能证明哪些合同，又为什么不能证明 live EDGAR 或模型质量？

## 13. Day 6：SEC 官方披露数据底座与 Point-in-Time 合同

> 执行状态（2026-08-28）：Day 6 已由 [PR #10](https://github.com/hrw991009/industry-intelligence-platform/pull/10) 合入 `main`，功能 head [`7a4766b`](https://github.com/hrw991009/industry-intelligence-platform/commit/7a4766b6d4c4ad764b9e095b2d0f03d8ec96c143) 的 push CI [`33053621106`](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/33053621106)、PR CI [`33053623731`](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/33053623731) 和合并提交 [`84a7945`](https://github.com/hrw991009/industry-intelligence-platform/commit/84a7945ed769d63974602b5c20984e2f4ebf0e93) 的 main CI [`33054136204`](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/33054136204) 均通过 7 个适用 Job。提交、合并和 CI 条件已经关闭，但确定性报告仍为 contract `18/18`、closeout `4/6`、总计 `22/24`，bulk coverage readiness `0/2`；D6-02/D6-06 因 `submissions.zip`/`companyfacts.zip` snapshot、published/coverage watermark 和 post-watermark gap 仍缺而保持 `thin_slice`，其余 D6 项保持 `implemented_pending_verification`。当前也没有合法 SEC 联系身份对应的 live smoke，D5-08/D5-09 浏览器 DoD 继续保留。项目所有者已明确把这两条 closeout case 改期为 Day 10 发布硬门并继续 Day 7；这是排期变更，不是从原分母删除失败事实。逐步范围与验收证据见 [Day 6 执行计划](learning-log/day-6.md)。

### 学习主题

- CIK、ticker、公司名称、accession、form、filing date、accepted at 与 report period 的不同语义。
- SEC submissions、XBRL 聚合 API、原始 filing/iXBRL 和 nightly bulk 的覆盖边界。
- 外部披露权威、系统业务事实源、不可变快照与派生索引之间的关系。
- Fair Access、服务端 User-Agent、缓存、限流、退避和来源使用条款。
- `as_of`、amendment 和“当时可见信息”如何进入数据模型、Tool 参数与 Eval。

### 实现任务

1. **官方 Adapter 与 CIK 解析**：建立最小 `disclosures` bounded context、`SecEdgarPort`、Frozen/Live Adapter、canonical filer/alias 和 `sec.resolve_filer@v1`；落实官方 host allowlist、身份化 User-Agent、跨进程速率预算、缓存/条件请求、响应预算、429/5xx 有界退避和来源使用记录。交互/小批量走 API；本步冻结并测试同批次达到 100 个 CIK 或全量刷新必须走官方 bulk 的选路合同，失败不得静默退化为高扇出请求。实际 bulk bytes、`bulk_published_at`/`coverage_through` 与 post-watermark 增量补齐跟随 Step 2 submissions 和 Step 4 XBRL 的正式批量读取落地，在此之前 D6-06 只能是 `thin_slice`。ticker/name 只返回带依据的候选，歧义不猜。
2. **Point-in-Time filing 选择**：建立 canonical filing、base/amendment 关系、版本化 `FilingSelectionScope v1`、`public_available_at`/可见性依据/策略和 `sec.list_filings@v1`；`latest` 必须在 `as_of` 与 amendment policy 下解析成明确 accession 并进入 Trace。按查询区间跟随并快照 submissions `filings.files` supplemental JSON，保存包含 bulk/incremental watermark 的 coverage manifest、按 accession 去重；只有 current、所需 supplemental 文件和截至 `as_of` 的时间覆盖均完整后才能返回 `no_result`。每个 source version 另存可见时间/依据/有效区间；`retrieved_at` 既不能代替版本可见时间，也不能把更正后字节追溯到更正前。
3. **不可变快照、Dense read 与 Workbench**：分离 official document identity/current projection 与 append-only `sec_source_snapshots`，通过 `workspace_sec_imports` 复用既有 File/Knowledge/Ingestion、Job/Outbox 和双索引；discovery Tool 可读公共 catalog，facts/search/read 强制 import。交付锁定 accession 的 `sec.search_filing@v1`/`sec.read_filing_section@v1`、`dense-v1` 和 CIK→accession→snapshot→DocumentVersion/Chunk 导航。
4. **XBRL context/fact 与 typed read**：保存聚合响应/raw iXBRL/instance XML snapshot、source kind、concept/unit/period/accession 和 source-specific nullable context/dimensions/decimals/scale，交付 `sec.get_xbrl_facts@v1`；aggregate 与 raw locator 分型，精确 raw/custom fact 回到锁定原始 XBRL。Workbench 增加 context/fact 面板与 standard/raw 浏览器反查；`frames` 只作候选发现。
5. **五个只读 Tool 与 `sec-source-v1` 收口**：建立只暴露 Day 6 五 Tool 的同一 Runtime/Harness profile；至少 24 个确定性 case 分为 contract/closeout regression，并用 `execution_kind=tool|sync`、`sync_kind=canonical_source|workspace_import` 和 eligible denominator 覆盖 identity、visibility/amendment、coverage watermark、snapshot/XBRL、故障、幂等和权限。deterministic replay 与 live smoke 分报；完成适用 DoD、分支/main CI 和所有者复核后再关闭 D6-01～D6-08。

### 测试

- CIK/ticker/name 解析、form allowlist、amendment/base 关系和 `as_of` 过滤的领域测试；覆盖只存在于 supplemental JSON 的历史 filing、current/supplemental 重复 accession、缺失/损坏 supplemental response 和不完整 coverage 禁止 `no_result`。
- SEC client 的 User-Agent、跨进程速率预算、缓存、退避、超时、响应预算、失败分类、官方跳转限制、99/100 CIK bulk threshold、bulk published/coverage watermark、post-watermark 增量补齐、partial/hash/failure 和无浏览器 CORS 依赖合同。
- 原始 filing/XBRL fixture 的解析、hash、重复同步、partial failure、Worker 重启和对账。
- XBRL instant/duration、unit/scale、dimensions、aggregate nullable 字段、raw/aggregate locator、standard/custom concept 与错误 context 负向测试。
- filing snapshot → Knowledge version → Evidence candidate 的真实 PostgreSQL/MinIO/Milvus/Elasticsearch 集成。
- 官方 live smoke 与冻结 replay 分开运行；PR CI 不访问实时 SEC，也不把 replay 写成 live 数据质量成功。

### 当日产物

SEC 来源复核、ADR 0007 修订、`disclosures` 模块、迁移、EDGAR Adapter、五个只读 SEC Tool、原始 filing/XBRL 快照链、Filer/Filings Workbench、`sec-source-v1` 数据集与来源可靠性报告、[Day 6 执行计划与日志](learning-log/day-6.md)。

### 验收门禁

- 一个公司可从名称/ticker 解析到明确 CIK，并锁定截至 `as_of` 的正确 form/accession；歧义时不猜测。
- 每个 eligible XBRL fact 和文本 Evidence 可反查 accession、适用的 context/section、unit/period、官方 URL、source snapshot/version hash、该版本可见时间/依据和 retrieved at；aggregate 缺失 raw 字段时明确为空而非伪造。
- cutoff 后 filing 进入 Context 的数量为 0；错误公司、form 或 accession 在确定性集上为 0。
- SEC 限流/429/依赖失败不伪装成 no result；重复同步不产生重复 filing、snapshot、fact 或索引。
- Day 6 只证明数据合同和只读 Tool，不提前声明财务计算、Hybrid Retrieval、L5 或中文 Agent 已完成。

### 复盘题

为什么 ticker 不能作为稳定身份？为什么 companyfacts 不能替代原始 filing？为什么 frames 不能作为精确 fiscal period 核验的最终依据？`as_of` 应在哪些层强制执行？

## 14. Day 7：Filing Hybrid Retrieval、财务计算与核对

> 执行状态（2026-08-28）：项目所有者已推进至 Day 7 Step 4。Step 1 的 `hybrid-v1`、BM25/RRF/可插拔 reranker、完整 Retrieval Trace、PostgreSQL 重载授权、filing text/XBRL fact Evidence locator 与迁移已实现，提交 `2944591` 的分支 CI `33135122319` 通过 7 个适用 Job；冻结 Recall@5/MRR、table/cell/character locator、Citation 100% 可解析评测、PR/main CI 仍缺，因此 D7-01 为 `implemented_pending_verification`、D7-02 为 `thin_slice`。Step 2 的 `financial-context-v1` 与 Step 3 的正式 XBRL operand、Decimal calculator、`financial-reconciliation-v1`、Calculation Evidence 重算链均在唯一 Runtime/Evidence 边界内实现。Step 4 当前工作树新增 fail-closed `sec.diff_filings@v1`、exact six-Tool `sec-l4-v1`、SEC/Research Workbench 全链审计，并补齐 SEC/XBRL/Calculation Evidence HTTP 序列化。本地 Python `1069 passed, 84 skipped`、Ruff、mypy `475` 个源文件、Web `87 passed`、build 与 OpenAPI 确定性通过。提交 `3462b48` 的 CI `33149285431` 为 6/7 Job；失败的 PostgreSQL Job 暴露测试夹具遗漏 `Document.active_version_id`，当前已按正式 ready/active 状态修复但尚无新远端证据。正式浏览器 API 全链、中英 paired run、真实依赖重跑及提交/PR/main CI 仍缺，因此 D7-03～D7-07 均为 `implemented_pending_verification`。Day 6 两条 bulk watermark case 保持原 `22/24` 分母并改为 Day 10 发布硬门，不构成完成或豁免。

### 学习主题

- 结构化 XBRL 与 Filing 文本/表格 RAG 的互补边界。
- 财务 concept、context、period、unit、scale、rounding、dimension 和 amendment 的核对顺序。
- 检索候选、Evidence、calculator input、Claim 与 Citation 的不同语义。
- 原子 Claim 分解和部分顺序 trajectory，为什么不要求唯一精确工具序列。
- 中文问题、英文披露证据与双语输出一致性。

### 实现任务

1. **Hybrid Retrieval 与 SEC locator**：在锁定 CIK/accession/`as_of` 的 Day 6 Dense 链上加入 Elasticsearch BM25、版本化 RRF、可插拔 reranker、去重和 section/table 多样性；Milvus/Elasticsearch 只给候选，PostgreSQL 重载授权与 source identity 后才规范化为 filing text/table/section 或 XBRL fact Evidence/Citation。
2. **Financial Context Compiler**：扩展现有 Context Compiler，按问题、`FinancialScope`、Memory、XBRL facts、Filing Evidence、Tool Observation 与 Token budget 生成 `financial-context-v1` manifest；cutoff 后、错误 accession、不可比较 unit 和超预算候选必须记录稳定排除原因。
3. **Typed calculator 与 reconciliation**：把既有 `finance.calculate@v1` 扩展到正式 SEC Evidence 输入，保留 Decimal、受控 operator、比例/百分比/变化率、rounding 和 unit propagation；新增 company/accession/form/period/instant-duration/unit/scale/dimensions/concept/amendment 的 typed reconciliation。
4. **Filing diff、中文 L4 profile 与 Workbench**：实现 `sec.diff_filings@v1` 的 base/amendment 与相邻可比 filing fact/section diff；建立 `scope → resolve → select → decompose → structured+narrative → calculate → reconcile → draft` 的 `sec-l4-v1`，复用唯一 Runtime/Checkpoint/Evidence 账本；Workbench 读取正式 API/Event/Trace 展示全链。
5. **`sec-tool-v1`、A0/A1/A2 与收口**：用同一 case manifest、数据版本、Scope 和预算对照 oracle/full context、纯 Hybrid RAG、RAG + SEC/XBRL Tool + calculator；分别报告简单事实、计算、跨章节、修订和无答案，并完成统一 DoD、三层 CI 与所有者复核。

### 测试

- Hybrid ranking、filter、active snapshot、section diversity 和 Context budget 的确定性测试。
- 财务公式、百分比/比例、负数、零分母、unit/scale、rounding 和输入 Evidence 完整性测试。
- fiscal/calendar、instant/duration、amendment、custom tag 和表格/叙述冲突负向集。
- 错误 CIK/accession、future leakage、伪造 fact/公式/Citation 和跨 Workspace 候选全部 fail closed。
- 同题中文/英文 pair 必须选择相同 filer/accession、事实、公式和终态；只允许解释语言不同。
- A0/A1/A2 同一 case manifest、预算和数据版本运行，不能给某个策略额外 oracle 信息。

### 当日产物

Filing Hybrid Retrieval、SEC locator、Financial Context Compiler、typed calculator、reconciliation、filing diff、SEC L4 profile、`sec-tool-v1`、A0/A1/A2 报告、完整 Retrieval/Calculation Workbench、`docs/sec-retrieval-design.md`、`learning-log/day-7.md`。

### 验收门禁

- Retrieval Recall@5 在冻结 SEC 黄金集不低于 0.80，Citation/source identity 可解析率 100%，跨 Workspace 与 future leakage 均为 0。
- 所有用户可见派生数字都存在 calculator trace；无来源数字、公式或错误 unit/period 数量为 0。
- 确定性集上错误 company/period/accession 为 0；证据不足正确拒答率不低于 0.90。
- A2 必须在复杂计算/修订场景相对 A1 有可测净收益，简单题退化不得超过 2 个百分点；否则回退相应复杂策略。
- Day 7 交付可解释 L4 draft，不把尚未执行 L5 Verifier 的结果标为 verified。

### 复盘题

什么时候应信结构化 fact，什么时候必须回原 filing？为什么“答案数字正确”仍可能是错误结果？模型分解 Claim 与确定性 calculator 的责任如何分开？

## 15. Day 8：SEC Verified Agent L5、监控与 Durable HITL

> 执行状态（2026-08-29）：Day 8 Step 1～5 已在 `feat/day-8` 工作树实现。Step 1～4 已提供 Verifier/one-revise、Monitor/Case、七工具 L5 Profile、持久 HITL、同 Run resume 与正式 Workbench；Step 5 新增 14-case/42-run `sec-verification-v1`、独立 A2/A3/A4 scorer、冻结 observations 和可重生成 JSON/Markdown。frozen deterministic/security/fault 合同门通过，A3 对 A2 的复杂场景净增益 `0.714286`、简单题退化 `0`，A4 operational/recovery 单独为 `1.0/1.0`。最终本地门禁为真实依赖 `1207 passed`、总体/核心分支 coverage `80.21%`/`86%`、Web `89 passed`、现有 Chromium `8 passed`，依赖与完整历史密钥审计通过。D8-01～D8-08 均为 `implemented_pending_verification`；报告不是 live SEC/model 质量，专用 Monitor browser、真实 Monitor hard-stop 注入、branch/PR/main CI、owner review 与 Day 4～7 债务均未关闭。详细事实见 [Day 8 执行计划](learning-log/day-8.md) 与 [SEC Verifier、Monitor 与恢复设计](sec-verification-monitor-design.md)。

### 学习主题

- Verified Claim precision 与回答覆盖率之间的权衡。
- 可判定 Verifier、最多一次 revise、停止语义和人工审批边界。
- 新 filing/amendment 监控如何复用 Schedule/Job/Outbox 和幂等 Case。
- 间接 Prompt Injection、外部披露不可信输入与写 Tool 最小权限。
- hard stop、重复 resume/decision、迟到结果和重复通知的恢复正确性。

### 实现任务

1. **Claim Verifier 与四种业务状态**：在同一 Research graph 中定义 SEC Evidence-aware Verifier、typed issue/report，逐 Claim 重载并检查 filer/accession/`as_of`、support、unit/period/context、Calculation lineage、coverage/conflict 和 Citation；业务状态固定为 `verified/partial/conflict/insufficient_evidence`，与 Runtime stop reason 分列。
2. **One-revise L5 与不可信输入防线**：实现最多一次 `verify → targeted retrieve/recalculate → revise → finalize`，冻结原 Workspace/Scope/toolset/budget；filing、网页、表格和 Observation 中的指令只作为不可信数据，加入 indirect prompt injection、伪造 tool/system、超预算和 no-progress 场景。
3. **Monitor、watermark 与幂等 Case**：建立版本化 Monitor/rule/watermark/Case；Beat 只创建 occurrence/Job/Outbox，Worker 复用 SEC sync、filing diff、Evidence 与 side-effect ledger，新 filing/amendment 的同一 trigger 只产生一个 Case。
4. **`monitor.subscribe@v1`、Durable HITL 与 Workbench**：模型只能请求订阅，当前用户 allow/deny/timeout 后才落库；复用 ApprovalRequest/Decision、Checkpoint CAS 和副作用账本，完成跨刷新/Worker 重启/取消竞态恢复，并让 Workbench 从正式 API/Event/Trace 展示 Verifier、revise、Approval、Monitor、Case 与 Evidence 反查。
5. **`sec-verification-v1`、A2/A3/A4 与收口**：冻结 deterministic/fault/security cases；同 manifest、数据、Scope 和预算比较 A2、A3、A4，分别报告质量/coverage、trajectory、恢复、副作用、成本和延迟，并完成三层 CI、正式浏览器和所有者复核。

### 测试

- Verifier 每条规则和四种业务终态的确定性单元测试；`partial/conflict/insufficient` 不能被 UI/导出改写为 verified。
- 最大 revise、预算、deadline、取消、no progress 和重复 Observation/Calculation 的停止测试。
- Prompt injection 下 read/write tool surface、WorkspaceScope、`as_of` 和 Budget 不变；未授权写 Tool 调用为 0。
- Monitor 的时区、watermark、misfire、重复 tick、amendment、过期 lease、429 和 dead-letter 测试。
- hard stop/重复 resume/decision/迟到结果后，ToolCall、Calculation、Monitor、Case 和通知重复数为 0。
- A2/A3/A4 使用同一 frozen cases 和预算；LLM judge 只辅助，不决定 hard gate。

### 当日产物

SEC L5 Verifier、one-revise graph、四种业务终态、Monitor/Case/HITL、fault/security suite、A2/A3/A4 报告、Verified Agent Workbench、[SEC Verifier、Monitor 与恢复设计](sec-verification-monitor-design.md)、`learning-log/day-8.md`。

### 验收门禁

- 冻结确定性集内伪造 source/accession/number/formula 为 0，verified Claim 的错误支持为 0。
- Citation 可解析、future leakage、跨 Workspace 泄漏、未授权写操作和重复副作用分别为 100%/0/0/0/0。
- A3 在复杂多源/计算/冲突场景相对 A2 有净收益，且简单题退化不超过 2 个百分点；无收益则移除强制 revise 或回退规则。
- 一个 monitor 经审批创建，新 filing/amendment 只生成一个 Case；deny/timeout 不产生写入。
- Worker 强制终止后从最后成功 Checkpoint 恢复，业务终态和 Runtime stop reason 均可解释。

### 复盘题

为什么 verified precision 可以通过更多拒答提升，却不代表产品更有用？Verifier 应该判断事实还是文风？哪些操作必须 HITL，哪些只读检索不应堆审批？

## 16. Day 9：公开 Benchmark、SEC Temporal Eval 与中文验证链

> 执行状态（2026-08-30）：Day 9 五步已由 PR #15 合入 `main`，功能 head `6a79e4a`、合并提交 `4500505`，push/PR/main 三层 CI 均通过 7 个适用 Job。四个外部数据集均为 `adapter_ready`/`release_eligible=false`，`sec-temporal-v1` 固定 60 个中英 case，`agent-security-v1` 以 6-case/18-trial 冻结 observation 验证 scorer；`release-suite-v1` 分列 deterministic/offline/live/failure taxonomy，并拒绝把不同 case suite 拼成全局 A0～A4 分数。统一 common-case manifest、Recall@5、真实 Runtime/model/database、公开集 prediction、live≥3 次、官方 judge、case→Run/Trace/Evidence、中文抽样和 owner review 仍缺。因此 D9-01～D9-07 保持 `implemented_pending_verification`，D9-08 保持 `thin_slice`；后续只按 [Day 10 执行计划](learning-log/day-10.md) 和 [发布就绪合同](release-readiness.md) 补证收口。

### 学习主题

- Benchmark coverage、产品验收、数据许可、污染和时效漂移的区别。
- 固定离线 CI、离线 release suite 与定时 live eval 的不同结论。
- FinQA/TAT-QA 的数值推理能力、FinanceBench 的小型 filing RAG 证据能力和 FinSearchComp 的 live research 能力边界。
- trajectory/result/evidence/runtime/point-in-time/security 分层评分。
- 中英配对 case 如何验证同一事实链，而不伪造“中国金融专家 benchmark”。

### 实现任务

1. 定稿 [SEC Agent 评测计划](sec-agent-evaluation.md) 和 release `EvalCase/Manifest`：固定 dataset/split/license/commit/hash、filer/accession/`as_of`、runtime/harness/model/prompt/tool/context version、预算和 scorer。
2. 接入 FinQA adapter，评分 supporting facts、program/execution answer；它只证明固定上下文数值推理，不作为开放检索或当前 SEC 质量证据。
3. 接入 TAT-QA adapter，评分 answer EM/F1、source、scale 和 derivation；它只证明表格+文本与单位/算术。
4. 对 FinanceBench 公开 150 题只做经许可审查的补充评测；CC BY-NC 和 source PDF 权利限制写入 dataset card，不作为商业分发基础。
5. 接入 FinSearchComp 的固定 historical/可复现子集与定时 live suite；动态题、专业数据库依赖和 LLM judge 漂移单独报告，不进入普通 PR 硬门。
6. 自建 `sec-temporal-v1`：锁 CIK/accession/`as_of`/fact/formula，覆盖 amendment、custom tag、期间/单位冲突、文本脚注、拒答和 cutoff；以官方 filing/XBRL 可确定复核，不要求专家撰写开放式投资结论。
7. 建立中英配对集：同一 gold scope/Evidence/program 生成中文和英文问题，比较 source selection、formula、result、终态与引用；中文解释质量人工抽样，不能只靠翻译模型 judge。
8. 固定 BFCL 非 live 类别或自建等价 function-call gold，使用 ToolSandbox/tau-bench 的 milestone/final-state 方法设计多轮状态题，并以 AgentDojo 方法验证间接提示注入；这些结果不替代财务正确性。
9. 运行 A0～A4 全量消融和 failure taxonomy，输出 retrieval、answer/program、Evidence/Citation、trajectory、recovery、point-in-time、security、Token、费用和延迟。
10. 对固定 provider/model/version 的 live/model suite 至少重复 3 次，报告均值、离散度和 `pass^k`；冻结 replay 只进入 deterministic contract 报告。

### 测试与数据治理

- 每个外部数据集有来源、版本/commit、split、license、checksum、允许用途、污染风险和本地转换测试。
- Gold trajectory 使用 required/allowed/forbidden tools、argument constraints、partial order、budget 和 final state，不绑定唯一精确序列。
- 规则 Scorer 与人工抽样校准；LLM judge 输出不稳定或与规则冲突时不能覆盖 hard gate。
- Temporal case 构造器验证 cutoff，不允许从未来 filing、最新 companyfacts 快照或缓存侧漏答案。
- 报告必须能从分数反查 case、run、trace、Evidence、calculation 和数据 manifest。

### 当日产物

FinQA/TAT-QA/FinanceBench/FinSearchComp adapters 与 dataset cards、`sec-temporal-v1`、中英配对集、agent/security suite、A0～A4 报告、live repeated-run 报告、failure taxonomy、`learning-log/day-9.md`。

### 验收门禁

- 固定离线集的来源/Citation 可解析率 100%，fabricated source/number/formula、future leakage、错误 company/period/accession、跨 Workspace、未授权写操作均为 0。
- 无答案正确拒答率不低于 0.90，SEC Retrieval Recall@5 不低于 0.80；公开 benchmark 使用其官方指标并与本项目门禁分报。
- 中英 pair 的 filer/accession/Evidence/formula/result/终态一致率为 100%；语言质量问题单独记录，不掩盖事实链差异。
- A2/A3 相对 A1 的收益、简单题退化、成本和延迟满足已冻结门禁；否则回退相应 Agent 复杂度。
- 任一公开 benchmark 缺失许可、版本或可复现 manifest 时只能记为实验，不能进入 release claim。

### 复盘题

哪个 benchmark 真正在测当前产品？为什么 FinQA 高分不能证明 EDGAR Agent 可用？为什么动态 benchmark 不应阻塞普通 PR？怎样发现中英问题使用了不同证据链？

## 17. Day 10：披露核验工作台、发布评测与完整交付

> 执行状态（2026-08-31）：Day 9 已由 [PR #15](https://github.com/hrw991009/industry-intelligence-platform/pull/15) 合入 `main`，功能 head `6a79e4a`、合并提交 `4500505`；push/PR/main 三层 CI 均通过 7 个适用 Job。Day 10 Step 1～Step 4 已在 `day-10` 工作树实现。Step 4 在冻结模块集合上把核心覆盖率提升并锁到 90%，新增锁定 Semgrep、Python/Node 许可证与 NOTICE 门禁，并以 `sec-release-recovery-v1` 冻结 12 个恢复/回滚场景。checked 恢复 observation 仍为 `not_executed`/0 of 12，四个指标均 `not_measured`，不能以 Runbook 或单测替代演练。readiness 当前登记 63 个 artifact，状态为 45 complete、33 implemented pending verification、9 thin slice、1 planned，共 43 未完成；16 个 blocker open、5 个 external gate pending，机器结论仍为 `no_go`。D10-03 为 `implemented_pending_verification`，D10-06 为 `thin_slice`；真实 Runtime/public/live、无拦截浏览器、正式恢复演练、许可/中文签字、远端 CI 和 owner review 尚未完成。

### 学习主题

- 业务工作台如何将复杂 Agent 轨迹压缩为可操作的核验结果。
- 发布门禁、运行证据、owner acceptance 与“代码存在”的区别。
- 质量、coverage、延迟和成本如何共同决定默认 profile。
- 数据源、模型、Tool、Prompt 和 benchmark 版本如何进入回滚与发布说明。

### 实现任务

1. 收敛产品路由为公司/filing 浏览、事实核验、Evidence/Calculation、Monitor/Case 和 Eval Workbench；已完成的通用页面保留历史入口或明确归档，不再扩张其 Provider。
2. 完成统一 UI 状态：loading、empty、ambiguous、not-ready、error、forbidden、partial、conflict、insufficient、cancelled、approval-required 和 retry。
3. 完成端到端中文用户路径：公司/期间澄清 → accession/`as_of` → XBRL + Filing Evidence → calculator → verifier → 引用报告 → 批准 monitor → 新 filing diff/Case。
4. 聚合 request/job/run/step/tool/evidence/calculation/checkpoint/monitor/case 的 JSON 日志、OTel Trace 和指标；建立 SEC rate-limit、source freshness、future-leakage、citation、verification 和 recovery 面板。
5. 完成 Agent/SEC 安全回归：Workspace、Tool allowlist、Prompt Injection、Secret、原始 filing/对象访问、写审批、数据许可和报告免责声明。
6. 完成 fresh environment、migration、备份/恢复、清空并重建 Filing 索引、Worker/Redis/MinIO/ES/Milvus/SEC 依赖故障与上一镜像回退演练。
7. CI 运行 deterministic quick suite、全量工程门禁、覆盖率、OpenAPI/SSE diff、Gitleaks、依赖/许可证/NOTICE 与关键浏览器 E2E；慢/付费/live eval 为受控 release job。
8. 清偿 Day 4 核心 85%→90% 覆盖率债务；完成 D1-09 Provider 侧凭据处置和复扫，否则不得发布标签。
9. 完成能力矩阵双向审计、ADR/README/Runbook、限制与 rollback；每个 `complete` 都附 branch CI、main merge CI、Trace/Eval/DoD 与项目所有者复核。
10. 根据 A0～A4 报告冻结默认 profile；L6 多 Agent 只有显著净收益时另开 ADR/time-boxed 实验，不进入本版本尾声。

### 完整用户路径验收

```text
登录 → 输入中文财务事实核验问题
→ resolve 公司/CIK → 选择 form、report period、as_of 与 accession
→ XBRL fact + Filing Hybrid RAG → typed calculator
→ reconcile unit/period/context/amendment → Evidence-aware verify
→ 最多一次 revise → 查看 verified/partial/conflict/insufficient 报告
→ 逐个反查官方 filing、fact/section、公式和计算输入
→ 人工批准创建 monitor → 模拟新 filing 到达 → 单一 diff Case
→ Workbench 比较 A0～A4、固定/公开/live 评测与成本延迟
```

### 最低质量门禁

- 核心 domain/application 覆盖率不低于 90%，后端总体不低于 80%，前端关键状态不低于 75%；关键 E2E、migration、contracts、CI 和 secret scan 全绿。
- Citation/source identity 可解析率 100%；fabricated source/accession/number/formula、future leakage、错误 company/period/accession、跨 Workspace、未授权写操作和重复副作用均为 0。
- 无答案正确拒答率不低于 0.90，SEC Retrieval Recall@5 不低于 0.80；accepted baseline 下降超过 2 个百分点阻断发布。
- fixed/replay、public benchmark 和 live repeated-run 报告分开；LLM judge 不是任何硬门的唯一判据。
- 所有 Run 有唯一 Runtime 终态/stop reason，所有报告有明确业务核验状态；恢复场景成功率 100%。
- 不输出预测、估值、目标价、荐股或交易动作；所有产品页面和导出明确说明是披露事实核验而非投资建议。
- 新环境可按 README/Runbook 复现核心 Scenario；备份恢复、索引重建和上一镜像回退成功。

### 当日产物

SEC 披露与财务事实核验工作台、release Eval manifests/reports、完整 Workbench、Runbook、来源/许可证清单、架构与评测报告、[Day 10 执行计划](learning-log/day-10.md)、[发布就绪合同](release-readiness.md)、演示证据和 `v0.2.0-sec-disclosure-verifier` 标签候选。

### 复盘题

哪项 Agent 复杂度产生了可测净收益？哪些 case 仍只能拒答？当前证据能支持“财务事实核验”这一声明到什么边界，哪些能力仍然不能对外宣称？

## 18. Agent-first 能力与证据审计

`docs/feature-matrix.md` 继续记录全部产品能力，并使用 `complete`、`implemented_pending_verification`、`thin_slice`、`contract_only`、`blocked`、`planned` 等事实状态。Day 10 时所有冻结目标都必须达到 `complete`；执行优先级首先由 SEC Agent 主线证据决定，而不是由页面数量、旧行业 Adapter 数量或公开 benchmark 总分决定。

Day 10 采用两级门禁：

- **核心 Agent 能力**：Runtime/Harness、SEC typed Tool、XBRL + Filing RAG、typed calculator、Evidence/Claim、point-in-time、Verifier、Recovery/HITL、Monitor 和 Eval 必须在冻结范围内达到 `complete`。
- **支撑产品能力**：身份/Workspace、Job/Outbox、会话、文件、Knowledge 入库和 Web 旅程必须足以真实承载核心 Run；已完成的旧行业 Adapter 只保留历史证据，不扩张为新主线。

任何核心项只有最终答案、Mock、截图或一次手工成功而没有 Trace、失败/恢复场景和 Eval 报告，均视为未完成。

### 18.1 必须覆盖的目标能力组

| 优先级 | 能力组 | 实现日 | 必须提交的证据 |
|---|---|---|---|
| P0 | Agent Runtime | Day 2～3 | 同一正式入口、typed state、Run/Step/Event、明确 stop reason、预算与可读 Trace |
| P0 | Agent Harness | Day 2～3 | Scenario、Fake/Replay、Fault injection、Tool/Context/Approval 组合、报告 CLI 与同一 Runtime |
| P0 | Tool-using Agent | Day 3 | L1/L2 轨迹、正确 Tool/参数、预算终止、Web/Text2SQL 真实 Artifact、越权拒绝 |
| P0 | Agent Memory | Day 4 | Short/Long-term Memory 分层；写入、召回、冲突、反馈、治理与 Eval；删除后 Context 残留为 0 |
| P0 | SEC 披露数据合同 | Day 6 | CIK/accession/form/as_of、原始 snapshot、XBRL context/fact、Fair Access、amendment 与不可变 lineage |
| P0 | 财务 Tool Agent | Day 5～7 | SEC Tool 选择/参数、结构化+叙述检索、typed calculator、unit/period/context reconciliation 与无越权调用 |
| P0 | Deep Research | Day 4～8 | L3 Evidence graph、金融 L4 durable state、SEC L5 verifier/one revise，以及 A0～A4 对照 |
| P0 | Durable/HITL 与 Monitor | Day 5、Day 8 | Worker 中断恢复、重复 resume/decision 零副作用、monitor 写审批、幂等 Case 与取消/预算 |
| P0 | Agent Knowledge/RAG | Day 5～7 | 同一 Runtime 接入 filing snapshot、XBRL + Hybrid Retrieval；Context manifest、计算、引用、拒答和策略对照 |
| P0 | Point-in-time 与 Evidence | Day 6～9 | company/period/accession/source identity、future leakage 0、Citation 100%、amendment/custom tag/冲突/无答案 |
| P0 | Agent Evaluation | Day 2～10 | 通用 50 场景保留；SEC 专项、FinQA/TAT-QA、temporal/双语/agent/security 分层指标与版本基线 |
| P0 | Agent Learning Workbench | Day 2～10 | Run/Context、Tool、Memory、Evidence/Claim、Filing/XBRL、Calculation、Checkpoint/HITL、Retrieval/Citation、Verifier/Monitor 均由正式事实驱动 |
| P1 | 身份、Workspace、Job/Outbox、会话、文件 | Day 1～5 | 核心 Run 所需真实链路、跨租户负向、迁移、后台执行和最小 E2E |
| P2 | 已完成行业 Adapter 与非 SEC 页面 | Day 3（历史） | 保留既有验收与 readiness，不新增 Provider，不作为 SEC 能力替代证据 |

### 18.2 双向审计方法

1. 从 Scenario 出发，追到 Harness profile、Runtime/Workflow、Tool/Context、Application Service、Artifact 和 Scorer，确认只有一条正式链路。
2. 从每个 SEC Run 反查 runtime/harness/model/prompt/tool/context version、CIK/accession/as_of、Event、Trace、Checkpoint、Evidence、Calculation、final output 与可选 Artifact。
3. 对每个复杂度增量比较前一层基线；没有净收益就回退，不把“更像 Agent”当作收益。
4. 运行完整财务核验/监控旅程、fresh migration、API/SSE contract、跨租户与 future-leakage 负向、fault/security suite、公开/temporal/bilingual Eval 和 secret scan。
5. 在 feature matrix 中分别记录 P0 门禁与 P1/P2 支撑状态；P1/P2 按简化后的冻结范围验收，范围外扩展不阻断 P0，但任何矩阵内未完成项都不能冒充 `complete`。

生产级投研、审计、投资建议和交易成熟度不属于本次能力审计；本计划只验收 SEC 披露事实核验和监控的冻结范围。

## 19. 全局 Definition of Done

任何 Day 1～Day 10 目标从过程状态改为 `complete` 前，都必须逐条评审下列 Definition of Done 的适用性：凡适用项必须全部通过；标记 `N/A` 必须写明与该目标无关的具体理由和复核人，不能用来逃避实现。面向用户的业务能力不得把真实用户旅程、服务端权限、正常/失败/恢复测试或安全检查标为 `N/A`；工程、文档和治理目标可以用等价的开发者/运维旅程与自动化校验替代，只有确实不改变数据库、HTTP/SSE 或运行时行为时，才可把对应 migration、契约或遥测项记为 `N/A`。`thin_slice` 和 `contract_only` 不是质量豁免：

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

### 19.1 Agent 能力追加 Definition of Done

从 Day 2 开始，Agent 核心能力除满足全局 Definition of Done 外，还必须同时满足：

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

## 20. 防偏航与明确禁止项

- Day 1 门禁未过，不进入 Day 2 Agent 主线；当前日门禁未过，不进入下一日。
- 此处 Day 1 执行门禁指新仓工程与新仓 Secret 边界；参考仓历史凭据处置 D1-09 是 Day 10 发布标签的独立阻断项，不允许被忽略或伪装关闭。
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
- Day 1～Day 10 不引入 Kubernetes、微服务、Neo4j 或多套重叠的可观测平台。
- 禁止输出股价预测、估值、目标价、荐股、组合建议、自动交易或把披露事实核验包装为审计/投资意见。
- 禁止让模型心算后直接持久化派生数字，或使用任意代码执行替代 `finance.calculate@v1`。
- 禁止混用 CIK/公司、accession、fiscal/calendar period、instant/duration、unit/scale、base/amendment；任何歧义必须澄清、冲突或拒答。
- 禁止使用 SEC `frames` 的近似对齐做精确公司期间核验，或把 companyfacts 当作 custom tag、脚注和原始 filing 的完整替代。
- 禁止在 point-in-time case 中读取 cutoff 后 filing、最新缓存、未来 companyfacts 或由答案派生的索引。
- 禁止把动态 benchmark、单次 live run、公开 leaderboard 或 LLM judge 总分作为唯一发布证据。

若本机资源不足以同时运行 Milvus、ES 和观测栈，使用 Compose profiles 分时启动，但不得改变目标架构或用低质量实现冒充最终方案。若没有外部/付费 Provider，测试使用 Fake Adapter，正式接口必须返回未配置错误。若解析器兼容性阻塞，先通过 Parser Port 交付基础 PDF Adapter，再单独解决许可和依赖，不能把解析逻辑耦合进业务 Service。

### 20.1 Agent 专项防偏航规则

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

## 21. 权威技术参考

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

## 22. 变更记录

| 版本 | 日期 | 变化 | 决策人 |
|---|---|---|---|
| 1.0.0 | 2026-07-23 | 首版：冻结产品边界、架构与七天学习/实现/测试门禁 | 待用户确认 |
| 1.1.0 | 2026-08-03 | 明确七天内高质量完成能力矩阵全部目标，冻结范围只限制广度；第 15 节改为七天完整性审计，生产级成熟度不在本计划展开 | 用户 |
| 1.2.0 | 2026-08-03 | 补齐双向目标审计、DoD 适用性与许可证门禁；冻结 Beat→Outbox 可靠调度、SSE wire contract、SSRF 连接前校验和三类 PDF 独立验收 | 用户授权的质量完善 |
| 1.3.0 | 2026-08-03 | 冻结 Refresh/CSRF 同 successor 响应恢复、改密撤销全部 Session、四角色动作矩阵；补齐 Job lease/fencing/AOF/未启动对账、持久 Schedule 停机补跑与 D1-12 可靠异步底座 | 用户授权的安全与可靠性完善 |
| 1.4.0 | 2026-08-12 | 仅调整 Day 3～Day 6 的执行顺序：工具/行业/Text2SQL、记忆/Deep Research、知识库/入库、混合 RAG/多模态引用；技术范围、任务、测试和门禁保持不变 | 用户 |
| 1.5.0 | 2026-08-12 | 将七天计划重构为 Agent-first：补充统一 Runtime/Harness、Context/State/Memory/Checkpoint/Trace 边界、L0～L6 Deep Research 演进和轨迹评测；保留必要工程底座，压缩非 Harness 安全与外围平台篇幅 | 用户 |
| 1.6.0 | 2026-08-12 | 明确 Tool、Short/Long-term Memory、Knowledge/RAG 均属于 P0 Agent 核心栈；增加 Memory 契约与 Eval、Memory/RAG 消融门禁，并说明 RAG 仅因依赖顺序安排在 Day 6，不降低优先级 | 用户 |
| 1.7.0 | 2026-08-12 | 按能力依赖与每日学习负担均衡 Day 2～Day 7：Runtime/Harness v0 → Tool loop → Memory/Evidence/L3 → Knowledge/Durable L4 → Hybrid RAG/L5 → 综合 Eval；Scenario、Scorer、Fault 与 Agent Learning Workbench 逐日累积，保留完整前端交互并简化重复的非 Agent 安全与运维学习内容 | 用户 |
| 1.7.1 | 2026-08-22 | 同步 Day 4 五步提交、分支 CI、覆盖率例外和 `main` 尚未合并的实际状态；不改变冻结范围、架构或门禁 | 用户授权的文档收口 |
| 1.7.2 | 2026-08-22 | 同步 PR #7 合并、`main` 合并提交 CI、Trace/Eval/DoD 复核与项目所有者授权收口；D4-01～D4-07 关闭并允许进入 Day 5，保留 Day 7 前 90% 核心覆盖率债务 | 用户授权的 Day 4 收口 |
| 2.0.0 | 2026-08-25 | 冻结 Day 1～4 与 Day 5 Step 1～3 历史事实；从 Day 5 Step 4 起将后续路线收敛为 SEC 披露与财务事实核验 Agent，并扩展至 Day 10，新增官方数据/XBRL、typed calculator、point-in-time、Verifier/Monitor、公开 benchmark + SEC temporal + 中英配对评测和发布门禁 | 用户 |
| 2.0.1 | 2026-08-25 | 同步 Day 5 Step 4 工作树实现与本地门禁：固定 SEC fixture、Dense `knowledge_search`、typed calculator、Evidence lineage、F0～F2 合同对照和 Workbench；明确尚无提交/远端 CI，F0/F1 不是 live/model 质量，Step 5 仍为 planned | 用户授权的步骤实施记录 |
| 2.0.2 | 2026-08-25 | 同步 Day 5 Step 5 本地实现：成功节点 Checkpoint/CAS、FinancialScope 恢复校验、持久 HITL、同 Run resume、副作用账本、Workbench 与 L4 recovery eval；明确尚无提交/远端 CI、后台超时扫描和 Day 8 跨刷新/Worker 重启组合证据 | 用户授权的 Day 5 本地收尾 |
| 2.0.3 | 2026-08-25 | 记录 Day 5 Step 5 统一本地门禁结果：真实依赖下 Python 1018、Vitest 83、Playwright 7、构建、OpenAPI 确定性、依赖审计与 Gitleaks 通过；状态仍等待提交、远端 CI、合并和 owner 收口 | 用户授权的 Day 5 本地门禁收尾 |
| 2.0.4 | 2026-08-26 | 记录 PR #9 与分支/PR/main CI，关闭 D5-01～D5-07；保留 D5-08/D5-09 缺失 SEC fixture 浏览器全链的 DoD，不把进入 Day 6 规划授权解释为豁免。Day 6 收敛为五个纵向步骤，并冻结 canonical source version + Workspace import、双层可见性、Dense/Hybrid 演进和 `sec-source-v1` 硬门 | 用户授权进入 Day 6 文档规划 |
| 2.0.5 | 2026-08-26 | 同步 Day 6 Step 1 当前工作树：新增官方 filer catalog Adapter、canonical identity migration、解析 API/Tool、Fair Access 与本地/真实依赖证据；D6-01 为 `implemented_pending_verification`，D6-05/D6-06 为 `thin_slice`。将实际 bulk snapshot/watermark 归入消费其数据的 Step 2/4，并保留 live SEC、分支/main CI、所有者复核与 D5 浏览器 DoD 的未完成边界 | 用户授权开始 Day 6 Step 1 |
| 2.0.6 | 2026-08-26 | 同步 Day 6 Step 2 当前工作树：新增 submissions current + supplemental point-in-time 选择、不可变 response snapshot、canonical filing/coverage migration、认证 API 与 `sec.list_filings@v1`；D6-02/D6-05/D6-06 保持 `thin_slice`，明确 bulk watermark/post-watermark gap、live SEC、后续三步、分支/main CI 与所有者收口仍未完成 | 用户授权继续 Day 6 下一步 |
| 2.0.7 | 2026-08-26 | 同步 Day 6 Step 3 当前工作树：新增 bounded SEC filing archive、PostgreSQL/MinIO 不可变 snapshot 与 quarantine、复用 Knowledge/Job/Outbox 的 Workspace import、`dense-v1` search/section read 和文本 Workbench；D6-03 为 `implemented_pending_verification`，D6-05/D6-07 为 `thin_slice`，明确 live SEC、XBRL typed fact/panel、`sec-source-v1`、远端 CI 与所有者收口仍未完成 | 用户授权继续 Day 6 下一步 |
| 2.0.8 | 2026-08-27 | 同步 Day 6 Step 4 当前工作树：新增 companyfacts aggregate 与 raw iXBRL/instance typed Adapter、不可变 XBRL snapshot、PostgreSQL context/fact、`sec.get_xbrl_facts@v1`、认证 API 和 standard/raw fact Workbench；D6-04/D6-07 为 `implemented_pending_verification`，D6-05 因缺专用五 Tool Runtime/Harness profile 仍为 `thin_slice`，明确 bulk watermark/post-gap、live SEC、`sec-source-v1`、远端 CI 与所有者收口仍未完成 | 用户授权继续 Day 6 下一步 |
| 2.0.9 | 2026-08-27 | 同步 Day 6 Step 5 当前工作树：新增严格五 SEC Tool 的共享 ToolL2/Harness profile、真实 Adapter composition 校验与 24-case `sec-source-v1` manifest/scorer/report；D6-05/D6-08 为 `implemented_pending_verification`。报告 contract 18/18、closeout 4/6，并保留 submissions/companyfacts bulk watermark 两条 blocker，故 D6-02/D6-06 仍为 `thin_slice`，Day 6 gate 未关闭 | 用户授权继续 Day 6 下一步 |
| 2.1.0 | 2026-08-27 | 同步 PR #10、功能 head、push/PR/main 三层 CI 与所有者准备 Day 7 文档的指令；明确 Day 6 分支虽已结束并合入，但 `sec-source-v1` 仍为 22/24，D6-02/D6-06 未完成，不能伪写为全量 `complete`。将 Day 7 原十项任务收敛为五个纵向步骤，新增 Day 7 执行计划与 SEC Retrieval/Calculation 设计；D7-01～D7-08 保持 `planned`，代码入口受 Day 6 closeout blocker 约束 | 用户要求确认 Day 6 后先规划 Day 7 |
| 2.1.1 | 2026-08-27 | 记录项目所有者明确开始 Day 7 Step 1；将 Day 6 的 22/24 与 live SEC 债务改为 Day 10 发布硬门且保留原分母。同步 `hybrid-v1`、版本化 Retrieval Trace、真实双索引重载、SEC filing text/XBRL fact Evidence locator 与迁移的本地实现事实；D7-01 为 `implemented_pending_verification`，D7-02 因 table/cell/Citation 评测缺口保持 `thin_slice` | 用户授权开始 Day 7 Step 1 |
| 2.1.2 | 2026-08-28 | 同步 Day 7 Step 1 分支 CI 与项目所有者推进 Step 2 的指令；在唯一 Context Compiler/Tool L2 链落地 `financial-context-v1`、可信 `FinancialScope`、稳定 scope/cutoff/unit/budget 排除原因、Tool Observation identity 和 Trace/OpenAPI/Web 契约。D7-03 为 `implemented_pending_verification`，保留真实 PostgreSQL、提交/PR/main CI、Step 1 ranking/table/Citation 与 Day 6 22/24 缺口 | 用户授权继续 Day 7 下一步 |
| 2.1.3 | 2026-08-28 | 记录 Step 2 提交 `d3c88d5` 的分支 CI `33140371558` 因 Context manifest `mappingproxy` 深拷贝在 PostgreSQL Job 失败，并在当前工作树以浅层投影和回归测试修复；同步项目所有者推进 Step 3 后，正式 XBRL Evidence operand、PostgreSQL 授权重载、Decimal scale/percentage、`financial-reconciliation-v1` 和 Calculation Evidence 重算链已落地。D7-04/D7-05 为 `implemented_pending_verification`，保留本机真实 PostgreSQL、Step 3 提交/远端 CI 及既有 Day 6/Step 1 缺口 | 用户授权继续 Day 7 下一步 |
| 2.1.4 | 2026-08-28 | 同步 Day 7 Step 4 当前工作树：新增 fail-closed filing diff、`sec.diff_filings@v1`、exact six-Tool 中文 `sec-l4-v1`、SEC/Research Workbench 审计链和完整 Evidence HTTP locator；修复分支 CI `33149285431` 暴露的 ready/active PostgreSQL 测试夹具。D7-06/D7-07 为 `implemented_pending_verification`，保留真实依赖、正式浏览器、中英 paired run、新分支/PR/main CI 与既有评测缺口 | 用户授权继续 Day 7 下一步 |
| 2.1.5 | 2026-08-28 | 同步 Day 7 Step 5 当前工作树：新增可重算 10-case/30-run `sec-tool-v1` manifest、独立 observations、严格 scorer 与 JSON/Markdown；deterministic A2 对 A1 复杂题净增益 `0.833333`、简单题退化 `0`，但报告明确不是 live/model/public benchmark 且 Day 7 未收口。同步修复 `e5fb75c` CI 暴露的 XBRL unit 边界、过期 Hybrid E2E selector 与版本常量 Secret 误报；本地 Python `1081 passed, 84 skipped`、Python/Web/OpenAPI/报告生成/Gitleaks 门禁通过。D7-08 为 `implemented_pending_verification`，保留真实依赖、浏览器、中英 paired、三层 CI、owner review 和既有债务 | 用户授权继续 Day 7 下一步 |
| 2.1.6 | 2026-08-28 | 记录 PR #11、功能 head、两组通过的 PR 检查与合并提交 main CI 最终 6/7 Job 通过、Browser E2E 失败，Day 7 状态不升级。按项目所有者“先完成后续代码、Day 10 统一查漏补缺”的排期授权，将 Day 8 收敛为五个纵向步骤，新增 Day 8 执行计划和 Verifier/Monitor/恢复设计；D8-01～D8-08 保持 `planned`，Day 4～7 原债务继续作为 Day 10 发布硬门 | 用户授权进入 Day 8 文档规划 |
| 2.1.7 | 2026-08-29 | 同步 Day 8 Step 1 当前工作树：复用正式 Evidence/SEC/Calculation 链实现确定性 Claim Verifier、四种业务状态、typed issue 与 append-only PostgreSQL 报告，增加只读 API、Event/Trace/OpenAPI 和真实迁移/持久化测试；D8-01/D8-02 为 `implemented_pending_verification`。明确 graph 发射、one-revise、Workbench、frozen eval、提交/远端 CI 和 owner review 尚未完成 | 用户授权继续 Day 8 |
| 2.1.8 | 2026-08-29 | 同步 Day 8 Step 2：在唯一 Research graph/Runtime 内实现 verify、最多一次 exact-action revise 与 finalize，增加 Draft revision、Claim 重试幂等、L5 Checkpoint 和不可信输入防线；修复 PostgreSQL CI 的 Elasticsearch 冷启动 readiness/超时，并同步 Web fixture 与 L5 浏览器驱动契约。真实依赖 `1180 passed`、现有 Chromium `8 passed`，D8-01～D8-04 为 `implemented_pending_verification`，远端 CI 和 D8 专属 browser/eval/owner 证据仍待关闭 | 用户授权继续 Day 8 并要求修复 CI |
| 2.1.9 | 2026-08-29 | 同步 Day 8 Step 3：复用 Schedule/Job/Outbox、SEC sync、filing diff 与 Evidence ledger，实现版本化 Monitor/rule、append-only watermark/run、幂等 Case、双侧 Evidence 反查和 Beat/Worker 装配；将 CI 真实根因从放宽超时修正为 Elasticsearch 写入显式 `refresh=true`。本机真实依赖主门 `1186 passed`，3 个本机 endpoint 配置失败用实际 Compose endpoint 重跑通过，总体/核心覆盖率为 `80.15%`/`85%`。D8-05 为 `implemented_pending_verification`，持久订阅 HITL、Workbench、完整 fault/security suite、A2/A3/A4、远端 CI 和 owner review 仍待关闭 | 用户授权继续 Day 8 并要求解决 CI |
| 2.2.0 | 2026-08-29 | 同步 Day 8 Step 4：新增版本化 `sec-l5-v1` 七工具 Profile 和严格 `sec.monitor.subscribe@v1`，复用唯一 Research Runtime/Approval/Checkpoint/side-effect ledger，原子完成 allow 后 Monitor/Schedule 与同 Run resume，deny/timeout 零业务写；增加正式 Monitor/Case API 与刷新恢复 Workbench。保留 `sec-l4-v1` 六工具 benchmark 契约，修复 JSONB SQL NULL 兼容并验证旧审批、迁移和 `sec-tool-v1` 可复算。真实依赖 `1197 passed`，总体/核心 coverage `80.11%`/`86%`，Web `89 passed`、现有 Chromium `8 passed`。D8-06/D8-07 为 `implemented_pending_verification`，D8-08 为 `thin_slice`；Step 5、远端 CI、专用 Monitor 浏览器旅程和 owner review 仍待关闭 | 用户授权继续 Day 8 |
| 2.2.1 | 2026-08-29 | 同步 Day 8 Step 5：新增 14-case/42-run `sec-verification-v1`、独立 A2/A3/A4 scorer、冻结 observations 与 deterministic/security/fault JSON+Markdown；scorer 重算 Evidence/Citation、Scope、trajectory、stop reason 和最终数据库计数，并以负向测试拒绝伪 verified、越权动作、缺跑与重复副作用。A3 复杂题净增益 `0.714286`、简单题退化 `0`，A4 operational/recovery 为 `1.0/1.0`。修复相同逻辑事件时间触发数据库墙钟 `onupdate` 的 Runtime 顺序误判，并以 PostgreSQL 回归锁定；最终本地门禁为真实依赖 `1207 passed`、总体/核心 coverage `80.21%`/`86%`、Web `89 passed`、Chromium `8 passed`，依赖审计和完整历史 Gitleaks 通过。报告明确不是 live SEC/model，Day 8 因专用浏览器、真实 Monitor hard-stop 注入、三层远端 CI 与 owner review 保持未完成 | 用户授权继续 Day 8 最后一步 |
| 2.2.2 | 2026-08-29 | 记录 Day 8 PR #14 与功能 head、push/PR/main 三层 CI 全绿；按项目所有者指令进入 Day 9，新增不超过五步的执行计划。Step 1 先建立正式 `evaluation` registry/release manifest/schema 和许可/hash fail-closed 合同，再依次接入固定 benchmark、SEC temporal/中英配对、受限与动态数据、A0～A4 收口；文档阶段 D9-01～D9-08 仍为 `planned` | 用户授权进入 Day 9 并开始 Step 1 |
| 2.2.3 | 2026-08-29 | 同步 Day 9 Step 1 当前工作树：在正式 `evaluation` bounded context 新增严格 Dataset Registry、release manifest、版本化 JSON Schema 和唯一生成入口；固定四个公开数据集 revision、11 个 artifact byte size/SHA-256、数据/代码许可与允许用途，并以负例拒绝浮动版本、gold 泄漏、许可越权、跨 split/future source 和不完整引用。聚焦测试 19 passed、模块 branch coverage `86.18%`；Ruff/mypy/Web、现有 Chromium `8 passed`、依赖审计、历史 Gitleaks 和五个真实依赖下 `1226 passed` 均通过，总体/既有核心 coverage `80.29%`/`86%`。四项仍为 `registered_only`，无 payload/Adapter/分数/远端 CI，故仅 D9-01 为 `implemented_pending_verification` | 用户授权开始 Day 9 Step 1 |
| 2.2.4 | 2026-08-30 | 同步 Day 9 Step 2：复用正式 `evaluation` 和受控公网 egress，实现 registry 驱动的 FinQA/TAT-QA materializer、统一 sanitized case、两个独立官方口径 scorer、dataset card 与 contract-only 分报。全量 split/hash/case digest 通过；FinQA 1147 test program 与官方执行器、TAT-QA 200 oracle case 与官方 scorer 均 0 mismatch。明确 TAT pinned test input/gold UID 零重合，使用 released gold 自带 context 安全拆分而不强连；两个数据集升为 `adapter_ready` 但继续 `release_eligible=false`。聚焦测试 29 passed，全量无强制真实依赖 pytest `1148 passed, 88 skipped`，Ruff/mypy、构建、OpenAPI/Web、依赖审计与现有 Chromium `8 passed` 均通过；真实依赖强制重跑、真实模型 run、远端 CI、Run/Trace/Evidence binding 与 owner review 仍缺，D9-02/D9-03 为 `implemented_pending_verification` | 用户授权继续 Day 9 下一步 |
| 2.2.5 | 2026-08-30 | 同步 Day 9 Step 3：新增内部 `sec-temporal-v1` manifest/生成器/schema/dataset card/contract-only 报告，复用 release Budget/Trajectory/SEC/Answer gold，将 30 个单 gold 中英 pair 展开为 60 case。11 个真实 accession 的 22 个 HTML/XBRL artifact 固定 SEC URL/size/SHA-256 并按 filing 隔离 split；八类分母为 `10/8/10/8/6/6/6/6`，本地 22/22 artifact、35/35 Evidence、pair identity 100%、future leakage 0。明确没有模型/Runtime 执行、真实 Run/Trace/Evidence binding 或 capability score，10 组中文抽样尚未签字，远端 CI/owner review 亦缺，故 D9-05/D9-06 仅为 `implemented_pending_verification` | 用户授权继续 Day 9 下一步 |
| 2.2.6 | 2026-08-30 | 同步 Day 9 Step 4：沿正式 registry/受控物化实现 FinanceBench 与 FinSearchComp 严格 Adapter/dataset card/schema/contract report；前者冻结 150 题/84 引用文档/189 Evidence 并保留未引用 metadata period 冲突，后者将 391 historical 与 244 dynamic 分报，单列 203 AkShare-compatible、41 专业依赖和 203 timestamp drift。新增复用 release Budget/Trajectory 的 6-case/18-trial `agent-security-v1`，规则 scorer 重算 argument/partial-order/stop/final-state、注入、跨 Workspace、越权、重复副作用、恢复和经验 `pass^3`。本地 evaluation `50 passed`、两套新增模块 branch coverage `84%`、全量无强制外部服务 pytest `1169 passed, 88 skipped`，Ruff/mypy 通过；四个外部数据集均为 `adapter_ready` 但 `release_eligible=false`，真实依赖总体 coverage、Runtime/model/database/judge、远端 CI 和 owner review 未完成，D9-04/D9-07 仅为 `implemented_pending_verification` | 用户授权继续 Day 9 下一步 |
| 2.2.7 | 2026-08-30 | 同步 Day 9 Step 5：新增严格 `release-suite-v1` 聚合器，校验 registry/release manifest 与九份受检报告身份/hash，生成 deterministic/offline/live/failure-taxonomy JSON+Markdown/schema。保留同一 source-suite 内 A1→A2、A2→A3、A3→A4 operational 决策，但全局 A0～A4 分数与生产默认策略为 null；公开集 prediction、live 3 次、Recall@5、Runtime/Trace/Evidence、远端 CI 与 owner review 缺失均为机器 blocker。本地 evaluation `57 passed`、新增模块 branch coverage `91%`、全量无强制服务 pytest `1176 passed, 88 skipped`，Ruff/mypy/Web `89 passed`/build/OpenAPI/Chromium `8 passed`、依赖审计与 96-commit Gitleaks 通过；真实依赖总体 coverage 与远端 CI 尚缺。D9-08 为 `thin_slice`，不把聚合报告冒充模型能力或 Day 9 完成 | 用户授权继续 Day 9 下一步 |
| 2.2.8 | 2026-08-30 | 记录 Day 9 PR #15、功能 head `6a79e4a`、合并提交 `4500505` 及 push/PR/main 三层 CI 全绿；按项目所有者指令进入最后一天并先完成文档规划。将 Day 10 原十项任务收敛为五个证据驱动步骤：发布台账、真实中文闭环、评测/可观测/安全、工程恢复门禁、最终审计与候选发布；新增 Day 10 学习日志和发布就绪合同。当前 D10-01～D10-08 仍为 `planned`，Day 1～9 所有未关闭项继续作为发布硬门 | 用户授权进入 Day 10 文档规划 |
| 2.2.9 | 2026-08-30 | 同步 Day 10 Step 1：在既有 `evaluation` bounded context 新增严格 release readiness manifest/生成器、checked JSON/Markdown 与 manifest/report Schema；从能力矩阵十张正式表提取 88 个目标并锁定 digest/状态计数，将每项目标映射至 owner、依赖日、验证命令和 30 个带 SHA-256 artifact。9 个 Day 9 taxonomy blocker 必须双向全覆盖，另登记 7 个跨 Day blocker；当前 45 complete、43 未完成、16 个 open blocker、5 个 pending external gate，结论 fail closed 为 `no_go`。聚焦/evaluation 为 `8/65 passed`，readiness branch coverage `84%`，全量 `1184 passed, 88 skipped`，Python/Web/构建/OpenAPI/audit/Gitleaks 通过；真实依赖、Chromium 与远端 CI 未执行。D10-07 仅为 `thin_slice`，不把台账存在写成发布就绪 | 用户授权开始 Day 10 Step 1 |
| 2.2.10 | 2026-08-31 | 同步 Day 10 Step 2：复用正式 SEC/Research/Verification/Evidence/Approval/Monitor/Case 链，从 ready Filing import 生成 typed scope 草稿并预填唯一 Research Runtime；新增 Verification Report generated-client wrapper，Workbench 只按服务端报告展示四态、Claim/Citation/Calculation/issue/Evidence，并对 active/paused Run 自动重建，非金融 Run 不请求金融报告。amendment 不静默降级；Case Evidence 可反查。readiness 扩展为 41 个 hash artifact，当前 45 complete、32 implemented pending verification、7 thin slice、4 planned，16 blocker/5 pending gate 与 `no_go` 不变。Web `94 passed`、相关 Python `37 passed`、TypeScript/ESLint 通过；无拦截真实依赖 Playwright、受控 SEC source、Worker 恢复、远端 CI 和 owner review 仍缺，故 D10-01/D10-02 为 `implemented_pending_verification`，D10-04 为 `thin_slice` | 用户授权继续 Day 10 Step 2 |
| 2.2.11 | 2026-08-31 | 同步 Day 10 Step 3：在唯一 evaluation bounded context 新增 checked `sec-release-evidence-v1`，引用同一 10-case/gold/budget 冻结 A0～A4，要求 offline 50 或 live 150 个 Run 绑定 Trace/Evidence/Calculation/final state/ranked candidates，并重算 Recall@5、Citation、拒答、freshness、Workspace、注入、未授权写、重复副作用与恢复指标/告警。当前 checked observation 为 `not_executed`/0 of 50，11 指标均 `not_measured`、告警 `unknown`，production default 为 null；release suite 只把 common manifest 缺失精确改为 production Runs 缺失，9 个 evaluation blocker、16 个总 blocker、5 个 external gate 与 `no_go` 不变。readiness 登记 49 个 artifact，状态 45 complete、32 implemented pending verification、8 thin slice、3 planned；聚焦 22 passed、evaluation 72 passed、后端 1191 passed/88 skipped、Web 94 passed，真实 Runtime/public/live/治理/远端证据仍缺，故 D10-04/D10-05/D10-07 仅为 `thin_slice` | 用户授权继续 Day 10 Step 3 |
| 2.2.12 | 2026-08-31 | 同步 Day 10 Step 4：冻结核心模块集合达到并在 CI 强制 90%，保留后端 80%/关键 Web 75%；锁定 Semgrep、Python/Node license scanner 与 NOTICE 门禁。新增 checked `sec-release-recovery-v1`、12 场景 manifest、0/12 unexecuted observation、报告/schema 和 staging Runbook；未把测试或命令冒充演练。readiness 登记 63 个 artifact，状态为 45 complete、33 implemented pending verification、9 thin slice、1 planned；16 blocker/5 external gate 与 `no_go` 不变，D10-03/D10-06 分别为 `implemented_pending_verification`/`thin_slice` | 用户授权继续 Day 10 Step 4 |
