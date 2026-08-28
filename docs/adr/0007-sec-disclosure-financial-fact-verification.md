# ADR 0007：SEC 披露与财务事实核验作为后续 Agent 主线

> 状态：已接受
>
> 日期：2026-08-25
>
> 修订日期：2026-08-28
>
> 决策人：用户
>
> 首次接受依据：`docs/master-plan.md` v2.0.0 第 1、5.6、6.7～6.8、Day 5 Step 4～Day 10
>
> 2026-08-28 修订同步基线：`docs/master-plan.md` v2.1.3

## 背景

Day 1～Day 4 已完成统一 Agent Runtime/Harness、Tool loop、Memory、Evidence/Claim 与 Research L3。ADR 接受时，Day 5 仍处于功能分支实施阶段；截至 2026-08-28，Day 5 五步已由 PR #9 合入 `main` 且分支/PR/main CI 成功，D5-01～D5-07 为 `complete`。D5-08/D5-09 因缺 ready SEC fixture 的 Dense/calculation Evidence 与暂停/审批/resume/刷新浏览器全链，保持 `implemented_pending_verification`。Day 6 已由 PR #10 合入 `main`，但确定性报告仍为 contract 18/18、closeout 4/6，bulk watermark/post-gap 与合法 live SEC smoke 尚缺；项目所有者将其改期为 Day 10 发布硬门并保留原评测分母。Day 7 Step 1 的 `hybrid-v1`、PostgreSQL 重载和 filing text/XBRL fact locator 已实现，D7-01/D7-02 分别保持 `implemented_pending_verification`/`thin_slice`，直到 ranking、table/Citation 与 PR/main 门禁关闭。Step 2 的 `financial-context-v1` 已提交，但分支 CI `33140371558` 因 Context identity 序列化在 PostgreSQL Job 失败；当前工作树已按既有 JSON 投影模式修复，尚待新远端 CI，因此 D7-03 保持 `implemented_pending_verification`。项目所有者随后继续 Step 3；正式 XBRL operand 的 PostgreSQL 授权重载、既有 Decimal calculator 的 scale/percentage 扩展、`financial-reconciliation-v1` 与 Calculation Evidence 重算链已进入当前工作树，D7-04/D7-05 因真实 PostgreSQL、提交与远端 CI 尚缺保持 `implemented_pending_verification`。

原计划后续继续构建通用 Hybrid/Multimodal RAG。该方向可以验证检索，却不足以充分展示 agent loop、typed Tool、确定性计算、point-in-time 和可恢复写操作的价值，也难以用一个明确业务结果判断 Agent 是否真正更好。

用户决定将后续范围收敛为“SEC 披露公司监控与财务事实核验工作台”，以 SEC EDGAR 原始披露、XBRL 结构化事实和 filing 文本为主要外部证据，并使用公开 benchmark 与自建 SEC temporal suite 分层评测。MVP 不代表覆盖全部海外市场或 `20-F/6-K`。

## 决定

从 Day 5 Step 4 起，所有新增业务能力、数据、页面和发布验收围绕 SEC 披露与财务事实核验。已有通用基础继续复用，不建立第二套 finance Runtime、finance RAG、上传、索引、Evidence、Job 或评测 loop。

### 产品边界

MVP 支持：

- SEC 官方 EDGAR 数据；
- `10-K`、`10-Q`、`10-K/A`，并让 `10-Q/A` 复用同一 amendment 合同；
- CIK/公司解析、filing/accession/报告期间/`as_of` 锁定；
- XBRL structured facts 与原始 filing 文本/表格双通道证据；
- typed calculator、单位/scale/期间/context/amendment 核对；
- 中文提问、中文核验报告、英文原始 Evidence；
- 披露监控、差异 Case 和写操作 HITL。

MVP 不支持：

- 实时行情、股价预测、估值、目标价、荐股、组合建议或自动交易；
- SEC filing 提交、EDGAR Next 账户管理或代表发行人执行申报；
- 审计、法律、税务或投资意见；
- 以任意 Web 搜索摘要代替官方 filing；
- 执行模型生成的代码、SQL 或计算表达式。

### 外部事实与系统事实

