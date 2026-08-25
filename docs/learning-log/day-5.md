# Day 5 执行计划：Knowledge 基础与 SEC Fixture Durable Research L4

> 制定日期：2026-08-23
>
> 更新日期：2026-08-25
>
> 计划基线：[Day 1～Day 10 主计划](../master-plan.md) 2.0.0 Day 5
>
> 能力边界：[Day 1～Day 10 目标能力矩阵](../feature-matrix.md) D5-01～D5-09
>
> 相关架构：[系统架构](../architecture.md)第 3、5、6、10.3、11、12、15.2、15.4、15.6、18 节，[ADR 0002](../adr/0002-postgresql-source-of-truth.md)、[ADR 0003](../adr/0003-unified-evidence-model.md)、[ADR 0004](../adr/0004-celery-redis-background-jobs.md)、[ADR 0005](../adr/0005-langgraph-research-only.md)、[ADR 0007](../adr/0007-sec-disclosure-financial-fact-verification.md)
>
> 当前状态：Step 1～3 已在 `feat/day5-knowledge-ingestion-step1` 实现且当前 head 分支 CI 通过，但尚未合入 `main`；D5-01～D5-07 为 `implemented_pending_verification`，D5-08 为 `thin_slice`，D5-09 与 Step 4～5 为 `planned`。分支实现不等于 Day 5 `complete`。

## 1. 当前门禁与今日边界

Day 5 从 Day 4 已完成的 Memory、Observation→Evidence→Claim、ResearchBrief、唯一 `ResearchL3Runtime` 和正式 Workbench 继续演进，不重写 Runtime、Tool loop、Evidence 账本或 Research graph。Step 1～3 建立通用私有文档入库底座；Step 4～5 在冻结 SEC filing fixture 上打通 Dense `knowledge_search`、确定性 `finance.calculate@v1`、可定位 Evidence 和可中断/审批/恢复的 L4，为 Day 6 官方 SEC 来源与 Day 7 双通道检索提供可验证起点。

正式链路固定为：

```text
知识库创建 → 私有短签名上传 → complete
→ PostgreSQL 原子创建 DocumentVersion + Job + Outbox
→ Dispatcher → Worker 分阶段解析/资产/Chunk/Embedding/双索引
→ ready/active SEC fixture version → Dense knowledge_search
→ FinancialScope + finance.calculate@v1
→ Observation/EvidenceCandidate → filing/number/formula Evidence locator
→ 同一 Research graph → Agent Checkpoint/Interrupt/HITL/Resume
→ Workbench/Eval/Trace
```

今日必须守住以下边界：

- PostgreSQL 保存 Knowledge、Job、Checkpoint、Approval 和 Research 业务事实；MinIO 保存私有字节，Milvus/Elasticsearch 只是可重建索引。
- Document 只有 vector 与 lexical 两类索引写入都成功后才能进入 `ready`；Day 5 查询只启用 Dense baseline。
- 官方 SEC 在线接入属于 Day 6；BM25 查询、RRF、rerank 与 XBRL+filing 双通道属于 Day 7；Verifier、bounded revise 和监控属于 Day 8，不提前混入。
- Agent Checkpoint 与 ingestion stage checkpoint 是两种 typed 状态机，不共用 revision、无类型 state JSON 或恢复入口。
- LangGraph 仅是现有 Research graph 的内部编排适配器；普通 CRUD、文档入库和 Tool loop 不进入 LangGraph。
- Worker hard stop、重复 resume、审批重复提交和 Celery 重投都必须收敛到同一业务结果，不宣称 exactly-once。
- 跨刷新与 Worker 重启的组合恢复在 Day 5 实现基础链并保留证据；包含官方 SEC、双通道、Verifier 和 Monitor 的发布级组合回归在 Day 10 统一关闭。

已登记但不阻断 Day 5 的历史债务保持不变：Day 4 核心 Domain/Application/Research workflow 合集覆盖率为 85%，须在 Day 10 总门禁前补到 90%；D1-09 的 6 组参考仓历史凭据候选仍阻断 Day 10 发布标签。

历史环境预检记录：本计划创建时仓库 `.nvmrc`/`package.json` 固定 Node `24.16.0`，当时终端为 Node `24.19.0`、pnpm `10.10.0`。该记录不否定已取得的远端分支 CI；Step 4 及后续本地完整门禁必须切换到 Node `24.16.0`，否则只能记录非锁定环境的局部结果，不能表述为可复现的 Day 5 验收通过。

