# Day 6 执行计划：SEC 官方披露数据与 Point-in-Time

> 制定日期：2026-08-26
>
> 计划基线：[Day 1～Day 10 主计划](../master-plan.md) 2.0.5 Day 6
>
> 能力边界：[Day 1～Day 10 目标能力矩阵](../feature-matrix.md) D6-01～D6-08
>
> 架构决策：[ADR 0007](../adr/0007-sec-disclosure-financial-fact-verification.md)
>
> 当前状态：Step 1 已在当前工作树完成本地实现与适用本地门禁，D6-01 为 `implemented_pending_verification`；D6-06 已实现 Fair Access client 与 99/100 CIK 选路合同，但 bulk snapshot/watermark 仍待后续正式来源同步，因此为 `thin_slice`。Step 2～5 仍为 `planned`。尚无 live SEC、分支/main CI 或项目所有者收口证据。

## 1. 进入条件与今日边界

Day 5 已完成合并验证：[PR #9](https://github.com/hrw991009/industry-intelligence-platform/pull/9) 合入 `main`，合并提交为 [`a38d0ae`](https://github.com/hrw991009/industry-intelligence-platform/commit/a38d0aee101b66d9c6601a01b426ffd1ec0dcb34)。分支 push CI [`32920879147`](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/32920879147)、PR CI [`32924323618`](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/32924323618) 和合并提交 CI [`32924732755`](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/32924732755) 均通过 7 个适用 Job，D5-01～D5-07 已关闭为 `complete`。

Day 5 日志同时明确记录：现有 Chromium 只覆盖通用 Knowledge/Research 和 durability 读取，没有 ready SEC fixture 的 Dense/calculation Evidence 全链，也没有同一 fixture 的暂停/审批/resume/刷新旅程。项目所有者于 2026-08-26 先授权 Day 6 文档规划，随后明确要求开始 Day 6 Step 1；按主计划的“用户最新明确指令优先”执行 Step 1 代码。该指令只调整 Step 1 的开始顺序，不构成 D5-08/D5-09 豁免，两项及 Day 5 总门禁继续保持 `implemented_pending_verification`。

本轮只实现 Day 6 Step 1，不进入 filing/accession、原始快照、XBRL、Workbench 或 `sec-source-v1`。D6-02～D6-05、D6-07～D6-08 继续保持 `planned`；D6-06 的 bulk snapshot、published/coverage watermark 与 post-watermark 补齐必须在出现正式批量 filing/XBRL 来源读取的后续步骤落地，不能用当前 99/100 选路函数冒充完整 bulk 能力。

### 1.1 官方来源合同复核

截至 2026-08-26，Day 6 采用以下官方合同：

- [EDGAR API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) 的 `data.sec.gov` submissions 与 XBRL API 无需 API key，并随披露实时更新；`submissions.zip`/`companyfacts.zip` bulk 约在每日美东时间 03:00 重编。浏览器不能依赖其 CORS，正式访问必须经服务端 Adapter。
- 聚合 XBRL API 主要覆盖标准、非自定义 taxonomy 且针对整个主体；`companyfacts/companyconcept` 不能代替锁定 accession 的原始 iXBRL、custom tag、脚注或叙述文本。
- [Accessing EDGAR Data](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data) 和 [SEC Developer Resources](https://www.sec.gov/about/developer-resources) 要求声明应用/联系信息，并将所有机器合计请求控制在每秒 10 次以内；本项目必须再保留安全余量。
- CIK 是稳定身份；ticker/name 映射只能生成候选。SEC 后续更正、删除和晚间提交的公开可见时间差异必须进入版本化来源与可见性策略，不能用当前索引或 UTC 零点猜历史事实。

普通 PR CI 只运行冻结 response/replay。live SEC smoke 独立运行并记录 URL、抓取时间、内容哈希、Adapter 版本、速率与失败；两类证据不得混报。

## 2. 复用、所有权与时点合同

Day 6 必须复用现有正式链路：

| 现有能力 | Day 6 复用方式 | 禁止做法 |
|---|---|---|
| `UnifiedAgentRuntime`、Harness、Tool Registry/Executor | 五个 SEC 只读 Tool 进入同一版本化 profile、Trace 和预算合同 | 新建 finance Runtime、第二套 Tool loop 或 SEC 专用聊天入口 |
| File/Knowledge/Ingestion、Job/Outbox | Workspace 导入锁定快照后创建既有 DocumentVersion 和异步入库任务 | Adapter 直接写 Chunk、索引或 ready 状态 |
| PostgreSQL、MinIO、Milvus、Elasticsearch | PostgreSQL 保存业务关系，MinIO 保存不可变字节，Milvus/ES 仅保存可重建候选 | 以向量库、缓存或 live SEC response 作为系统业务真相 |
| Evidence/Claim、Checkpoint/HITL、Workbench | SEC Tool 输出继续走 Observation/EvidenceCandidate 与正式 Event/Trace | 让原始响应绕过授权、locator、hash 或 point-in-time 检查 |

数据所有权冻结为两层：

- 全局 canonical source catalog 保存公开 SEC filer、filing/document identity 和版本化 current projection；source snapshot blob 与历史 source version append-only、不可变，current projection 只能由新的官方 source version 推进。
- `workspace_sec_imports` 保存 Workspace 对 canonical filing/snapshot 的授权绑定、导入状态和 Knowledge DocumentVersion。认证 Workspace 内的 `resolve_filer/list_filings` 可以读取公共 discovery catalog，但不能读取其他 Workspace 的 import 状态；`get_xbrl_facts/search_filing/read_filing_section` 读取内容前必须从 import 绑定重新授权。
- 删除 Workspace 导入会清理该 Workspace 的派生 Knowledge/索引，但不会删除仍被其他 Workspace import、Run/Evidence 或固定评测引用的 canonical snapshot；公共快照按独立来源保留策略和显式引用计数对账。

现有 `FinancialScope v1` 表示 accession 已选定后的 Day 5 replay，不原地扩坏。Day 6 新增版本化 `FilingSelectionScope v1` pre-selection contract，至少包含 CIK 候选、allowed forms、report period、`as_of` 和 amendment policy；选定后才物化 selected accession scope。

Point-in-time 必须区分：

- `report_date`、`filed_date` 和 `accepted_at` 是披露业务/受理时间；
- `public_available_at`、`visibility_basis`、`visibility_policy_version` 决定一份 filing identity 是否可进入 `as_of` 候选；
- 每个 source snapshot/version 还必须保存 `source_version_available_at`、可见性依据和有效区间；只有能证明该字节/响应版本在 `as_of` 前存在时才能使用；
- `retrieved_at` 只证明本地何时抓取，既不能自动排除事后取得但当时已公开的版本，也不能把更正后首次抓到的字节追溯成更正前版本；
- 无法证明历史日内公开时间时 fail closed，不猜测；amendment selection policy 必须显式保存并解析成 accession。

## 3. 五步执行切片

| 步骤 | 能力映射 | 可验收用户结果 | 当前状态 |
|---|---|---|---|
| 1. 官方 Adapter 与 CIK 解析 | D6-01、D6-06 | 公司名/ticker 产生可解释候选，用户锁定明确 CIK；歧义不猜 | 本地已实现，待外部验证 |
| 2. Point-in-Time filing 选择 | D6-02、D6-05 部分 | 在 `as_of` 与 amendment policy 下锁定正确 form/accession | `planned` |
| 3. 不可变快照、Dense read 与 Workbench | D6-03、D6-05 部分、D6-07 | 锁定 filing 可入库、检索、读取并沿来源链反查 | `planned` |
| 4. XBRL context/fact 与 typed read | D6-04、D6-05 部分 | 结构化事实保留 unit/period/context/source 并可定位 | `planned` |
| 5. 五 Tool 同 Runtime 与 `sec-source-v1` 收口 | D6-05、D6-08 | 数据、时点、权限、故障和 live/replay 链路可系统评测 | `planned` |

### 步骤 1：官方 Adapter 与 CIK 解析

建立最小 `disclosures` bounded context、`SecEdgarPort`、Frozen/Live Adapter、canonical filer/alias 模型和 `sec.resolve_filer@v1`。Adapter 只能访问审核过的 SEC host/path；模型不能传 host、URL、User-Agent、速率或缓存策略。

实现边界：

- 服务端声明应用与联系邮箱；API 和 Worker 共用跨进程速率预算，低于官方每秒 10 次上限并保留余量。
- 交互式/小批量读取走 API；本步先冻结并测试 99/100 CIK 与 full-refresh 的 API/bulk 选路合同，不允许 bulk 失败时静默退化为高扇出逐主体请求。实际 `submissions.zip`/`companyfacts.zip` bytes、`bulk_published_at`、`coverage_through` 和 `(coverage_through, as_of]` 增量补齐分别随 Step 2 submissions 与 Step 4 XBRL 的正式批量读取落地；在此之前不得把选路函数写成完整 bulk 能力。
- 实现缓存、条件请求、响应大小/类型限制、超时、非官方跳转拒绝、429/5xx 有界退避和稳定错误分类。
- CIK 规范化为 10 位；ticker、现名和历史 alias 保存来源版本/有效期，只返回候选和匹配依据。

验收证据：identity fixture、历史 alias、同 ticker 多候选、低置信、错误公司、非官方 URL、429/timeout/依赖失败，以及 99/100 CIK 的 bulk threshold。bulk hash/partial/fallback、published/coverage watermark 与 post-watermark gap 是 Step 2/4 完成 D6-06 时的追加硬门。`ambiguous` 不自动选择第一项，依赖失败不伪装 `no_result`；Frozen contract 进入 PR CI，live smoke 独立留来源记录。

#### Step 1 实施记录（2026-08-26）

- 新增 `disclosures` bounded context、`SecEdgarPort`、Frozen/Live/Unavailable Adapter、canonical `sec_filers`/`sec_filer_aliases`/`sec_catalog_syncs` migration 与 PostgreSQL repository。来源版本按官方响应 hash 幂等，旧 alias 保留有效区间，较旧 catalog 不能回退 current projection。
- Live Adapter 固定访问 `https://www.sec.gov/files/company_tickers.json`，服务端 User-Agent 必须同时配置应用标识和联系邮箱；API/Worker 共用 Redis server-time 滑动窗口，默认 8 req/s、最大 9 req/s。缓存保留 ETag/Last-Modified，拒绝跳转、错误类型、超大/空/重复键响应，并对 429/5xx/timeout 返回稳定 typed error。
- 新增认证 Workspace API `GET /api/v1/workspaces/{workspace_id}/disclosures/filers/resolve` 与 `sec.resolve_filer@v1`。输入只允许 `query`/`limit`；CIK 规范化为 10 位，精确 CIK/ticker/name 优先，同一最高置信层多候选才返回 `ambiguous`，低置信名称候选不稀释精确身份命中。
- Tool 复用现有 `PydanticToolAdapter`、Registry/Executor、Workspace capability、Trace Observation 和 attributed `ToolSource`；它尚未加入普通 Conversation 的生产 profile，Day 6 五 Tool 专用 profile 仍按 Step 5 交付。
- 本地证据：`disclosures` 模块 17 条测试通过；真实 PostgreSQL catalog/历史 alias/精确 ticker 优先与 migration smoke 3 条通过；真实 Redis 跨进程预算 1 条通过；普通全量回归为 977 passed/79 skipped。强制 PostgreSQL/Redis/MinIO 后为 1054 passed/2 个显式索引依赖场景 skipped；补齐健康 Milvus/Elasticsearch endpoint 后这 2 条分别通过，当前树累计覆盖率为 81%。OpenAPI 生成前后 hash 一致。

未关闭项：当前环境未配置真实 SEC 联系身份，因此没有运行 live SEC smoke；bulk snapshot bytes、`bulk_published_at`/`coverage_through`、post-watermark 增量补齐、分支/main CI、D5 浏览器 DoD 和项目所有者最终复核均未完成。D6-01 因此只能是 `implemented_pending_verification`，D6-06 只能是 `thin_slice`。

### 步骤 2：Point-in-Time filing 选择

建立 canonical filing、base/amendment 关系、版本化可见性策略和 `sec.list_filings@v1`。`FilingSelectionScope v1` 由可信 Runtime Context 注入，模型只能提供业务候选；`latest` 必须解析成明确 accession 并进入 Trace。

实现边界：

- 启用 `10-K`、`10-Q`、`10-K/A`；`10-Q/A` 只保证同一 amendment 合同兼容，不扩大到 `20-F/6-K`。
- 保存 report/filed/accepted/public-available 时间、可见性依据、primary document、官方 URL、来源版本和 amendment policy；filing identity 可见不代表任一事后 snapshot version 可用于历史 cutoff。
- `sec.list_filings@v1` 对请求的 `as_of`/report period 计算 coverage，并跟随、快照 `submissions` 响应中 `filings.files` 指向且时间范围相交的官方 supplemental JSON；按 accession 去重。coverage manifest 同时保存 bulk watermark 与任何增量补齐 refs。只有它证明 current + 所需 supplemental files + 截至 `as_of` 的 bulk/incremental 时间覆盖均完整后才可返回 `no_result`；缺失/损坏 supplemental file 或未补齐的 bulk gap 返回 typed dependency/incomplete/partial error。
- 至少支持 `as_filed` 与 `latest_amendment_known_by_as_of`；未来 filing/alias 可落 canonical source catalog，但进入 Tool output、Context、Calculation 前必须过滤。

验收证据：正确 company/form/report period/accession，cutoff 后 base/amendment 候选为 0，歧义与缺失可见性证据返回 typed 状态；覆盖仅存在于 supplemental JSON 的历史 filing、current/supplemental 重复 accession、缺失/损坏 supplemental response，以及 `as_of` 分别位于 bulk `coverage_through` 之前、相等和之后的测试。未补齐 post-watermark gap 时不得返回 `no_result`；`as_of`、coverage manifest、policy、selected accession、base relation 和 source identity 可从 Trace 重放。

### 步骤 3：不可变快照、Dense read 与 Workbench

仅按已锁 accession 下载 complete submission/raw text、primary HTML/iXBRL/XBRL instance XML 和解析必需附件。canonical document identity/current projection 与 append-only source snapshot/version 分开保存；MinIO 字节不可变，PostgreSQL 保存官方 URL、SHA-256、retrieved_at、`source_version_available_at`/有效区间/依据和 Adapter version。官方 correction/deletion 追加新版本并推进 projection，不覆盖旧字节。

实现边界：

- `workspace_sec_imports` 通过既有 File/Knowledge/Ingestion Application Service 创建 DocumentVersion、Job 与 Outbox；不得旁路写 ready、Chunk 或索引。
- 实现 `sec.search_filing@v1` 与 `sec.read_filing_section@v1`；Day 6 只启用锁定 accession 的 `dense-v1`，输出携带 `retrieval_profile_version`，Day 7 才启用 `hybrid-v1`。
- Workbench 沿 CIK → accession → document/snapshot → DocumentVersion/Chunk/section 导航；浏览器不直连 SEC。

验收证据：同 accession/document/hash 幂等；相同来源身份字节变化进入 anomaly/quarantine，不覆盖旧快照。损坏、partial、Worker hard stop 不进入 ready；重复 filing/snapshot/chunk/index 为 0；未锁 accession、hash 不匹配和跨 Workspace 均拒绝。真实 PostgreSQL/MinIO/Milvus/Elasticsearch 集成和浏览器旅程通过。

### 步骤 4：XBRL context/fact 与 typed read

建立 canonical XBRL context/fact 与聚合响应 snapshot，完成 `sec.get_xbrl_facts@v1`。标准事实可以由 `companyfacts/companyconcept` 发现；精确 raw fact 与 custom tag 必须回到锁定 accession 的 raw iXBRL 或独立 XBRL instance XML；`frames` 只用于候选发现。

实现边界：

- 保存 source kind、官方 URL/response snapshot hash、source-version visibility、accession、taxonomy/concept、unit、instant/duration 和 filed time。原始 context ID、dimensions、decimals/scale 等字段按 source capability 可空；aggregate locator 使用 endpoint snapshot + accession + concept + unit + period，不能伪造 raw context。
- Tool 只从 PostgreSQL 重新加载，并通过 Workspace import、`as_of`、accession、context 和 source snapshot 检查后返回。
- 聚合与 raw 来源分开，不把 aggregate 数据描述成 custom tag、脚注或原始 filing 的完整覆盖。
- Workbench 增加 accession → source snapshot → XBRL context/fact 面板，展示 aggregate/raw locator、缺失字段原因和 source-version visibility；浏览器验收必须能反查一个 standard fact 和一个 raw/custom fact。

验收证据：standard/custom、instant/duration、dimensions、unit/period 与错误 context 正负例；cutoff 后 fact 为 0。每个返回值都可反查 accession、原始/聚合 snapshot 和 typed fact locator。

### 步骤 5：五 Tool 同 Runtime 与 `sec-source-v1` 收口

建立 Day 6 专用 Harness profile，只暴露 `sec.resolve_filer@v1`、`sec.list_filings@v1`、`sec.get_xbrl_facts@v1`、`sec.search_filing@v1` 和 `sec.read_filing_section@v1`。Day 5 calculator 代码保留，但不把 calculator、diff、Hybrid、Verifier 或 Monitor 计入 Day 6 完成声明。

`sec-source-v1` 至少 24 个确定性 case，分为 18 个 contract cases 与 6 个 closeout regression cases；后者不支持“未见数据泛化”声明。覆盖 identity/ambiguity、form/amendment/visibility/supplemental history、snapshot/idempotency/anomaly、XBRL context/unit/custom、Tool readiness/trajectory、429/5xx/timeout、损坏/partial 和跨 Workspace。每个 case 固定 `execution_kind=tool|sync`；sync case 另固定 `sync_kind=canonical_source|workspace_import`。manifest 还固定 dataset/split/checksum、可选 source snapshot/version、`expected_snapshot_presence`、`expected_import_presence`、bulk/增量 coverage、CIK/accession/`as_of`、visibility policy、allowed/forbidden tools、期望里程碑、结果/错误、eligible metrics 和 scorer version。

收口硬门：

- 预期返回来源的 eligible cases 中，source identity 与 locator 可解析率为 100%；ambiguous/no-result/依赖/权限 case 不进入该分母，但单独评价错误分类；
- future leakage、错误 company/form/accession、跨 Workspace、未授权写 Tool均为 0；
- 重复 filing/snapshot/fact/index 为 0；
- 429 或依赖失败误报 `no_result` 为 0；
- deterministic replay、offline report 与 live smoke 分报，live 结果不进入普通 PR 硬门。

成功且 `expected_snapshot_presence=true` 的 `execution_kind=tool` case 必须由 AgentRun/ToolCall/Trace 反查 source snapshot。`sync_kind=canonical_source` 由 Job/Outbox 反查 canonical sync/source snapshot，合法成功时可固定 `expected_import_presence=false`；`sync_kind=workspace_import` 才必须反查 `workspace_sec_imports`，并按 manifest 分别断言 snapshot/import presence。对 429、连接失败、bulk 下载前失败或快照提交前 hard stop，`expected_snapshot_presence=false`/`expected_import_presence=false`，证据链在 request attempt 或 Job/Event + typed error 收口，并断言零已提交 snapshot/import。每项指标在 manifest 中固定 eligible denominator，不能用不适用 case 稀释错误率。

完成 migration 往返、OpenAPI、Python/Web/浏览器、真实 PostgreSQL/Redis/MinIO/Milvus/Elasticsearch、依赖与 Secret 扫描、分支 CI、`main` CI、DoD 和项目所有者复核后，D6-01～D6-08 才能统一改为 `complete`。

## 4. 明确不进入 Day 6

- BM25 查询、RRF、rerank、Recall@5/MRR 和结构化+叙述双通道核对属于 Day 7。
- 正式财务计算、period/unit reconciliation、filing diff 和中文 SEC L4 profile 属于 Day 7。
- Evidence-aware Verifier、bounded revise、Monitor/HITL 和间接 Prompt Injection suite 属于 Day 8。
- `sec-temporal-v1` 60-case、公开 benchmark release suite 和中英配对属于 Day 9。
- 行情、预测、估值、荐股、交易、SEC filing 提交、`20-F/6-K`、任意 Web fallback 和第二套 Runtime/RAG 均不在范围内。

## 5. 复盘问题

1. 为什么 CIK 是身份而 ticker/name 只能是带版本的候选？
2. 为什么 `accepted_at`、`public_available_at` 与 `retrieved_at` 不能合并成一个时间字段？
3. 为什么全局公开 SEC 快照仍需要 Workspace import 绑定和返回前授权？
4. 为什么 aggregate XBRL API 不能替代锁定 accession 的 raw iXBRL？
5. `sec-source-v1` 能证明哪些数据合同，又为什么不能证明 Day 7 检索质量或最终金融判断？