- SEC 原始 filing 是本产品的外部披露核验依据。
- PostgreSQL 仍是系统业务事实源，保存 canonical SEC filer/filing/document/source snapshot/XBRL 目录、Workspace import、版本、状态、Evidence 关系、Agent Run、计算、审批、Monitor 和 Eval。
- MinIO 保存回答时实际使用的原始 filing/iXBRL/XML/附件与 submissions/companyfacts/companyconcept response 快照；对象保持私有且不可变。
- Milvus 和 Elasticsearch 只保存可重建的 filing 文本索引。
- SEC 聚合 API 或索引返回的候选必须回 PostgreSQL 重新加载并检查版本、`as_of`、状态和 Workspace。

“PostgreSQL 是系统事实源”不表示系统可以改写 SEC 披露；“SEC 是外部披露依据”也不表示 live SEC 响应可以绕过本地版本、授权、hash 和 Evidence 正规化。

SEC canonical source catalog 是全局、按稳定来源身份去重的公共披露目录；snapshot blob 与历史 source version append-only、不可变，filer/filing current projection 只能由新的官方 source version 推进。`workspace_sec_imports` 保存 Workspace 对 canonical filing/snapshot 的授权绑定、导入状态和 Knowledge DocumentVersion。认证 Workspace 内的 `resolve_filer/list_filings` 可以读取公共 discovery catalog，但不能暴露其他 Workspace import 状态；`get_xbrl_facts/search_filing/read_filing_section` 读取内容前必须经当前 Workspace import 授权。删除一个 import 不得删除仍被其他 Workspace、Run/Evidence 或固定评测引用的 canonical snapshot。

### 来源合同

正式 Adapter 只允许经过审核的 SEC 官方 host，并满足：

1. 所有进程合计遵守 SEC Fair Access，服务端设置包含应用标识与联系邮箱的 `User-Agent`。
2. 实施全局速率预算、缓存、条件请求、429/5xx 有界退避、timeout、响应大小/类型限制和稳定错误。
3. 交互/小批量读取使用 API；同批次达到 100 个 CIK 或计划全量刷新时强制使用官方 `submissions.zip`/`companyfacts.zip` bulk 路径。bulk snapshot 必须保存 `bulk_published_at`/`coverage_through`；请求 `as_of` 晚于水位时以版本化官方增量快照补齐，否则返回 typed incomplete/partial，不得返回 `no_result`。bulk 不可用时重试或明确失败，不静默退化为高扇出逐主体请求；浏览器不直接调用 `data.sec.gov`。
4. 每个快照共同保存 source kind、官方 URL、retrieved at、content hash、Adapter/source version、`source_version_available_at`、可见性依据和有效区间；来源定位字段按 source kind 判别，不伪造不适用字段：filing document 保存 CIK、accession、form、filing/accepted/report date、primary document 和 document locator；submissions/companyfacts/companyconcept 保存 CIK、endpoint/query、coverage manifest 与 supplemental references；bulk 保存 archive URL/date/hash、`bulk_published_at`、`coverage_through` 和 member locator，CIK/accession 可空。各类原始响应与 raw iXBRL 均须可重放。
5. 同一 accession/hash 幂等；同一 accession 内容变化不得静默覆盖，必须进入异常审计。
6. 原始 filing 可能包含第三方材料；“公开可访问”不自动表示整个 corpus 可按统一开放许可证再分发。每个 fixture/dataset 仍须记录来源、允许用途和再分发边界。

### Point-in-time 合同

每个 SEC Run 必须显式保存：

- filer/CIK；
- allowed forms；
- report period；
- `as_of`；
- filing selection policy；
- selected accession；
- amendment/base relation；
- source snapshot/hash。

Day 6 在 accession 选择前新增版本化 `FilingSelectionScope v1`，至少包含 CIK 候选、allowed forms、report period、`as_of` 和 amendment policy；选定后才物化 accession-bound scope。现有 Day 5 `FinancialScope v1` 保持 replay 兼容，不原地改变字段或语义。`sec.list_filings@v1` 必须计算查询区间 coverage，跟随 submissions 响应中与该区间相交的 `filings.files` supplemental JSON，保存包含 bulk/incremental watermark 的 coverage manifest 并按 accession 去重；current、所需 supplemental 文件或截至 `as_of` 的时间覆盖未全部检查时不得返回 `no_result`，缺失、损坏或时间缺口使用 typed dependency/incomplete/partial error。