### 1.1 当前分支证据

| 步骤 | 提交 | 当前证据 | 尚缺门禁 |
|---|---|---|---|
| Step 1 | `bba63e6` | 私有上传 acceptance 切片；对应分支 CI 已通过 | 与后续步骤一起合入 `main`、合并 CI、Day 5 DoD/owner 收口 |
| Step 2 | `ad57073`，CI 修复 `adec643` | 版本化解析资产切片；修复后的分支 CI 已通过 | 同上；失败的旧提交不能冒充通过证据 |
| Step 3 | `4daa028` | Embedding、双索引写入、删除对账与 Workbench 切片；当前 head CI `32796096690` 通过 | `main` 合并、完整故障/恢复门禁、Day 5 DoD；Dense 查询和 Tool/Evidence 由 Step 4 关闭 |

这些证据只支持 `implemented_pending_verification`/`thin_slice`。当前分支未合入 `main`，没有 `main` 合并提交 CI，也没有 Day 5 全量 Trace/Eval/DoD 与项目所有者验收记录。

## 2. 现有实现与复用边界

当前分支已经包含正式 `knowledge`、`ingestion` 与索引写入相关实现，但只覆盖 Step 1～3；`retrieval` 查询、SEC 领域模型、财务计算 Tool 和 L4 用户闭环仍不存在。后续按真实职责扩展现有模块，不复制已有机制，也不把索引写入冒充可检索或可核验能力。

| 现有能力 | 必须复用 | Day 5 扩展 | 禁止做法 |
|---|---|---|---|
| 私有文件 | `FileApplicationService`、`PrivateFileObjectStore`、`FileObject`、MinIO 短签名 URL、服务端 complete 校验 | Knowledge 上传复用同一 FileObject 与对象存储合同；增加 PDF/TXT/Markdown 知识文件规则 | 新建第二套上传服务、公开 Bucket、信任浏览器 MIME/大小/hash |
| 附件解析 | `AttachmentParserPort`、`BoundedAttachmentParser` 的有界校验与安全模式 | 新建版本化 `DocumentParser -> ParsedDocument` Port/Adapter，支持页面、OCR、图片和复杂表格资产 | 把聊天附件 parser 直接扩成万能解析器，或在 API 请求内解析 |
| 可靠任务 | `JobApplicationService`、Outbox Dispatcher、`JobExecutionRuntime`、lease/fencing、`JobReconciler` | 增加入库 Job handler、独立 stage checkpoint、确定性外部 ID、补偿和对账 | FastAPI `BackgroundTasks`、进程内队列/dict、Celery result backend 作为真相 |
| Agent Checkpoint | `CheckpointEnvelope`、`CheckpointStore` Port、schema version、revision/CAS 与不兼容拒绝 | 将 `ResearchGraphState` 映射为可恢复 Agent State，补齐持久 Store、resume token 和事件 | 用 Trace、`research_runs.state`、Job retry 或入库 stage 状态冒充 Agent resume |
| Tool 与审批 | `ToolRegistry`、`ToolExecutor`、`ApprovalRequest/Decision` typed contract、side-effect idempotency key | 持久化审批请求/决定和副作用账本，恢复时先查已有结果 | 模型决定审批结果、Graph 节点绕过 ToolExecutor、重复 decision 重复执行 |
| Research L3 | `ResearchL3Runtime`、`ResearchGraphState`、统一 AgentRun/Step/Event/Budget、Evidence/Claim 服务 | 在同一 graph/runtime 上增加 L4 checkpoint/interrupt/resume；不创建第二 graph | 新建 research_steps/events/checkpoints、图外手工跑节点、节点直连 Provider |
| Evidence | `EvidenceApplicationService`、Normalizer、typed locator、当前 Workspace/资源重授权 | 增加 DocumentVersion/Chunk/Asset locator 与 Knowledge 来源类型 | 把 retrieval result、模型摘要、URL 或 `[S1]` 直接写成 Evidence |
| Workbench/Eval | `ChatWorkbench`、`TracePanel`、`EvidenceWorkspace`、`ResearchWorkspace`、Scenario/Fake/Replay/Scorer | 增加 Knowledge locator、ingestion、Checkpoint/HITL 与恢复视图 | 前端保存第二份业务事实或仅用 Mock trajectory 展示 |

