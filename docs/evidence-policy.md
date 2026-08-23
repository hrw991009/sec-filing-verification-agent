# Evidence、Claim 与来源治理策略

> 版本：1.0（Day 4 步骤 3）；更新日期：2026-08-21；当前范围：Day 3 Web/行业与 Text2SQL Observation 的可信提升、版本化 locator、Claim 关系、派生证据图、失效、重新授权、Inspector 与确定性评测；本策略不定义 ResearchBrief/L3 graph，其复用本账本的合同见 [Research L3 状态机](research-state-machine.md)；Message/Report Citation、文档/多模态 Evidence、Verifier 和 bounded revise 仍不在本文范围。

## 1. 目标与唯一正式链路

Tool Observation 是模型可见但不可信的外部结果，不因带有 URL、SQL 或 `[S1]` 就自动成为 Evidence。当前唯一提升链路是：

```text
Conversation / UnifiedAgentRuntime / ToolL2Runtime
  → persisted ToolCall + ToolObservation
  → Evidence Normalizer 重建并校验完整 Observation 信封
  → 当前 Workspace 与底层来源重新授权
  → 来源版本、content hash、许可、locator 与依赖完整性校验
  → accepted Evidence 或 append-only rejected decision
  → ResearchClaim + claim_evidence
  → graph_nodes / graph_edges 派生投影
  → OpenAPI → Trace / Evidence Inspector
```

PostgreSQL 是 Evidence、Claim、关系、决策和图投影的业务事实源。前端不保存第二份 Evidence；图节点/边也不能反过来创造 Evidence 或 Claim 事实。生产、Harness、Research 和 Workbench 必须继续复用统一 Runtime、ToolCall、Observation、Run/Step 和 Trace 标识。

## 2. 当前实体与不变量

| 实体 | 职责 | 关键不变量 |
|---|---|---|
| `evidence_normalization_decisions` | 每个 Observation source 的 accepted/rejected 决策 | 绑定 Workspace、Run、ToolCall、Observation、source ordinal 和 normalizer version；同一输入重试幂等 |
| `evidence` | 当前可治理 Evidence 投影 | 保存 typed locator、hash、来源版本、许可、授权快照和完整 origin lineage；同一授权语义下才允许去重 |
| `research_runs` | Day 4 步骤 3 的最小所有权外壳 | 只允许绑定同 Workspace/owner 的正式 `research` AgentRun；完整状态机属于步骤 4 |
| `research_claims` | 结构化 Claim 当前投影 | verification status、coverage 和 conflict 由 active 关系计算，不接受 finalizer 文案覆盖 |
| `claim_evidence` | Evidence 对 Claim 的关系事实 | 只接受 `supports`、`refutes`、`context`；保存 origin Run/Step、ordinal、version 和 lifecycle |
| `graph_nodes` / `graph_edges` | Claim→Evidence 的可重建派生图 | 节点必须指向正式资源；边必须指向同一 ResearchRun 的节点；Evidence 失效同步使关系与图投影失效 |

Evidence 的 `active` 状态必须带有有界 excerpt，且不能有失效时间或原因。`superseded`、`tombstoned`、`unavailable` 必须清空 excerpt，并保存 UTC 失效时间与有界原因。资源 revision 从 1 开始并用 `If-Match` 做 compare-and-swap。

## 3. Observation 完整性边界

Normalizer 不接受浏览器或模型重新拼装的来源对象。它按 ToolCall ID 从 PostgreSQL 读取正式记录，并逐项验证：

1. ToolCall 属于当前 Workspace，状态为 `completed`，且有执行 Step、resolved Tool name/version 和 Observation。
2. Observation 只能含冻结字段，UUID、UTC 时间和 source 结构必须通过 Day 3 `ToolObservation`/`ToolSource` domain 校验。
3. Observation 的 `call_id`、Tool name/version 必须与 ToolCall 完全一致。
4. ToolCall 保存的 arguments、schema、content 和 envelope SHA-256 必须重新计算一致。
5. source ordinal 从 1 连续编号；相同 ToolCall/Observation/normalizer 的重复请求返回原决策，不重复创建 Evidence。