`latest` 必须在运行时解析成明确 accession 并进入 Trace。Point-in-time 过滤使用 `public_available_at`、`visibility_basis` 和 `visibility_policy_version`：

- `report_date`、`filed_date`、`accepted_at` 保留各自业务/受理语义，不能混成一个 cutoff 字段；
- `retrieved_at` 只记录本地抓取时间，不能把事后抓取但在 `as_of` 前已公开的 filing 判为未来信息；
- 每个 source snapshot/version 还必须保存 `source_version_available_at`、可见性依据和有效区间；filing identity 当时存在不等于任一事后抓取字节版本当时存在；
- `retrieved_at` 也不能把 correction 后首次抓取的字节追溯成 correction 前版本；无法证明 source version 时必须 fail closed；
- SEC 后续更正、删除或晚间提交的传播差异必须形成新的来源/可见性版本，不能静默改写旧 Run；
- 无法证明历史日内公开时间时 fail closed，不能用 UTC 零点或当前索引猜测；
- 任何 `public_available_at` 晚于 `as_of` 的 filing、fact 或派生索引都不得进入 Tool output、Context、Calculation 或 final answer。

SEC `frames` 只用于候选发现或横截面探索，不能作为精确 fiscal period 核验的最终上下文。`companyfacts/companyconcept` 不能被解释为 custom tag、脚注、叙述文本或原始 filing 的完整覆盖。

### 双通道 Evidence

结构化通道保存 source-typed XBRL fact Evidence。aggregate fact locator 固定 endpoint response snapshot、accession、concept、unit 和 period，原始 context ID、dimensions、decimals/scale 按来源能力可空；raw iXBRL 或独立 XBRL instance XML locator 才承诺原始 element/context/dimensions。叙述通道从锁定 accession 的原始 filing 中检索 section/table/text Evidence。

两通道都必须经过：

```text
source result
→ snapshot/version check
→ Workspace/current scope authorization
→ typed locator validation
→ content hash and point-in-time check
→ Observation/EvidenceCandidate
→ Evidence Normalizer
→ Evidence/Claim/Citation
```

结构化与叙述证据冲突时，系统保留两者和选择依据，输出 `conflict` 或 `partial`；不得静默覆盖。

### Tool 与 Agent loop

正式 Tool surface 为：

- `sec.resolve_filer@v1`
- `sec.list_filings@v1`
- `sec.get_xbrl_facts@v1`
- `sec.search_filing@v1`
- `sec.read_filing_section@v1`
- `finance.calculate@v1`
- `sec.diff_filings@v1`
- `monitor.subscribe@v1`

前七个是只读 Tool；`monitor.subscribe@v1` 是写 Tool，必须持久审批。Tool capability、WorkspaceScope、`as_of`、allowed forms、Budget、SEC client policy 和审批结果来自可信 Runtime Context，模型不能提交或扩大这些字段。

交付按日分层：Day 6 只验收前五个 SEC 只读 Tool；`finance.calculate@v1` 的 Day 5 fixture 实现保留，正式 SEC 计算/核对与 `sec.diff_filings@v1` 在 Day 7 验收，`monitor.subscribe@v1` 在 Day 8 验收。`sec.search_filing@v1` 输出必须携带 `retrieval_profile_version`：Day 6 仅为 `dense-v1`，Day 7 才能声明 `hybrid-v1`；不能用同一 Tool 名静默把 Dense 结果描述为 Hybrid。Day 7 的五步顺序和具体合同见 [Day 7 执行计划](../learning-log/day-7.md) 与 [SEC Filing Retrieval 与财务计算设计](../sec-retrieval-design.md)。

`financial-context-v1` 必须扩展现有 Context Compiler，而不是新建 finance loop 或拼接器。LOCAL Tool L2 从可信 Runtime Context 注入完整 `FinancialScope`；Memory、filing/XBRL Observation 和其他文本均保持不可信 USER data。Compiler 对 scope/cutoff/unit/预算冲突 fail closed，并在 manifest/Trace 中记录 candidate identity、版本、hash、Token 与稳定决定原因；WEB Tool L2 保持 `context-v1`，历史 Trace 不被原地改写。