Day 2 的聊天附件完成语义必须保持兼容：`FileObject ready` 只表示该私有对象已经通过附件合同并可供现有会话使用，不表示知识文档已经解析和建立双索引。Day 5 的 Knowledge 专用 Application Service 复用文件存储、服务端校验和事务模式，独立管理 `DocumentVersion` 的 queued/processing/ready/deleting/deleted 状态；不能全局改写现有 `/files/{id}/complete` 的含义，也不能让附件状态替代知识入库状态。

如果 Day 5 实现需要改变技术栈、数据所有权、模块职责、Research graph 公共语义或安全边界，必须先更新相应 ADR 和主计划；按既有决策实现时不为形式新增 ADR。

## 3. 五个可验收步骤

| 步骤 | 主要能力矩阵 | 纵向用户结果 | 关闭重点 |
|---|---|---|---|
| 1. 知识库、私有上传与异步受理 | D5-01、D5-02、D5-05 的受理部分 | 创建知识库 → 私有上传 → 服务端 complete → DocumentVersion/Job/Outbox → 立即返回并可刷新查看 | 事实模型、权限、上传合同、原子创建与 queued/validating 状态 |
| 2. 版本化解析与可追溯资产 | D5-03、D5-04、D5-05 的解析部分 | 数字 PDF、扫描 PDF、图表 PDF、TXT/Markdown → ParsedDocument → Page/Chunk/Asset | Parser contract、页码/bbox/hash、OCR/图片/复杂表格、阶段恢复 |
| 3. 双索引 ready、删除对账与文档 Workbench | D5-05、D5-06、D5-07，D5-08 的 Embedding/index-write 部分 | Embedding → vector/lexical index → ready；版本切换、删除和故障可见 | 两类索引门禁、确定性 ID、重投幂等、跨存储补偿、详情界面 |
| 4. SEC Fixture Knowledge、计算与 Evidence 闭环 | D5-08 | 固定 accession fixture → `knowledge_search` → `FinancialScope` → `finance.calculate@v1` → Evidence/Claim | Dense baseline、数值/单位/期间/公式、稳定失败语义、Context manifest 与 F0～F2 fixture 对照 |
| 5. SEC Fixture Durable Research L4、HITL 与收口 | D5-09 与全部 Day 5 门禁 | hard stop → 最后成功 Checkpoint → approve/deny/timeout → resume → 零重复副作用 | 同一 Run 恢复、持久审批、SEC locator Workbench、Scenario/Eval、DoD 与 CI |

### 步骤 1：知识库、私有上传与异步受理闭环

交付一个真实用户能够完成的最小知识入口：创建知识库，选择 PDF/TXT/Markdown，通过私有短签名 URL 上传，服务端重新验证对象后立即创建 Document、DocumentVersion、Job 和 Outbox，并返回可查询的受理状态。HTTP 请求不等待解析。

验收条件：

- 用户可以创建、列表、查看、编辑和删除自己 Workspace 的知识库；文档计数来自正式 PostgreSQL 关系，刷新后不丢失。
- 上传使用不可猜测 object key 和私有 Bucket；文件名只作已消毒显示元数据，签名参数限制 key、大小、Content-Type 和有效期。
- complete 重新检查对象存在、实际大小、SHA-256、metadata、扩展名/MIME/magic bytes；伪类型、损坏、加密、超限和跨 Workspace 请求明确失败。
- 同一个 client request/Idempotency-Key 或重复 complete 不产生重复 DocumentVersion、Job 或 Outbox；业务资源与可靠任务在同一 PostgreSQL 事务创建。
- API 返回 `202 + document/version/job/events`，页面能在刷新后显示 uploaded/queued/validating，而不是把“已上传”显示为“可检索”。
- Alembic fresh upgrade、复合外键/唯一约束、OpenAPI 生成、领域/API/真实 PostgreSQL/MinIO/组件和最小浏览器旅程通过。

本步必须保留的证据：一条成功受理记录、一条伪类型或超限失败记录、一条跨 Workspace 拒绝记录，以及重复 complete 仍只有一个业务结果的数据库证据。