任一信封或哈希不一致时整个请求失败为不可提升的正式错误，不能只跳过坏字段后继续“尽力成功”。每个合法 source 的业务拒绝则保存稳定 decision reason，便于审计和重试解释。

## 4. Web/行业来源提升

当前 `industry_source_v1` locator 固定：`source_item_id`、`source_kind`、Provider、Provider version 和 content hash。只有同时满足以下条件才能成为 active Evidence：

- 当前 Workspace 中存在 locator 完全一致的不可变 `SourceItem`；
- 对应 `DataSource` 的 Provider version 与 Observation `source_version` 一致；
- `SourceItem.content_sha256` 与 Observation source hash 一致；
- 来源使用条款非空且没有明确禁止 Evidence 使用；
- SourceItem、DataSource、来源类型与当前 Workspace 授权一致。

Web Tool 只返回公共来源摘要不等于已经形成快照。若本地 SourceItem 不存在，Normalizer 保存 `source_snapshot_missing`，不把 URL 或模型摘要直接当 Evidence。当前 locator 以不可变行业 SourceItem 为正式快照；网页全文快照、段落范围和私有 object key 属于后续文档/Web snapshot 扩展，不能在本步虚报。

## 5. Text2SQL 来源提升

当前 `sql_result_v1` locator 固定：QueryRun、连接、SchemaSnapshot、schema hash、允许表、源列以及 `[row_start, row_end)`。提升时必须重新验证：

- locator 为 `sql://{connection_id}/{schema.table}/query-runs/{query_run_id}`，没有 credential、query string 或 fragment；
- QueryRun 属于当前 Workspace，状态为 completed，并精确绑定同一个 AgentRun 与 ToolCall；
- 数据连接仍为 ready，locator 表仍在当前 allowlist；
- QueryRun 绑定同 Workspace/connection 的 SchemaSnapshot 和唯一 QueryResult；
- QueryResult 与 QueryRun 的 content hash 都和 Observation source hash 一致；
- 经过 Day 3 完整 AST 校验的 SQL 能用冻结 SchemaSnapshot 重建保守列 lineage；
- excerpt 只保存有界列名、前三行、row count 和 truncated 状态，不保存无界原始结果。

读取 SQL Evidence 时再次检查 QueryRun completed、result hash、连接 ready 和表 allowlist。连接撤销、表移出 allowlist、QueryRun 不可用或 locator 损坏时，active Evidence 不得继续返回；持久化数据损坏返回受控 persistence failure，不静默伪装成授权成功。

## 6. Claim、coverage、conflict 与证据图

Claim 只在同一 Workspace/ResearchRun 中创建，创建 Step 必须是同一 origin Run 的 completed Step。所有关联 Evidence 必须 active 且在创建时通过底层依赖重新授权。

- 至少一个 active `supports` 且没有 `refutes`：`supported`。
- 至少一个 active `refutes` 且没有 `supports`：`refuted`。
- 同时存在 active `supports` 和 `refutes`：`conflicted`，`conflict=true`。
- 没有 active supports/refutes：`uncertain`。
- coverage 为 active supports/refutes 数量除以全部关系数量；`context` 不伪造支持度。

图只投影已持久化的 Claim 和 Evidence relation。Evidence 失效会把对应 `claim_evidence`、Evidence node 和 graph edge 标为 invalidated，并重新计算 Claim；历史关系 ID 仍用于审计，但不能继续计入 coverage 或显示为有效支持。

## 7. 授权、去重、失效与错误语义

`workspace_id`、actor、role 和 action 来自受保护路由的 Principal/WorkspaceScope，客户端不能提交。Evidence 保存创建时的最小授权快照，但每次读取仍按当前 Workspace 和底层资源重新授权；历史快照不是永久通行证。

去重 key 包含 Workspace、authorization role、typed locator 和 content hash。相同 hash 不允许跨 Workspace、跨来源版本、跨 locator 或跨授权角色合并。已失效的同 key Evidence 不会被“复活”，后续归一化返回 `dependency_unavailable`。

