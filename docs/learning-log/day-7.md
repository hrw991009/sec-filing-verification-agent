# Day 7 执行计划：Filing Hybrid Retrieval、财务计算与核对

> 制定日期：2026-08-27
>
> 计划基线：[Day 1～Day 10 主计划](../master-plan.md) 2.1.0 Day 7
>
> 能力边界：[Day 1～Day 10 目标能力矩阵](../feature-matrix.md) D7-01～D7-08
>
> 架构决策：[ADR 0003](../adr/0003-unified-evidence-model.md)、[ADR 0007](../adr/0007-sec-disclosure-financial-fact-verification.md)
>
> 详细设计：[SEC Filing Retrieval 与计算设计](../sec-retrieval-design.md)
>
> 当前状态：`planned`。Day 7 文档基线已冻结，尚未开始代码；Day 6 已合入 `main` 且分支、PR、main CI 均通过，但 `sec-source-v1` 仍为 `22/24`，D6-02/D6-06 的两条 bulk watermark closeout case 未关闭，因此 Day 7 代码入口仍受阻。

## 1. 进入条件与本日边界

Day 6 已由 [PR #10](https://github.com/hrw991009/industry-intelligence-platform/pull/10) 合入 `main`，功能 head 为 [`7a4766b`](https://github.com/hrw991009/industry-intelligence-platform/commit/7a4766b6d4c4ad764b9e095b2d0f03d8ec96c143)，合并提交为 [`84a7945`](https://github.com/hrw991009/industry-intelligence-platform/commit/84a7945ed769d63974602b5c20984e2f4ebf0e93)。push CI [`33053621106`](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/33053621106)、PR CI [`33053623731`](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/33053623731) 和 main CI [`33054136204`](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/33054136204) 均通过 7 个适用 Job，本地 `main` 与 `origin/main` 一致且工作树干净。

这些证据关闭了 Day 6 的提交、合并和 CI 条件，但没有改变版本化评测事实：

- `sec-source-v1` contract `18/18`、closeout `4/6`、总计 `22/24`；
- `submissions-bulk-watermark` 与 `companyfacts-bulk-watermark` 仍为 `capability_missing`；
- `submissions.zip`/`companyfacts.zip` 不可变 bytes、`bulk_published_at`/`coverage_through` 和 post-watermark 官方增量补齐尚未实现；
- 当前环境未配置合法 SEC 联系身份，尚无 live SEC smoke。

因此，“Day 6 分支已结束并合入”与“D6-01～D6-08 全部 `complete`”必须分开表述。开始 Day 7 代码前，必须先把两条 closeout case 做到 `24/24`；如果产品所有者决定把它们改期，必须显式修改主计划、ADR、矩阵和评测分母，不能只在学习日志中豁免。

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