本步不实现 Parser/OCR、Chunk/Asset、Embedding、索引查询或 Research resume；相关状态只能保持不可检索。

### 步骤 2：版本化解析与可追溯资产闭环

在 Worker 内通过版本化 `DocumentParser -> ParsedDocument` 执行验证、解析、OCR、资产抽取和 Chunk。`DocumentVersion` 固定 parser/chunker 配置；Page、Chunk、图片、表格和表格 HTML/截图均保留页码、标题路径、bbox、content hash 与版本关系。

验收条件：

- 至少使用五类独立 fixture：数字文本 PDF、需要 OCR 的扫描 PDF、同时含图片和复杂表格且至少 20 页的 PDF、TXT、Markdown。
- 数字 PDF 能断言文本与页面 locator；扫描 PDF 能断言 OCR 输出；图表 PDF 能断言图片、复杂表格 HTML/截图、页码、bbox 和 Chunk/Asset 关联。
- Parser Port 固定 schema/parser version、输入/输出预算、timeout 和稳定错误；具体解析/OCR SDK 只出现在 Adapter，Research/Knowledge Service 不直接依赖 SDK。
- validating、parsing、extracting_assets、chunking 使用独立 ingestion stage checkpoint、阶段幂等键和 fencing token；Worker 重投或阶段 hard stop 不产生重复 Page/Chunk/Asset。
- 损坏、加密、页数/文本量/图片像素/输出大小超限、Parser timeout、依赖缺失和取消都有可重试或不可重试分类及用户可见错误。
- Parser 升级创建新 DocumentVersion，不能覆盖旧 locator；旧版本可追溯但不会自动成为 active version。
- 领域、Parser contract、真实 Worker/PostgreSQL/MinIO 集成、故障注入、权限和前端阶段/资产预览测试通过。

本步必须保留的证据：三类 PDF 的解析摘要与 locator、至少一个阶段 hard-stop 恢复记录、一个不可重试解析失败记录，以及重复投递零重复资产的断言。

本步不宣称文档 `ready`，也不实现 Dense/BM25 查询；完成解析不等于完成索引。

### 步骤 3：双索引 ready、跨存储删除与文档 Workbench 闭环

定义独立版本化 `EmbeddingProvider` Port/Adapter 和索引 Port，完成 embedding、vector_indexing、lexical_indexing 两类写入。Milvus/Elasticsearch 使用 `chunk_id:index_version` 等确定性外部 ID；只有两类索引记录都成功后，DocumentVersion 才进入 `ready` 并可切换为 active。Day 5 的 Elasticsearch 只完成 lexical index 写入，不开放 BM25 查询。

验收条件：

- Embedding contract 固定 provider/model、dimension、normalization、batch、timeout、版本和稳定错误；确定 Fake 下同输入产生可重复索引记录，正式 Adapter 未配置时明确失败。
- vector 与 lexical 任一缺失时状态为 partial/not ready，不能进入 `ready`；索引结果、attempt、外部 ID 和 error 均有 PostgreSQL 记录。
- Worker 在 embedding/vector/lexical 各阶段 hard stop、超时、重复消息、ACK 丢失或旧 fencing token 到达时可安全重试，重复 Chunk、Embedding、索引和 Artifact 数为 0。
- 删除按 `deleting → Milvus/ES/MinIO/缓存清理 → deleted` 推进；任一外部删除失败可重试，对账器能发现遗漏和孤儿，删除后旧索引不再被新查询使用。
- 新版本 ready/active 后，旧版本仍可追溯但不作为新 Run 的 active Context；切换和回滚都有版本检查与审计。
- Knowledge/Document 页面展示列表、状态、错误、版本、阶段、Chunk、页面、图片、表格截图/HTML、重试、取消、reindex 和删除；刷新从正式 API/Job 事实重建。
- 真实 PostgreSQL、Redis、MinIO、Milvus、Elasticsearch 集成，以及组件、OpenAPI、SSE/Job 状态和浏览器测试通过。

本步必须保留的证据：代表性文档进入 `ready` 的双索引记录、一条索引依赖失败记录、一条 hard-stop/重投恢复记录、一条跨存储删除与对账记录，以及从 DocumentVersion 导航到 Chunk/Asset 的浏览器证据。