稳定拒绝原因包括 `observation_invalid`、`unsupported_source`、`locator_invalid`、`source_version_missing`、`source_snapshot_missing`、`source_hash_mismatch`、`license_not_allowed`、`dependency_unavailable`、`resource_unauthorized`。HTTP 使用统一 `application/problem+json`：不存在或无权查看统一为 404，stale revision 为 409，非法请求为 422，持久化不可用为 503。

失效端点要求 `If-Match` 和非空原因。失效后响应、列表详情和 Claim relation 都不得暴露原 excerpt、私有对象或签名 URL；本步没有对象存储 locator，因此签名 URL 条件为 `N/A`（原因：当前仅行业 SourceItem 与 SQL QueryResult；复核人 Codex，2026-08-21）。

## 8. API、Inspector 与评测

正式资源端点位于 `/api/v1/workspaces/{workspace_id}`：

- `POST /evidence/normalizations`；
- `GET /evidence`、`GET /evidence/{evidence_id}`、`POST /evidence/{evidence_id}/invalidate`；
- `POST/GET /research-runs`；
- `POST/GET /research-runs/{research_run_id}/claims`；
- `GET /claims/{claim_id}`、`GET /research-runs/{research_run_id}/claim-graph`。

TracePanel 只在 completed Tool 事件同时具有 call/observation ID 时显示“提升为 Evidence”。成功后跳转正式 Evidence Inspector；Inspector 展示来源版本、hash、许可、授权快照、normalizer、Evidence→Observation→ToolCall→Step/Run 反向 Trace，以及 Claim coverage/conflict/关系。刷新后全部状态从 API 重建。

版本化数据集 `day4-evidence-v1` 使用共享 Harness Scenario loader；`evidence-scorer-v1` 分开报告 validity、attribution、Claim support、coverage、conflict、citation resolvability、authorization leakage 和 normalization latency，且每项比率保留固定 numerator/denominator。冻结报告是确定性合同基线，不代表实时 Provider 或 Research L3 质量。

## 9. 发布、回滚与验证入口

发布顺序为先将 Alembic migration `f2a4c6e8b013` 升级到 head，再部署同时支持新表和新 API 的应用。回滚前必须停止 Evidence/Claim 写入并备份 PostgreSQL；只有明确接受丢失本步骤全部 ledger 数据时才能 downgrade 到 `d7c91e4a62bf`。不得保留第二套未迁移的 Citation/Evidence 正式链路。

验证入口：

- Domain/Normalizer/Scorer：`apps/backend/tests/modules/evidence/`；
- Web 正式链路、拒绝决策、Claim/失效：`apps/backend/tests/integration/test_conversation_web_tool_postgres.py`；
- Text2SQL 正式链路和只读账号：`apps/backend/tests/integration/test_evidence_text2sql_postgres.py`；
- fresh migration：`apps/backend/tests/integration/test_migration_smoke.py`；
- Inspector/Trace：`apps/web/src/evidence/EvidenceWorkspace.test.tsx`、`apps/web/src/chat/TracePanel.test.tsx`；
- 浏览器提升与刷新恢复：`tests/e2e/app-shell.spec.ts`；
- 数据集与报告：`evals/scenarios/day4-evidence-v1.json`、`evals/reports/day4-evidence-v1.json`、`evals/reports/day4-evidence-v1.md`。

步骤 3 的本地验收、commit/push 和最终分支 CI 均已通过；Research L3 已在步骤 4 完成并继续复用本账本。[PR #7](https://github.com/hrw991009/industry-intelligence-platform/pull/7) 已合入 `main`，合并提交 [`c0b854e`](https://github.com/hrw991009/industry-intelligence-platform/commit/c0b854e64ef1966b76cdcc38c41a507959c836cb) 的 [CI 32549438592](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/32549438592) 全绿，Trace/Eval/DoD 与授权收口完成，因此 D4-06 为 `complete`。Message/Report Citation、文档/多模态 locator 和 Verifier 仍按 Day 5～6 后续步骤推进。
