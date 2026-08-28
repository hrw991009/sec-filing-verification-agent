# Day 7 执行计划：Filing Hybrid Retrieval、财务计算与核对

> 制定日期：2026-08-27
>
> 计划基线：[Day 1～Day 10 主计划](../master-plan.md) 2.1.5 Day 7
>
> 能力边界：[Day 1～Day 10 目标能力矩阵](../feature-matrix.md) D7-01～D7-08
>
> 架构决策：[ADR 0003](../adr/0003-unified-evidence-model.md)、[ADR 0007](../adr/0007-sec-disclosure-financial-fact-verification.md)
>
> 详细设计：[SEC Filing Retrieval 与计算设计](../sec-retrieval-design.md)
>
> 当前状态：Day 7 五步代码已由 [PR #11](https://github.com/hrw991009/industry-intelligence-platform/pull/11) 合入 `main`；功能 head `6a25ab2` 的两组 PR 检查均通过，但合并提交 `ae33b98` 的 main CI `33156337673` 最终为 6/7 Job 通过、Browser E2E 失败。D7-01/D7-03～D7-08=`implemented_pending_verification`，D7-02=`thin_slice`；`sec-tool-v1` deterministic gate 不代表 live/model、正式浏览器、中英 paired、真实依赖、main CI 或所有者复核已完成。项目所有者授权继续 Day 8～Day 9、Day 10 再统一查漏补缺；Day 6 的 `22/24`、bulk watermark/live SEC 与其他既有缺口继续作为发布硬门，不从原分母删除。

## 1. 进入条件与本日边界

Day 6 已由 [PR #10](https://github.com/hrw991009/industry-intelligence-platform/pull/10) 合入 `main`，功能 head 为 [`7a4766b`](https://github.com/hrw991009/industry-intelligence-platform/commit/7a4766b6d4c4ad764b9e095b2d0f03d8ec96c143)，合并提交为 [`84a7945`](https://github.com/hrw991009/industry-intelligence-platform/commit/84a7945ed769d63974602b5c20984e2f4ebf0e93)。push CI [`33053621106`](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/33053621106)、PR CI [`33053623731`](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/33053623731) 和 main CI [`33054136204`](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/33054136204) 均通过 7 个适用 Job，本地 `main` 与 `origin/main` 一致且工作树干净。

这些证据关闭了 Day 6 的提交、合并和 CI 条件，但没有改变版本化评测事实：

- `sec-source-v1` contract `18/18`、closeout `4/6`、总计 `22/24`；
- `submissions-bulk-watermark` 与 `companyfacts-bulk-watermark` 仍为 `capability_missing`；
- `submissions.zip`/`companyfacts.zip` 不可变 bytes、`bulk_published_at`/`coverage_through` 和 post-watermark 官方增量补齐尚未实现；
- 当前环境未配置合法 SEC 联系身份，尚无 live SEC smoke。

因此，“Day 6 分支已结束并合入”与“D6-01～D6-08 全部 `complete`”仍必须分开表述。项目所有者随后明确要求开始 Day 7 Step 1；本次在主计划、ADR、矩阵和学习日志中把两条 closeout case 显式改期为 Day 10 发布前硬门，保留 `sec-source-v1` 原分母和失败事实，不把改期写成豁免或完成。

Day 7 只交付可解释的 SEC L4 draft：锁定 filer/accession/`as_of` 后完成 Hybrid Retrieval、可定位 Evidence、确定性计算、核对和差异解释。Day 8 的 L5 Verifier、bounded revise、Monitor、持久写审批和跨 Worker 恢复不进入本日；Day 9 的公开 benchmark release suite 也不提前执行。

## 2. 复用边界与不变量

- 复用唯一 `UnifiedAgentRuntime`、Tool Registry/Executor、Checkpoint、Evidence/Claim/Citation 和 Research L4 graph，不建立金融专用 loop。
- Milvus 与 Elasticsearch 只产生候选；PostgreSQL 重新加载 Chunk、XBRL fact、source snapshot、Workspace import 和授权状态后，候选才能进入 Evidence。
- 所有检索、Context、Calculation、reconciliation、diff 和最终 Citation 必须锁定同一 `FinancialScope`：Workspace、CIK、accession、form、report period、`as_of` 与 amendment policy。
- `sec.search_filing@v1` 保持 Tool 名和 DTO 向前兼容，通过显式 `retrieval_profile_version=hybrid-v1` 区分 Day 6 `dense-v1`；禁止静默改写历史 Trace。
- 复用现有 `financial_verification` Decimal calculator 和 `evidence` lineage，扩展为正式 SEC 输入，不重写第二个计算器或 Citation 系统。
- 外部 filing、表格和 Tool Observation 都是不可信数据，不能改变 Instructions、Tool allowlist、Scope、Budget、`as_of` 或审批策略。

## 3. 五步实施计划

| 步骤 | 能力映射 | 实现范围 | 完成证据 |
|---|---|---|---|
| 1. Hybrid Retrieval 与 SEC locator | D7-01、D7-02 | 在锁定 accession 内并行取得 Milvus Dense 与 Elasticsearch BM25 候选，执行版本化 RRF、可插拔 rerank、去重和 section/table 多样性；定义 filing text/table/section 与 XBRL fact 的判别联合 locator，并统一进入正式 Evidence/Citation | 分层 ranking/过滤/授权测试；Recall@5 ≥ 0.80；wrong accession、future/Workspace leakage 为 0；Citation/source identity 可解析率 100% |
| 2. Financial Context Compiler | D7-03 | 扩展现有 Context Compiler，按问题、`FinancialScope`、Memory、XBRL facts、Filing Evidence、Tool Observation 和 Token budget 生成版本化 manifest；每个保留/排除项记录原因和来源版本 | cutoff、错误 accession、unit 不可比、预算裁剪与注入负向测试；同输入 manifest 确定性一致 |
| 3. Typed calculator 与 reconciliation | D7-04、D7-05 | 把既有 `finance.calculate@v1` 扩展到正式 SEC Evidence 输入；支持受控 Decimal operator、比例/百分比/变化率、unit/scale/rounding propagation；新增 period/unit/context reconciliation typed result | 零分母、负数、单位冲突、instant/duration、dimension、custom/standard concept 和 amendment 测试；所有用户可见派生数字都有公式与输入 Evidence lineage |
| 4. Filing diff、中文 L4 profile 与 Workbench | D7-06、D7-07 | 实现 `sec.diff_filings@v1` 的 base/amendment、相邻可比期间 fact/section diff；建立 `scope→resolve→select→decompose→structured+narrative→calculate→reconcile→draft` profile；Workbench 展示 Retrieval、Context 排除、Calculation、reconciliation、diff 与 Citation 反查 | 不可比范围 fail closed；中英同题锁定相同 filer/accession/facts/formula/终态；同一 Runtime/Checkpoint/Trace；正式 API 浏览器旅程 |
| 5. `sec-tool-v1`、A0/A1/A2 与 Day 7 收口 | D7-08 | 冻结简单事实、计算、跨章节、修订和无答案 cases；同一 manifest/预算/数据版本运行 oracle/full context、纯 Hybrid RAG、RAG+SEC/XBRL+calculator；生成确定性报告并完成统一 DoD | 错误 company/period/accession 为 0；证据不足正确拒答率 ≥ 0.90；A2 复杂题相对 A1 有净收益，简单题退化 ≤ 2pp；分支/PR/main CI 和所有者复核 |

步骤按依赖顺序执行。前一步没有形成正式数据合同、测试和可反查证据时，不开始下一步；Workbench 随每一步读取正式 API/Event/Trace 增量扩展，不在最后用 Mock 页面补交。

### Step 1 当前证据与缺口

- 已实现：`hybrid-v1` 并行 Dense/BM25、RRF60、去重、可插拔 reranker、section cap；依赖失败保持 `dependency_failed`，不伪装为 `no_result`。
- 已实现：Trace 冻结 query rewrite、双候选 limit、RRF、reranker、final limit、diversity、`as_of`、active source 与 index version；历史未注入 lexical port 的调用仍为 `dense-v1`。
- 已实现：Milvus/Elasticsearch 候选经 PostgreSQL 重新校验 Workspace、Knowledge Base、document version、active import/snapshot 与双索引版本；真实 PostgreSQL/MinIO/Milvus/Elasticsearch 链通过。
- 已实现：Tool Observation 使用 `sec://filing-chunks/{id}` 与 `sec://xbrl-facts/{id}`；Evidence 判别联合、SQLAlchemy normalizer、可用性重载、数据库约束迁移和 OpenAPI DTO 已支持 filing text 与 XBRL fact。
- 本地门禁：强制 PostgreSQL/Redis/MinIO/Milvus/Elasticsearch 的 Python 全量套件为 `1121 passed`，总体分支覆盖率 `80.62%`、Memory/Evidence/Research 核心合集 `86%`；Ruff format/check、mypy、migration 全历史往返与 fresh upgrade、OpenAPI/TypeScript 连续生成一致均通过。Web Prettier、ESLint、typecheck、Vitest `85 passed` 和 production build 通过。上述是当前工作树本地证据，不替代分支、PR 或 main CI。
- 尚未关闭：冻结 SEC 黄金集 Recall@5/MRR、filing table row/column/cell 与 character locator、正式 Citation resolver 100% 指标、live SEC smoke、PR/main CI。因此本步骤不能标为 `complete`。提交 `2944591` 的分支 CI `33135122319` 已通过；项目所有者随后明确继续 Step 2，该排期决定不删除上述验收缺口。

### Step 2 当前证据与缺口

- 已实现：在现有 `ContextCompilerV1` 上扩展兼容 `financial-context-v1` 的 `FinancialContextCompilerV1`，没有建立第二套 Runtime 或金融拼接器；同一编译器继续处理 WEB 的 `context-v1`。
- 已实现：生产 LOCAL Tool L2 policy 使用 `financial-context-v1`，把可信 Runtime `FinancialScope` 作为独立 source 注入模型请求；manifest/Trace 记录完整 scope identity，Memory、附件和 Tool Observation 仍是不能覆盖 scope 的不可信 USER data。
- 已实现：`finance.calculate`、`knowledge_search` 与三个 accession-bound SEC Tool 的 Observation 先按 scope 分类；错误 CIK/accession/form/period/scale 记为 `excluded_financial_scope_mismatch`，cutoff 后 scope/XBRL fact 记为 `excluded_future_source`，unit 冲突记为 `excluded_unit_mismatch`，畸形 payload 记为 `excluded_unsupported_financial_source`。
- 已实现：金融 Observation 按既有 Tool 顺序作为可选 Context source；预算不足时不使整个 Run 静默失败，而是在 manifest 中保留 locator identity、envelope hash、source version、零 Token 成本和 `excluded_token_budget`。同输入 request/manifest 确定一致。
- 已实现：Trace API/OpenAPI/TypeScript 增加 `financial_scope` source kind、稳定排除枚举和 nullable `source_identity`；Web decoder 对枚举、identity object 及 source kind 一致性做运行时校验。
- 提交与远端证据：Step 2 已提交为 `d3c88d5`。分支 CI `33140371558` 的 Browser E2E、Python/Web quality、Python/Node audit 和 Secret scan 共 6 个 Job 通过；PostgreSQL integration 在 Context manifest 持久化处因 `asdict()` 深拷贝只读 `source_identity` 报 `cannot pickle 'mappingproxy' object`，因此该次 CI 整体失败，不能写成 Step 2 远端通过。
- 当前工作树修复：Context manifest 持久化改为 dataclass 字段的浅层投影，再交给既有 JSON 规范化器递归转换；新增不依赖 Docker 的回归测试证明冻结 identity 会落为普通 JSON object。该修复尚未提交或取得新的分支 CI。
- 尚未关闭：本机 Docker daemon/PostgreSQL 未运行，当前工作树未执行强制 PostgreSQL Trace identity 往返；Step 1 的 ranking/table/Citation 和 Day 6 `22/24` 缺口继续保留，因此 D7-03 只能标为 `implemented_pending_verification`。

### Step 3 当前证据与缺口

- 已实现：`sec.get_xbrl_facts@v1` 为每个正式 fact 返回受 Workspace、`as_of` 与授权快照约束的确定性 Evidence ref；`finance.calculate@v1` 通过新 `FinancialOperandRepository` 按 fact ID 从 PostgreSQL 重载 Workspace import、active Knowledge Base/Document/Version、filing/source identity 与 cutoff，不信任模型回传的 value/ref。
- 已实现：正式 operand 使用现有 Decimal calculator，增加 `percentage`，并把 fact scale 规范到锁定 `FinancialScope.scale` 后再执行 add/subtract/ratio/percentage/percent change；formula、rounding、unit、scale 和输入 Evidence refs 一并进入 typed output。Day 5 fixture 输入继续兼容，但只要请求携带任一 `source_fact_id` 就必须完整走正式解析，未授权、缺字段或依赖失败不得降级到 fixture。
- 已实现：`financial-reconciliation-v1` 在算术前返回 `consistent`、`conflict`、`insufficient_evidence` 或 `not_comparable`，并记录稳定 issue code 与关联 Evidence refs；当前核对 scope/cutoff、report period、instant/duration/forever、unit、dimensions、standard/custom concept 和 amendment relation。非 `consistent` 不执行计算。
- 已实现：正式 Calculation Evidence 不只校验输出 hash，而是重新加载 active 输入 Evidence 与底层 XBRL fact/source/filing，复核 locator/value/identity，重跑 reconciliation 和 calculator 后才保存现有 `financial_calculation_v1` locator；历史 fixture calculation 保留原路径。
- 本地证据：不启用外部服务硬门的 Python 全量为 `1060 passed, 84 skipped`；全后端 Ruff format/check、全仓 mypy `473` 个源文件、Web format/lint/typecheck、Vitest `85 passed` 与 production build 通过。测试覆盖 scale propagation、percentage/percent change、负数/零分母、unit、instant/duration、dimensions、concept、amendment、正式 Tool 成功/拒绝/禁止 fixture 降级、Evidence locator 和 Context manifest 回归。
- 尚未关闭：本机 Docker daemon 未运行，新增 PostgreSQL operand 授权/跨 Workspace 负向测试未在本地执行；当前 Step 3 未提交，也没有新的分支、PR 或 main CI。尚未建立 `sec-tool-v1` 的错误 company/period/accession 指标和所有派生数字 lineage 100% 总门，因此 D7-04/D7-05 只能标为 `implemented_pending_verification`。

### Step 4 当前证据与缺口

- 已实现：新增 `sec-filing-diff-v1` application contract 和认证 API；仅接受已解析 base/amendment 或同 CIK、同基础 form 的相邻 annual/quarterly period。授权、active Knowledge/DocumentVersion、`as_of`、当前 `FinancialScope`、来源优先级、fact identity 和共享 section identity 都在服务端重载；不可比、未就绪、无结果、权限和依赖失败均返回 typed 状态，失败时不暴露部分 diff。
- 已实现：`sec.diff_filings@v1` 复用现有 XBRL 与 Hybrid filing service，返回左右 fact Evidence ref、section source/hash/Chunk 和完整 scope；Tool 不计算 delta，派生数字仍必须交给既有 `finance.calculate@v1`。同 section 多个 Chunk 按最高 score 和稳定 UUID tie-break 选择，避免候选顺序覆盖高质量结果。
- 已实现：生产 LOCAL surface 冻结为 `knowledge_search`、`finance.calculate`、`sec.search_filing`、`sec.read_filing_section`、`sec.get_xbrl_facts`、`sec.diff_filings` 六个只读 Tool；只有 exact surface 才启用中文 `sec-l4-v1`，仍复用同一 Tool L2 Runtime、Research graph、Checkpoint、Context manifest、Trace 与 Evidence ledger。语言切换不得改变 scope、事实、公式、reconciliation 或终态。
- 已实现：SEC Workbench 增加正式 Filing Diff 输入、typed failure、fact/section 双向来源与显式 unit/scale；Research Workbench 从正式 Trace/Evidence 展示 Retrieval、Context 排除、Calculation formula、reconciliation、diff Tool 状态和 Citation 反查。修复 Evidence API 只序列化旧 Industry/SQL locator 的缺陷，现可返回 SEC text/XBRL/Calculation locator 与正式 reconciliation lineage。
- 本地证据：不启用外部服务硬门的 Python 全量为 `1069 passed, 84 skipped`；Ruff format/check、全仓 mypy `475` 个源文件、Web format/lint/typecheck、Vitest `87 passed`、production build 和 OpenAPI 连续生成一致均通过。差异服务、base/amendment、相邻期间、跨公司拒绝、Tool source/Evidence、profile surface、composition root、认证 API、Evidence HTTP 序列化和两个 Workbench 审计路径均有回归测试。
- 远端事实：Step 4 已随提交 `e5fb75c` 推送；其分支 CI [`33152912538`](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/33152912538) 中 Python/Web quality 与两项依赖审计通过，Browser E2E、PostgreSQL integration 和 Secret history 失败。Browser 仍寻找已被正式 Hybrid 控件取代的 `Dense 检索`；对 operand `no_result` 的代码路径审计定位到 raw XBRL `iso4217:USD` 尚未规范到 calculator `USD`；Secret scan 命中测试中的 `token_counter_version` 非凭据常量。
- 当前工作树修复：E2E 选择器改为 `Hybrid 检索`；SEC XBRL→Financial 边界只把标准 `iso4217`/`xbrli` QName unit 规范为 calculator unit，未知 custom unit fail closed；版本常量使用仓库既有的精确 `gitleaks:allow` 注释。上述修复尚无新远端 CI。
- 尚未关闭：正式 API 驱动的浏览器 SEC diff/Research 全链、中英同题 paired run、真实 PostgreSQL/MinIO/Milvus/Elasticsearch 重跑、新分支/PR/main CI 和所有者复核仍缺。D7-06/D7-07 因此只能标为 `implemented_pending_verification`。

### Step 5 当前证据与缺口

- 已实现：新增严格 `sec-tool-v1` manifest、独立 frozen observation 输入和 `sec-tool-scorer-v1`；固定 `sec-tool-contract-data-v1`、同一 8 step/4096 Token/费用/延迟预算，以及 A0 oracle/no-tools、A1 纯 filing Hybrid search/read、A2 正式 `sec-l4-v1` 六 Tool surface。10 个 case 对简单事实、计算、跨章节、base/amendment 和无答案各固定 2 条，共 30 个策略运行，任何 case/strategy 缺失或重复都拒绝评分。
- 已实现：规则 scorer 从观察值重新计算 answer/Evidence、calculation program/lineage、Citation、identity、拒答、Tool surface、预算、步骤、Token、费用和延迟；错误 company/period/accession 直接从实际选择与 gold identity 比较，不接受报告自报计数。case 和 observation 均绑定现有 production component pytest 证据。
- 确定性报告：[`sec-tool-v1.json`](../../evals/reports/sec-tool-v1.json) 与 [`sec-tool-v1.md`](../../evals/reports/sec-tool-v1.md) 可由 `pnpm run eval:sec-tool` 确定重建并格式化。A0/A1/A2 case accuracy 为 `1.0/0.5/1.0`，复杂题为 `1.0/0.166667/1.0`，A2 对 A1 净增益 `0.833333`；A1/A2 简单题均 `1.0`，退化 `0`；A2 拒答、Citation、calculation lineage、Tool/budget 均 `1.0`，三策略错误 company/period/accession 均为 `0`。A2 相对 A1 增加 `73 micro USD` 与 `43 ms`，报告保留成本而不只报质量。
- 防误报边界：当前 observations 是 deterministic frozen contract，不是当前模型实际跑出的答案；报告自身标记 `day7_closeout_ready=false`，并列出真实依赖、live SEC/model、正式浏览器、中英 paired、三层 CI 和 owner review 阻断项。公开 benchmark 仍属于 Day 9，不能把本报告写成公开 benchmark 分数或完整金融 Agent 能力。
- 本地统一门禁：不启用外部服务硬门的 Python 全量为 `1081 passed, 84 skipped`；Ruff format/check 与全仓 mypy `478` 个源文件通过。Prettier、ESLint、Web/contract typecheck、Vitest `87 passed`、production build、OpenAPI 连续生成、`sec-tool-v1` 连续生成和最近 10 次提交的 Gitleaks 均通过。84 个 skipped 明确包含 PostgreSQL/Redis/MinIO/Milvus/Elasticsearch integration，不能作为真实依赖通过证据。
- 尚未关闭：上述外部/真实运行证据未完成，Step 1 的 Recall@5/table/cell/Citation 100% 与 Day 6 `22/24` 债务仍在；D7-08 因此是 `implemented_pending_verification`，不是 `complete`。

## 4. 版本化合同与评测口径

Day 7 至少冻结以下版本身份，具体字段在对应步骤实现前由测试先锁定：

- `hybrid-v1`：Dense/BM25/RRF/rerank 的候选和配置身份；
- `sec-evidence-locator-v1`：filing text/table/section 与 XBRL fact locator 联合；
- `financial-context-v1`：Context manifest、排除原因和 Token budget；
- `financial-reconciliation-v1`：一致、冲突、不足和不可比 typed result；
- `sec-l4-v1`：中文 SEC L4 profile；
- `sec-tool-v1` / `sec-tool-scorer-v1`：A0/A1/A2 共用的场景和评分合同。

评测不得只比较最终答案字符串。每个 case 至少检查 scope identity、retrieval profile、候选层级、Evidence locator、Calculation lineage、reconciliation result、Citation resolvability、Tool/Runtime 轨迹、成本和延迟。确定性 replay、live SEC/model repeated run 和公开 benchmark 必须分报。

## 5. Day 7 Definition of Done

Day 7 只有同时满足以下条件才能关闭：

1. D7-01～D7-08 的正式 Domain/Application/Adapter/API/Worker/Workbench 链路均已实现，没有第二套 Runtime、calculator、Evidence 或 Citation 旁路。
2. Python/Web/Playwright、OpenAPI、migration 全历史往返、真实 PostgreSQL/Redis/MinIO/Milvus/Elasticsearch、build、audit 和 Secret 扫描全部通过。
3. `sec-tool-v1` 固定报告达到 Recall、Citation、lineage、identity、拒答和 A0/A1/A2 门槛；失败 case 不从分母删除。
4. 分支 push CI、PR CI、合并提交 main CI 均通过，文档记录准确提交与 Run 链接。
5. 项目所有者逐项复核能力、限制和学习题；只交付 L4 draft，不使用 `verified` 产品状态。

## 6. 明确不进入 Day 7

- L5 Verifier、四种 verified 业务终态和最多一次 revise 属于 Day 8。
- Monitor、写 Tool、ApprovalRequest、通知与跨 Worker durable HITL 属于 Day 8。
- FinQA、TAT-QA、FinanceBench、FinSearchComp 的正式 release suite 与 temporal/中英大样本属于 Day 9。
- 投资建议、估值、预测、自动交易、税务和审计意见不属于 MVP。

## 7. 复盘题

1. 为什么 Dense、BM25 或 reranker 的高分候选仍不能直接成为 Evidence？
2. 为什么数字正确但 company、accession、period、unit 或 context 错误仍是失败？
3. Context Compiler 应如何证明某条候选被排除，而不是被模型无声遗忘？
4. Claim 分解、确定性 calculator 和 reconciliation 各自负责什么，为什么不能互相替代？
5. A2 相对 A1 没有净收益时，应回退哪部分复杂策略，而不是继续增加 Tool？