本步不开放 BM25 查询、RRF、rerank 或 Hybrid 搜索；索引写入能力不能被表述为 Day 6 RAG 已完成。

### 步骤 4：SEC Fixture Knowledge、计算与 Evidence 闭环

在既有 `ToolRegistry`/`ToolExecutor` 和 Harness profile 中注册版本化 `knowledge_search` 与 `finance.calculate@v1`，并实现 `KnowledgeContextSource`。首个数据包只使用经来源/许可复核、固定 accession/form/filing time/hash 的 SEC `10-K/10-Q` fixture，不访问 live SEC。Dense candidate 返回后必须回 PostgreSQL 重新加载和授权，再由现有 Evidence Normalizer 形成 filing/number/formula 可追溯的 Evidence/Claim。

验收条件：

- `FinancialScope` 至少固定 CIK、accession、form、report period、`as_of`、unit/scale；模型不能改变公司、申报、截止时点、WorkspaceScope、KB allowlist、Top-K 或预算。
- 只返回当前 Workspace 的 ready/active fixture version；跨 Workspace、错误 accession、cutoff 后、旧版本、deleted、partial_index 和 failed 文档召回数为 0。
- 明确区分 `not_ready`、`no_result`、`partial_index`、`dependency_failed`、`permission_denied`、`ambiguous_filer` 和 `period_mismatch`；任何一种都不能回退为 Fake success 或伪造 Evidence。
- filing locator 至少包含 CIK、accession、form、filed/accepted time、document/version/chunk、section/page、content hash 和 parser/chunker/index version；数值 locator 还包含 period、unit/scale 与输入 Evidence refs。
- `finance.calculate@v1` 只接受 allowlisted operator、Decimal 输入、兼容 unit、rounding policy 和 Evidence refs；保存公式与输出 lineage，拒绝任意代码、无来源数字、单位冲突和除零。
- `knowledge_search`、calculator 与 `KnowledgeContextSource` 复用统一 Tool loop/Context Compiler；Research graph 不改 Runtime 即可消费 Observation。
- 至少覆盖一条原文事实问答、一条派生计算和一条证据不足问题，形成“Dense candidate → Observation → typed calculation → Evidence/Claim → supported/uncertain L3 draft”链路；Day 8 Verifier 的四类最终业务状态不在本步实现。
- F0 fixture oracle/full-context、F1 fixture Dense RAG、F2 F1+calculator 的机器可比较报告分别给出来源、数值、公式、Evidence 支持、Token、费用和延迟，不能只报告向量分数或最终答案。F0～F2 仅是 Day 5 合同对照，不占用 Day 7～Day 8 正式 A0～A4 的语义。

本步必须保留的证据：成功 Dense/计算 Trace、no-result 或 period-mismatch Trace、跨 Workspace 与 cutoff 后零召回记录，以及 F0～F2 JSON/Markdown 报告。

本步不实现 live SEC Adapter、XBRL 结构化通道、BM25/RRF/rerank、Verifier、bounded revise 或 Monitor；这些分别属于 Day 6～Day 8。

### 步骤 5：SEC Fixture Durable Research L4、HITL 与 Day 5 收口

将包含 `FinancialScope`、SEC fixture Evidence 和 calculation refs 的 `ResearchGraphState` 映射到统一版本化 Agent State/`CheckpointEnvelope`，在每个安全节点完成后用 expected revision/CAS 保存 Checkpoint。增加持久 `ApprovalRequest/Decision`、resume token、副作用账本和恢复 Application Service/API/SSE；重复 resume 继续同一 AgentRun，并从最后成功节点恢复。

验收条件：