`finance.calculate@v1` 只允许受控 Decimal operator、unit/scale、rounding policy 和 Evidence refs。它不能执行 Python、JavaScript、Shell、SQL 或任意表达式。

Planner、Retriever、Analyst、Calculator coordinator 和 Verifier 首先是同一 typed graph 的节点职责。多 Agent 只有在 A0～A4 对照证明净收益后才允许另开 ADR。

### 评测与发布声明

任何单一公开 benchmark 都不能证明产品可用。发布证据分为：

- 固定 Runtime/Tool/Failure replay；
- FinQA/TAT-QA 等公开数值与表格 benchmark；
- FinanceBench/FinSearchComp 等补充覆盖；
- 自建 `sec-temporal-v1`；
- 中英配对、tool trajectory、recovery 和 injection suite；
- 固定 provider/model/version 的重复 live evaluation。

公开 benchmark 必须记录来源、版本/commit、split、license、checksum、允许用途和污染风险。LLM judge 只能辅助。固定 replay、公开 benchmark 和 live 结果不得混成一个总分。

## 结果

### 收益

- 复用已完成 Runtime/Tool/Memory/Evidence/Knowledge/Job 能力，避免重新造系统。
- 结构化事实、文本检索和确定性计算让错误可以归因到 scope、source、retrieval、calculation、verification 或 runtime。
- SEC 官方数据与 executable calculation 支持无需专家撰写开放式答案的确定性 temporal cases。
- Monitor/HITL 为 durable agent loop 提供真实写操作和恢复场景。
- 中文产品体验与英文公开 benchmark 可以通过共享 gold Evidence/Calculation 建立可比较链路。

### 代价与风险

- SEC taxonomy、custom tags、amendments、fiscal calendars 和单位语义复杂，聚合 API 不能覆盖全部场景。
- 原始 filing 解析、table/section locator 和 immutable snapshot 增加存储与对账成本。
- 不具备金融专家数据资源时，只能高置信验收披露事实、计算和引用，不能宣称验证开放式投资判断。
- 动态 benchmark 和 live Provider 存在时效、成本和随机性，必须与固定 CI 分离。
- 严格拒答可提高 verified precision，却可能降低回答覆盖率，报告必须同时展示两者。

## 否决方案

### 通用金融聊天或荐股 Agent

否决原因：数据权威、时点、风险和验收边界过宽，容易把流畅文案误作能力。

### 纯 Filing RAG

否决原因：无法可靠处理 CIK/accession、XBRL context、unit/period、确定性计算、amendment 和真实写操作，也无法解释 Agent loop 的净收益。

### 只使用 companyfacts 或 frames

否决原因：聚合范围和 period alignment 有明确限制，不能替代 custom facts、脚注、表格语义与原始 filing。

### 为金融方向建立第二套 Runtime/RAG

否决原因：会复制 Run/Step/Event、ToolExecutor、Knowledge、Evidence、Checkpoint 和 Eval，破坏既有架构与历史验收。

### 用一个 LLM judge benchmark 作为发布门

否决原因：动态数据、judge 漂移和覆盖缺口无法证明 point-in-time、Citation、恢复、安全或写操作正确。

## 迁移与回滚

1. Day 1～Day 5 的表、API、页面、提交与验收事实不回滚。
2. 新增 `disclosures` 和 `financial_verification` 模块时通过 Alembic 扩展，不改写旧行业表或 Evidence 历史；migration 同时建立 canonical source 与 Workspace import 的引用/唯一约束。
3. SEC filing 复用现有 File/Knowledge/Ingestion；旧私有知识仍可读取，默认新产品 profile 只暴露当前 Workspace 已导入的 SEC scope。
4. 回滚时停止新 SEC sync、verification 和 monitor 创建；保留已落 PostgreSQL 的 Run/Evidence/Calculation/Audit，撤回新路由/profile，不删除历史快照。
5. 已创建 Monitor 先禁用 schedule 并完成在途 Job 对账，再回滚应用；不得通过删表或丢弃 Outbox 消除副作用。
6. 任何扩大到行情、估值、交易、其他监管源或多 Agent 的决定必须新增 ADR。
