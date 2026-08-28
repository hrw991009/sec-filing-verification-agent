# SEC Filing Retrieval 与财务计算设计

> 状态：Day 7 Step 1 核心 Hybrid 与 filing text/XBRL fact locator 已通过本地统一门禁；ranking 评测和 table locator 待关闭
>
> 基线：`IIP-MASTER-001` 2.1.1，D7-01～D7-08
>
> 决策来源：[ADR 0003](adr/0003-unified-evidence-model.md)、[ADR 0007](adr/0007-sec-disclosure-financial-fact-verification.md)

## 1. 目标与非目标

本设计把 Day 6 已锁定的 SEC filer/accession/XBRL/source snapshot 链扩展为可评测的 Filing Hybrid Retrieval、SEC Evidence、确定性财务计算、period/unit/context 核对和中文 L4 draft。它不创建第二套 Agent loop、检索存储、calculator 或 Citation 模型。

Day 7 不负责 L5 Verifier、bounded revise、Monitor/HITL、公开 benchmark release suite，也不输出投资建议或把 L4 draft 标记为 verified。

## 2. 现有组件复用

| 现有边界 | Day 7 使用方式 |
|---|---|
| `modules/retrieval` | 扩展当前 Dense candidate port/service 为版本化 Hybrid candidate pipeline |
| `modules/disclosures` | 继续负责锁定 filing、XBRL fact、source snapshot、Workspace import 与 SEC read Tool |
| `modules/financial_verification` | 扩展现有 `FinancialScope`、Decimal calculation 和 `finance.calculate@v1`，不另写计算器 |
| `modules/evidence` | 统一保存 SEC locator、Calculation lineage、Claim relation 与 Citation |
| `agent_runtime` / `agent_harness` | 复用 Runtime、ToolExecutor、Checkpoint、Trace 和 profile materializer |
| PostgreSQL / MinIO / Milvus / Elasticsearch | PostgreSQL 是业务事实源，MinIO 保存不可变来源 bytes，Milvus/Elasticsearch 只保存可重建候选索引 |

## 3. `hybrid-v1` 检索管线

```text
FinancialScope + query
  -> locked Workspace/CIK/accession/as_of
  -> Dense candidates from Milvus
  -> lexical candidates from Elasticsearch
  -> PostgreSQL identity/authorization/source-version reload
  -> versioned RRF
  -> duplicate collapse + section/table diversity
  -> optional bounded reranker
  -> PostgreSQL reload and Evidence normalization
  -> Context candidate or typed no-result/dependency error
```

必须冻结并写入 Trace 的参数包括：query rewrite version、Dense/BM25 candidate limits、RRF constant、reranker identity、final limit、diversity policy、active source version、index versions 和 `as_of`。任一依赖失败不得伪装成 `no_result`。

RRF 只融合各通道名次，不把不同分数空间直接相加。相同 Chunk/source identity 先合并通道贡献，再做 section/table 多样性；reranker 只能重排已授权且同 accession 的候选，不能引入新文档。

## 4. SEC Evidence locator

`sec-evidence-locator-v1` 是判别联合，至少包含共同身份：Workspace、CIK、accession、form、report period、`as_of`、official URL、source version、content hash 和 observed time。

- `filing_text`：document、section、Chunk/character locator、excerpt hash；
- `filing_table`：document、table、row/column/cell locator、Chunk 与 excerpt hash；
- `xbrl_fact`：taxonomy、concept、context、period、unit、dimensions、decimals/scale、source kind 与 fact identity。

Locator 只有在读取时重新通过 Workspace、source visibility、snapshot/import active state 和 cutoff 授权，才能解析正文或 fact。索引命中、模型生成的 URL 或仅内容 hash 都不能替代正式 locator。

## 5. Financial Context manifest

`financial-context-v1` 由现有 Context Compiler 生成，不建立独立拼接器。Manifest 对每个候选记录：来源类型、identity、Token 成本、优先级、保留/排除决定和稳定原因码。

强制排除条件包括：错误 Workspace/CIK/accession、晚于 `as_of`、inactive source、错误 period/form、unit 不可比、缺失 locator、预算不足和重复内容。Memory 与 Tool Observation 只能提供上下文，不能覆盖可信 `FinancialScope` 或把自身提升为 SEC Evidence。

## 6. Calculation、reconciliation 与 diff

`finance.calculate@v1` 只接受已授权 Evidence operand，使用 Decimal 和固定 operator。结果保存 program、operand Evidence IDs、unit/scale/rounding、执行状态和输出 Evidence lineage；零分母、单位冲突、缺输入或越权输入返回 typed error。

`financial-reconciliation-v1` 按以下顺序核对：company/CIK -> accession/form -> fiscal period -> instant/duration -> unit/scale -> dimensions -> standard/custom concept -> amendment/source version。结果至少区分 `consistent`、`conflict`、`insufficient_evidence` 和 `not_comparable`，不得由模型把冲突改写为成功。

`sec.diff_filings@v1` 只比较同公司且满足明确关系的 base/amendment 或相邻可比 filing。Fact diff 与 section diff 分报，并各自保留左右 Evidence locator；不可比 period、unit、dimension 或来源覆盖返回理由，不生成误导性 delta。

## 7. Runtime、Trace 与 Workbench

`sec-l4-v1` 复用同一 Runtime/Checkpoint：

```text
scope -> resolve -> select -> decompose
      -> structured + narrative retrieval
      -> calculate -> reconcile -> draft
```

轨迹允许部分顺序，只冻结安全和业务里程碑，不要求模型采用唯一 Tool 序列。Workbench 从正式 API/Event/Trace 展示 query rewrite、各通道候选、RRF/rerank、Context 排除、Calculation、reconciliation、diff 和 Citation 反查；前端不自行计算排名或财务结果。

## 8. 评测与回滚

`sec-tool-v1` 的 A0/A1/A2 必须共用 case manifest、数据版本、Scope 和预算：A0 为 oracle/full context，A1 为纯 Filing Hybrid RAG，A2 为 RAG + SEC/XBRL Tool + calculator。核心门禁见 [Day 7 执行计划](learning-log/day-7.md) 和 [SEC Agent 评测计划](sec-agent-evaluation.md)。

回滚时通过 profile/retrieval version 停止新 `hybrid-v1` 和 `sec-l4-v1` 运行，保留 PostgreSQL 中的 Run、Trace、Evidence、Calculation 与审计记录；恢复 `dense-v1` 只影响新请求，不能原地改写历史结果或删除 locator。