- Checkpoint 保存 graph/state schema version、run/workspace、`FinancialScope`、Brief/Plan/current node/pending actions、filing/calculation Evidence/Claim/Artifact refs、Budget、审批、取消、stop metadata 和脱敏错误摘要；不保存 Secret、完整 filing 正文或原始 chain-of-thought。
- 每次 load/resume/approve/deny 都重新验证当前 Principal、Workspace membership、capability、Budget 和 Tool policy；创建时权限快照不能代替当前授权。
- 旧 revision、错误/重复 resume token、重复 decision、跨 Workspace、已终态 Run 和不兼容 schema 明确拒绝或执行已评审迁移，不能静默从头重跑。
- 外部副作用严格执行“持久化意图/幂等键 → 执行 → 持久化结果”；恢复先查询账本，Tool Call、计算结果、知识写入和 Artifact 重复数为 0。
- allow 从同一 Checkpoint 继续，deny/timeout 形成稳定终态或受控分支；取消、deadline、max steps、Token/费用预算在 pause/resume 前后持续生效。
- Research 主要节点被强制 hard stop 后，Reconciler/Worker 从最后成功 Checkpoint 恢复；Celery redelivery 不新增重复 Agent Step，Runtime retry 仍产生可观察 Step/Event。
- Workbench 展示 Checkpoint revision/state diff、暂停原因、ApprovalRequest/Decision、恢复位置和结果；刷新后从正式 API/Event/Trace 重建，并能沿“SEC fixture/accession → Chunk → 数值/公式 Evidence → Research Step → Checkpoint”导航。
- 保留 Day 4 已冻结的 50 条 Scenario，不用“数量已超过 30”替代 Day 5 覆盖；新增版本化 Day 5 场景覆盖入库故障、旧版本/删除、Knowledge 失败语义、hard stop、重复 resume、allow/deny/timeout、取消和预算。
- 形成 ingestion reliability、SEC fixture Knowledge/calculation/Evidence、L3/L4 runtime recovery 三类独立报告；规则/确定性 Scorer 与人工抽样同时存在，LLM judge 不是唯一判据。
- 全量 format/lint/type、fresh migration、OpenAPI/SSE/typed contract、真实依赖、权限负向、组件、关键 E2E、覆盖率、依赖/许可证、Secret、隐私和回滚检查通过。

本步必须完成 `docs/ingestion-state-machine.md`、统一 Agent Checkpoint/L4 合同、Day 5 运行与回滚说明，并按实际证据更新 `docs/agent-runtime.md`、`docs/research-state-machine.md`、能力矩阵、README 和本文验收记录。

步骤 5 通过仍不等于可直接标记 Day 5 `complete`。只有功能分支 CI、合入 `main`、合并提交 CI、双向能力映射、正式 Trace/Eval/DoD 复核和项目所有者验收全部完成后，才能统一关闭 D5-01～D5-09 并开始 Day 6 官方 SEC 来源接入。

## 4. 步骤状态与转换规则

| 步骤 | 当前状态 | 关闭条件 |
|---|---|---|
| 1. 知识库、私有上传与异步受理 | `implemented_pending_verification` | `bba63e6` 已实现且分支 CI 通过；尚缺 `main` 合并 CI、Day 5 DoD 和 owner 收口 |
| 2. 版本化解析与可追溯资产 | `implemented_pending_verification` | `ad57073`、CI 修复 `adec643` 已实现且修复后分支 CI 通过；尚缺最终合并门禁 |
| 3. 双索引 ready、删除对账与文档 Workbench | `implemented_pending_verification` | `4daa028` 与 head CI `32796096690` 已通过；Dense query/Tool 不属于本步已实现证据 |
| 4. SEC Fixture Knowledge、计算与 Evidence | `planned` | Dense `knowledge_search`、`FinancialScope`、calculator、F0～F2 尚未实现 |
| 5. SEC Fixture Durable Research L4、HITL 与收口 | `planned` | Checkpoint/HITL/resume、L3/L4 recovery 与 Day 5 总门禁尚未实现 |

状态只随证据推进：

- 只有契约、局部实现或部分真实链路时，按事实保持 `planned`、`contract_only` 或 `thin_slice`。
- 实现完成并通过该步本地统一门禁，但尚未完成远端/所有者验收时，使用 `implemented_pending_verification`。
- 功能分支 CI 通过只关闭分支验证缺口，不等于已合入 `main`，也不等于 `complete`。
- 只有 `main` 合并提交 CI、适用 DoD、正式 Trace/Eval、双向矩阵映射和项目所有者复核全部通过后，才可标记 `complete`。
- 页面截图、Mock success、单条漂亮答案、只证明索引返回结果或只证明 state 行存在，都不能关闭任何步骤。

任一步的关闭记录必须追加在本文，至少包含提交/工作树范围、迁移、OpenAPI/SSE、单元/集成/组件/E2E、真实依赖、Scenario/Eval、覆盖率、安全/许可证、Trace、限制、回滚和复核人。保留各步骤当时的历史事实，不用后续结果覆盖原始时点。

## 5. Scenario、Eval 与学习计划

### 5.1 数据集和故障矩阵

Day 4 已有 50 条累计 Scenario，但它们只证明通用 Runtime/Memory/Evidence/L3 回归，不证明 SEC 或财务能力。Day 5 必须保留既有数据集和报告，不重写基线，并新增版本化场景：

- ingestion：重复 complete、重复 Job、解析/OCR/资产/Chunk/Embedding/vector/lexical 各阶段失败或 hard stop、取消、旧 fencing token、依赖恢复；
- lifecycle：旧版本、active 切换、partial_index、跨存储删除失败、对账修复、删除后查询；
- SEC fixture knowledge：正确/错误 accession、ready/active、cutoff、not_ready、no_result、partial_index、dependency_failed、permission_denied、period mismatch、跨 Workspace 和 Context budget；
- calculation：事实抽取、加减乘除、比率/变化率、unit/scale、rounding、公式 lineage、无来源输入、单位冲突和除零；
- research L4：带 `FinancialScope` 的节点 hard stop、Checkpoint CAS 冲突、schema 不兼容、重复 resume/decision、allow/deny/timeout、取消和预算耗尽；
- 对照：F0 fixture oracle/full-context、F1 fixture Dense RAG、F2 F1+calculator，以及 L3 从头失败与 L4 从 Checkpoint 恢复。

Parser/Embedding 的 contract fixture、入库可靠性 case 和 Agent Scenario 必须分别计数和报告，不能把文件样本数量冒充 Agent Scenario 数量。

### 5.2 评分与报告

至少形成以下相互独立、可机器比较的报告：

| 报告 | 核心指标 |
|---|---|
| Ingestion reliability | 阶段完成率、恢复结果、重复资源数、取消、耗时、依赖错误分类、对账结果 |
| SEC Fixture Knowledge/Calculation/Evidence | filing identity/cutoff、ready/active 过滤、跨租户召回、locator/公式可解析、数值/单位/期间正确性、拒答和 Token/费用/延迟 |
| SEC Fixture Research L4 recovery | `FinancialScope` 保持、Checkpoint 恢复成功、重复 resume/decision、重复 Tool/计算/Artifact 数、stop reason、步骤/Token/费用/延迟变化 |

所有报告固定 dataset、accession/snapshot hash、scenario、runtime/harness/tool/context/parser/chunker/embedding/index/graph/checkpoint/scorer version，并保存 deterministic fixture refs。固定/replay 不能表述为 live SEC 或真实 Provider 质量。

### 5.3 当日学习问题

编码前写下并在每步验收后回答：

1. Knowledge Base 为什么既可以提供 Tool，又可以提供 Context Source，但仍不等于 Memory？
2. 为什么 DocumentVersion/Chunk/Asset 是业务事实，而 Milvus/Elasticsearch 只是可重建候选索引？
3. Agent Checkpoint 与 ingestion stage checkpoint 都使用版本和幂等时，为什么仍不能共用一个状态模型？
4. Worker hard stop 后怎样证明是“从最后成功节点继续”，而不是从头重跑并隐藏重复费用或副作用？
5. `no_result`、`not_ready`、`partial_index`、`dependency_failed` 和 `permission_denied` 分别会怎样改变 Agent 的下一步与用户界面？
6. `FinancialScope` 为什么必须在检索、计算、Checkpoint 和 Citation 中保持同一 CIK/accession/period/`as_of`？
7. 冻结 SEC fixture 能证明哪些合同，为什么不能证明 live EDGAR、新 filing 新鲜度或公开 benchmark 泛化能力？

每步验收记录“假设 → 场景 → 实现 → Trace → Scorer → 结论”，并明确选择保留、回退或继续实验。不能只记录测试数量或最终页面截图。

## 6. Day 5 Definition of Done 映射

每个 D5 目标从过程状态改为 `complete` 前，逐项记录以下证据。`N/A` 必须写明具体理由、复核人和日期；面向用户的旅程、权限、失败/恢复和安全检查不得标为 `N/A`。

| DoD 维度 | Day 5 必须提交的证据 |
|---|---|
| 真实用户旅程 | 创建 KB → 导入固定 SEC fixture → 观察入库 → ready filing 详情 → Dense Research/typed calculation → Evidence locator → hard stop/审批/resume → Workbench 反查 |
| 正常/边界/失败/权限/恢复 | 五步各自的领域、API、真实依赖、故障注入、跨 Workspace、刷新和恢复场景 |
| Migration 与契约 | fresh Alembic upgrade/check/downgrade/upgrade；OpenAPI/SSE、Parser、Embedding、`FinancialScope`、Knowledge/Calculator Tool、Observation、Checkpoint、Approval 和 resume typed contract |
| 可观测 | request/job/run/step/tool/document/accession/evidence/checkpoint 关联 ID、结构化阶段/Event/Trace、稳定错误码、无完整 filing/敏感原文 |
| 数据所有权和生命周期 | PG 事实、MinIO 私有对象、双索引派生、版本切换、deleting/deleted、补偿/对账、Checkpoint/Approval 保留与回滚 |
| 安全与隐私 | MIME/magic/大小/页数/像素/输出预算、Workspace 重授权、短签名 URL、Prompt Injection 边界、Secret/CoT/私有全文不进日志或 Checkpoint |
| 许可证与供应链 | Parser/OCR/Embedding/Milvus/ES/LangGraph 等新增或变更依赖的许可证、来源、NOTICE、锁文件和 audit |
| Eval | 新增 Day 5 SEC fixture/计算/L4 Scenario、三类规则 Scorer、人工抽样、F0～F2 与 L3/L4 对照；报告 filing identity、数值/公式、轨迹、Evidence、恢复、副作用、Token/费用/延迟 |
| 文档与回滚 | ingestion 状态机、Checkpoint/L4 合同、README/Runbook、能力矩阵、本文步骤证据；停用新入库/查询/恢复能力时不破坏既有 L3 事实 |
| 干净环境 | format/lint/type/build、全量测试、真实 PG/Redis/MinIO/Milvus/ES、关键浏览器旅程、Secret/依赖扫描和干净 CI |

Agent 追加 DoD 同时要求：

- 生产和 Harness 使用同一 Runtime/Research graph，Knowledge Tool/Context Source 只通过正式 profile 接入。
- State、Event、Tool/Observation、Evidence locator、Artifact、Checkpoint、Approval 和 side-effect ledger 都有版本化 typed contract。
- Tool surface、WorkspaceScope、Budget、deadline、审批与 Secret 来自可信 Runtime Context，模型不能修改。
- 成功、Tool/Provider/索引失败、取消、预算耗尽、hard stop、resume、重复请求和 schema 不兼容都有 Scenario。
- Trace 能解释 `FinancialScope`、Context manifest、Knowledge/Calculator Tool Step、Evidence/Claim、Checkpoint、usage 和结果，但不保存原始 chain-of-thought 或完整 filing。
- 真实用户可以看到 not-ready/partial/failed、审批、取消和恢复；UI 不能把错误、L3 草稿或 partial index 伪装成 complete/ready。

## 7. Day 5 总门禁

只有以下条件全部实际通过，才允许关闭 Day 5 并进入 Day 6：

- 代表性 SEC fixture 进入 `ready`，Research 经同一 Harness/Runtime 检索并引用固定 accession/section/Chunk/数值/公式 Evidence。
- 上传立即返回，Worker 各主要阶段可观察、可取消、可恢复或安全重试，重复 Chunk/索引/Artifact 为 0。
- F0～F2 对照报告证明 Dense Tool 与 calculator 对来源、数值、公式和拒答的贡献，而不只是“向量库有结果”。
- Research hard stop 后从版本化 Checkpoint 恢复；allow/deny/timeout、重复 resume、取消和预算真实生效，重复副作用为 0。
- Workbench 能沿“SEC fixture/accession → DocumentVersion/Chunk → 数值/公式 Evidence → Research Step → Checkpoint/Approval”导航并在刷新后恢复。
- 全局与 Agent DoD、D5-01～D5-09 双向映射、功能分支 CI、`main` 合并提交 CI 和项目所有者复核全部完成。

若任一条件未通过，保持 Day 5 相应过程状态并继续当前步骤，不开始 Day 6 官方 SEC 来源接入，不通过删除场景、放宽预算、静默 Mock、跳过真实依赖或把 Day 6～Day 8 能力提前混入来制造进度。
