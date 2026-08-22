# Day 4 执行计划：Agent Memory、Evidence 与 Deep Research L3

> 制定日期：2026-08-20
>
> 计划基线：[七天主计划](../master-plan.md) 1.7.1 Day 4
>
> 相关决策：[系统架构](../architecture.md)第 6.5～6.6、10.3、12、15.1、15.4、15.6、18 节，[ADR 0003](../adr/0003-unified-evidence-model.md)、[ADR 0005](../adr/0005-langgraph-research-only.md)
>
> 当前状态：Day 3 门禁已关闭；Day 4 步骤 1～5 已完成仓库内实现与统一的本地验收，五个提交已推送到 `feat/day-4`，最终提交 `b99ca7a` 的 GitHub CI `32547497639` 已通过全部 7 个适用 Job。GitHub `main` 仍为 `2791123`，没有 Day 4 PR/合并提交，项目所有者最终 Trace/复盘也尚未记录，因此 D4-01～D4-07 保持 `implemented_pending_verification`，当前仍在 Day 4 收口，尚未进入 Day 5。

## 1. 执行边界

Day 4 复用 Day 2/3 已保存的 L0/L2 Run、Observation、Context manifest、任务结果、Token、费用、延迟和 24 条累计 Scenario，不重做基线，也不另写 Runtime、Tool loop 或仅供展示的 Workbench 数据链路。每一步都必须沿正式 PostgreSQL、Application Service、统一 `UnifiedAgentRuntime`、Event/Trace/Context manifest、OpenAPI 和真实前端交互形成纵向闭环。

本日终点是可治理的 Short/Long-term Memory、Observation→Evidence→Claim 与可解释 Research L3 草稿。Durable Checkpoint、持久 HITL、Worker hard-stop resume 属于 Day 5，Verifier 与 bounded revise 属于 Day 6；不得用普通状态持久化、节点名称或 Mock 成功提前冒充这些能力。

每一步只有在其正常、边界、失败、权限、刷新恢复、契约、可观测和评测条件全部通过后才能关闭。适用的全局与 Agent Definition of Done 必须逐项复核；任何 `N/A` 都要写明具体理由、复核人和日期。

## 2. 五个可验收步骤

| 步骤 | 主要能力矩阵 | 关闭重点 |
|---|---|---|
| 1. 可控 Memory 写入 | D4-01、D4-03 的写入/Short-term 建模部分 | 用户确认、revision、来源与策略 |
| 2. Memory 召回与治理 | D4-02、D4-03、D4-07 的 Memory 预算部分 | 实际 ModelInput/manifest、修改与删除残留 |
| 3. Evidence/Claim 账本 | D4-06、D4-04 的 Evidence/Claim 前置事实 | 提升边界、lineage、coverage/conflict |
| 4. Research L3 graph | D4-04、D4-07 的 Research 执行边界 | ResearchBrief、唯一 graph、预算与终态 |
| 5. Workbench/Eval/门禁 | D4-05 与全部 Day 4 Eval/DoD | 正式可视化、对照报告、全量门禁 |

### 步骤 1：可控 Memory 写入纵向闭环

交付从正式会话发起的“保存为记忆”旅程：生成候选摘要，用户确认或编辑后保存；同时建立版本化 `ShortTermMemoryState`、`Memory`、`MemoryRevision`、生命周期、provenance/source ref、scope、confidence、write reason 和 policy/user decision。使用 Alembic、正式 Application Service/API/OpenAPI 和前端交互，不默认永久保存全部聊天，也不把 Short-term Memory、Context compaction、Run State 或 Checkpoint 混写为 Long-term Memory。

验收条件：

- 用户能从真实会话创建候选、编辑并确认，刷新后仍能查看当前投影和修订历史；create/update/merge/reject 都有稳定事实与错误语义。
- 敏感、低置信、无明确价值、来源失效和跨 Workspace 请求被策略或权限边界拒绝；模型不能自行确认写入。
- fresh migration、数据库约束、OpenAPI 生成、API/组件/真实 PostgreSQL 测试和最小浏览器旅程通过，Trace/日志不保存 Secret、敏感原文或原始 chain-of-thought。

### 步骤 2：Memory 召回、治理与 Context manifest 闭环

在同一 Runtime 的 Context Compiler 中接入 Short/Long-term Memory：按当前目标、user/workspace scope、时效、冲突、重复、敏感度和 Token 预算选择，并在 manifest 中记录包含、排除和裁剪原因。完成跨 Thread 召回、使用说明、搜索、反馈、修改、停用、过期和删除；建立独立 Memory Scorer，测量 write accuracy、retrieval precision/utility、污染率、冲突处理、Token 成本、修改生效率和 deletion residual。

验收条件：

- 下一次正式 Agent Run 能说明实际使用了哪条 Memory，并可从回答/Trace 反查 revision、provenance、scope 和 manifest；Harness 不建立第二套调用路径。
- 冲突 Memory 按冻结策略明确排除，或带 provenance/不确定性注入，绝不静默覆盖新事实；过期、无关和超预算 Memory 不会静默注入。用户修改后下一 Run 使用新版本，停用或删除后下一 Run 不再引用。
- deletion residual 为 0、跨 Workspace 召回为 0；重复删除幂等，Short-term/Long-term/State/Checkpoint 分层和预算耗尽均有确定性 Scenario。

### 步骤 3：Observation→Evidence→Claim 账本闭环

复用 Day 3 的 Web/行业与 Text2SQL Observation/EvidenceCandidate，建立统一 Evidence Normalizer、版本化 locator 判别联合、来源/许可与资源重新授权、去重、content hash、lineage、状态和失效语义。建立 `ResearchClaim` 与 `claim_evidence`，分别表达 supports/refutes/context 关系以及 supported/refuted/uncertain/conflicted 状态、coverage 和 conflict；证据图只从已持久化并授权的正式关系派生。

验收条件：

- 至少各有一条 Day 3 Web/行业和 Text2SQL 正式 Observation 经过完整校验后成为 Evidence，并能反查 origin Run/Step/ToolCall、来源版本、normalizer version 和 locator。
- 未授权、locator 无效、来源版本或许可缺失、依赖失败、敏感内容超界和跨 Workspace 候选不得提升为 active Evidence；失效后不再暴露 excerpt、私有对象或签名 URL。
- 每个关键 Claim 关联当前用户可访问的 Evidence，或明确标记 uncertain/conflicted；supports/refutes/context、coverage、conflict 和图节点/边可确定验证，不能由文案或 finalizer 伪造确定性。

### 步骤 4：ResearchBrief 与唯一 Research L3 graph 闭环

实现问题澄清和版本化 `ResearchBrief`，显式保存原始问题、确认范围、排除项、完成标准和预算；在 LangGraph 内建立唯一 typed L3 graph：`clarify_scope → write_research_brief → plan → research_loop → normalize_evidence → synthesize_claims → outline → draft`。Graph 节点只调用统一 Runtime 或 Domain/Application Port；ToolExecutor 只能通过 Runtime 的既有校验路径触达。若本步引入 LangGraph 依赖，必须锁定版本并完成许可证、依赖和回滚复核。

验收条件：

- 确定 Fake 下 ResearchBrief、typed state、Event 序列、Plan、Observation→Evidence、Claim、coverage/conflict 和可解释草稿可重复；原始问题与确认范围不会被 Planner 静默改写。
- 生产、Harness、Research 和 Workbench 使用同一 run_id、Step/Event sequence、Budget、stop reason 与 Trace；不存在图外 Research、Router/节点直连 Provider 或复制 Tool loop。
- 正常、Tool/Provider 失败、Evidence 缺失/矛盾、max steps、Token/费用、deadline、取消和跨 Workspace 场景均有明确终态；本步只交付 L3，不宣称 durable resume、HITL、Verifier 或 bounded revise。

### 步骤 5：Workbench、对照 Eval 与 Day 4 门禁收口

扩展正式 Agent Learning Workbench 的 Memory、Context manifest、Research 时间线和 Evidence/Claim 图；所有状态从 OpenAPI、Event、Trace、manifest 和正式资源 API 重建。保留 Day 2/3 基线并增加 Day 4 的 Memory 候选/冲突/删除、Research scope、Evidence 缺失/矛盾、预算和权限 Scenario；Memory Scorer 与 Evidence/Research Scorer 分开报告，并完成同题 L0/L2/L3 对照。

验收条件：

- 刷新后仍能回答“什么被写入、为何召回、什么进入 Context、哪些 Claim 由哪些来源支持”；修改、停用、删除和 Evidence 失效会同时反映在界面与下一 Run。
- 累计 Scenario 不少于主计划要求的 20 条且保留已有 24 条基线；报告同时包含结果、轨迹、Evidence、Memory 污染/删除残留、Token、费用和延迟，并由规则 Scorer、确定性夹具和人工抽样支持，LLM judge 不是唯一判据。
- 全量 format/lint/type、fresh Alembic migration、OpenAPI/SSE contract、真实 PostgreSQL/Redis/MinIO、权限负向、组件、关键 E2E、依赖/许可证、Secret 与隐私检查通过；不存在静默 Mock、调试旁路或第二正式链路。
- 完成 `docs/memory-policy.md`、`docs/research-state-machine.md`、本学习日志、能力矩阵和必要 README/运行回滚说明；逐项关闭 Day 4 验收门禁与适用 Definition of Done，干净 CI 通过后才能把 D4-01～D4-07 标记为 `complete` 并进入 Day 5。

## 3. 步骤状态

| 步骤 | 当前状态 | 关闭条件 |
|---|---|---|
| 1. 可控 Memory 写入 | `implemented_pending_verification` | 本地验收与最终分支 CI 已通过；待 `main` 合并、合并提交 CI 和所有者总复核统一关闭 |
| 2. Memory 召回与治理 | `implemented_pending_verification` | 本地验收与最终分支 CI 已通过；待 `main` 合并、合并提交 CI 和所有者总复核统一关闭 |
| 3. Evidence/Claim 账本 | `implemented_pending_verification` | 本地验收与最终分支 CI 已通过；待 `main` 合并、合并提交 CI 和所有者总复核统一关闭 |
| 4. Research L3 graph | `implemented_pending_verification` | 本地验收与最终分支 CI 已通过；待 `main` 合并、合并提交 CI 和所有者总复核统一关闭 |
| 5. Workbench/Eval/Day 4 门禁 | `implemented_pending_verification` | 本地门禁与最终分支 CI 已通过；待 `main` 合并、合并提交 CI 和项目所有者最终 Trace/复盘关闭 |

状态只随实际证据更新。代码存在、页面截图、单条漂亮答案、Mock success 或局部绿色测试都不能把任一步改为完成。

### 3.1 步骤 1 本地验收记录

复核人：Codex；复核日期：2026-08-20。

- 用户旅程：真实 Chromium 从已持久化 Conversation 选择 Message，生成候选，编辑并确认，刷新后通过正式候选/Memory API 恢复同一 Revision；完整 Playwright 5/5 通过。
- 正常与并发：create/update/merge/reject、候选创建和确认幂等、候选 Revision/目标 Revision CAS、两个并发确认仅一个成功均由真实 PostgreSQL 验证。
- 权限与失败：未登录 401、viewer 写入拒绝、member/owner 可信 scope、伪造 Workspace/Message、来源逻辑删除、敏感内容、正文超限、过期时间和 stale Revision 均有稳定拒绝；跨 Workspace 来源和详情泄漏为 0。
- 数据与迁移：`b4d8f3a9c210` 在 fresh PostgreSQL 上完成 upgrade/downgrade/upgrade；复合外键、CheckConstraint、候选/Revision lineage 和事务一致性测试通过。
- 契约：OpenAPI 与生成 TypeScript 两次生成 SHA-256 一致；未通过暂存文件掩盖有意的未提交契约 diff。HTTP 使用统一 problem+json；本步没有新增 SSE Event，故 SSE schema 变更为 `N/A`（原因：Memory 写入是同步资源决议，不是 Run 流事件；复核人 Codex，2026-08-20）。
- 质量：328 个 Python 文件通过 Ruff format/check，mypy 检查 318 个源文件无问题；911 个 pytest、14 个 Vitest 文件共 57 个测试、生产构建和 wheel/sdist 构建通过，真实 PostgreSQL/Redis/MinIO 强制开启且无 skip。
- 安全与供应链：Python/Node audit 均无已知漏洞；受控路径及 49 个 Git 提交的 Gitleaks 无发现；AuditLog 只含 id、decision、revision、scope、source count 和 trace id，测试确认敏感正文不进入响应或审计 metadata。
- 依赖/许可证 `N/A`：本步骤没有新增 Python、Node、服务或 Provider 依赖，锁文件和 NOTICE 无变化；复核人 Codex，2026-08-20。
- 尚未远端验证：当前没有执行 commit、push 或 GitHub CI，因此步骤状态保持 `implemented_pending_verification`；这不阻塞继续由项目所有者审阅本地切片，但不能表述为干净 CI 已通过。

### 3.2 步骤 2 本地验收记录

复核人：Codex；复核日期：2026-08-20。

- 正式下一次 Run：真实 Chromium 在 Conversation A 确认 Memory 后新建 Conversation B；测试驱动只替换外部 Provider，正式 Job/Loader、`UnifiedAgentRuntime`、`ContextCompilerV1`、Context manifest、Event/Trace 与 PostgreSQL 链路不变。Trace 明确显示 Long-term Memory“已送入模型”，并可导航到对应 revision 后继续治理。
- ModelInput/manifest：契约测试同时断言被纳入 Memory 正文实际存在于 Provider `ModelRequest`，manifest 只保留 id/digest/revision/scope/ranking 元数据；排除项不进入 ModelInput，纳入项 Token 总数与预算快照一致。Short-term、Long-term、summary、附件、问题与 Observation 保持独立 source kind 和稳定顺序。
- 召回策略与权限：真实 PostgreSQL 以 current goal、scope、freshness、status/expiry、敏感度、反馈、重复、冲突和预算执行确定性选择；Conversation B 只能召回本 Workspace 当前用户的相关 Memory，同题的外部 Workspace Memory 候选数为 0。
- 治理与删除：搜索、反馈、修改、停用、启用、过期和删除均走正式 HTTP/Application/Repository；修改后召回 content v2，负反馈、停用与过期分别给出稳定排除原因。删除与重复删除均成功，随后召回、列表、详情、Revision 正文和 Revision source 的在线 residual 均为 0；备份介质擦除仍属于 Day 7 保留策略，不在本步冒充完成。
- Workbench：正式 Memory 管理页支持列表、搜索、筛选、详情、Revision、修改、scope/kind/expiry、停用/启用、反馈和删除确认；共享 Memory 对非 owner 只读。失败或 stale revision 后重新加载服务端，不用乐观 UI 假装成功；Trace 展示 scope、version、relevance、feedback、Token 与排除原因。
- Eval：`day4-memory-v1` 通过共享 Harness Scenario loader，`memory-scorer-v1` 输出 write accuracy、retrieval precision/utility、pollution、conflict handling、edit effectiveness、deletion residual、input token 和 latency，并为每项比例固定 numerator/denominator。JSON/Markdown 是确定性 fixture 基线，不冒充真实 Provider 质量结论。
- 契约与迁移：`d7c91e4a62bf` 在 fresh PostgreSQL 上完成步骤 2 schema upgrade，Memory migration 与治理测试 4/4 通过；OpenAPI/TypeScript 连续两次生成 SHA-256 一致。HTTP 使用统一 problem+json；本步未新增 SSE Event type，故 SSE schema 变更为 `N/A`（原因：召回事实进入既有 Context manifest/Trace，治理是同步资源操作；复核人 Codex，2026-08-20）。
- 完整本地门禁：PostgreSQL/Redis/MinIO 全部强制开启时 Python `916 passed`、无 skip；Ruff、mypy 321 个源文件、Python wheel/sdist、ESLint、类型检查、15 个 Vitest 文件共 60 条测试、生产构建和 Playwright 5/5 通过。Python/Node audit 无已知漏洞，受控代码/测试/文档/Eval 路径 Gitleaks 无发现。
- 依赖/许可证 `N/A`：本步骤未新增 Python、Node、服务或 Provider 依赖，`uv.lock`、`pnpm-lock.yaml` 与 NOTICE 无变化；复核人 Codex，2026-08-20。
- 本机运行时偏差：仓库固定 Node `24.16.0`，当前本机为 `24.19.0`，pnpm 因此给出 engine warning；上述门禁均通过，但精确 Node 版本的干净环境复核仍必须由 CI 完成。
- 尚未远端验证：当前没有执行 commit、push 或 GitHub CI，因此步骤 2 与相关能力矩阵项保持 `implemented_pending_verification`/`thin_slice`，不得表述为干净 CI 已通过。

### 3.3 步骤 3 本地验收记录

复核人：Codex；复核日期：2026-08-21。

- 正式来源链路：Day 3 `industry.web_search:v1` 与 `database.text2sql:v1` 均通过统一 `ToolL2Runtime` 产生正式 PostgreSQL ToolCall/Observation；Normalizer 重新计算 Tool schema/content/envelope hash，不接受客户端重组的候选。
- Web/行业提升：真实 Web Conversation→Job/Outbox→Runtime→Tool→Observation 链路中，只有具备同 Workspace 不可变 SourceItem、精确 Provider version/hash 和允许许可的一项成为 Evidence；缺少快照的第二项保存 `source_snapshot_missing` 拒绝决策，重复归一化返回同一 Evidence。
- Text2SQL 提升：独立 PostgreSQL 只读账号完成实际 SELECT；QueryRun 精确绑定 AgentRun/ToolCall，并携带 completed 状态、SchemaSnapshot、validated SQL、QueryResult 和 result hash。`sql_result_v1` locator 可反查 connection、QueryRun、SchemaSnapshot/hash、允许表、源列和行范围；跨 Workspace 读取为 0。
- Claim 与图：supports/refutes/context、supported/refuted/uncertain/conflicted、coverage 与 conflict 使用纯 domain 规则；Claim→Evidence 节点/边只由正式关系生成。Evidence tombstone 后 excerpt 清空、relation/node/edge 失效，Claim 从 supported 重算为 uncertain、coverage 从 1 降为 0。
- 权限与生命周期：来源版本/hash/许可、locator、依赖、当前连接 allowlist 和 Workspace 在提升/读取时分别复核；去重不跨授权角色、来源版本或 Workspace；所有写操作保留审计与资源 revision，失效使用 `If-Match`。
- Workbench：Trace completed Tool 事件可发起“提升为 Evidence”，成功进入正式 Inspector；页面显示来源版本/hash/许可/授权快照/normalizer、Evidence→Observation→ToolCall→Step/Run 反向 Trace、Claim coverage/conflict/关系，并从 API 在刷新后恢复。
- Eval：`day4-evidence-v1` 通过共享 Harness loader，`evidence-scorer-v1` 分开报告 validity、attribution、Claim support、coverage、conflict、citation resolvability、authorization leakage 和 latency；JSON/Markdown 是确定性合同基线，不冒充实时 Provider 或 Research L3 质量。
- 迁移与契约：`f2a4c6e8b013` 已在 fresh PostgreSQL 通过 migration smoke；OpenAPI 和 TypeScript 契约由正式应用生成。未新增 SSE Event type，故 SSE schema 变更为 `N/A`（原因：提升与治理是同步资源决议，origin 复用既有 Tool Event/Trace；复核人 Codex，2026-08-21）。
- 完整本地门禁：PostgreSQL/Redis/MinIO 全部强制开启时 Python `927 passed`、无 skip，包含 Evidence HTTP 权限/Revision 契约、fresh migration smoke、Web/Claim 与独立只读账号 Text2SQL Evidence 集成；354 个 Python 文件通过 format，Ruff、mypy 342 个源文件和 Python wheel/sdist 构建通过。Web format/lint/typecheck、16 个 Vitest 文件共 61 条测试、生产构建和 Playwright 5/5 通过；其中浏览器从正式 Trace 提升 Evidence 并在刷新后恢复 Inspector lineage。OpenAPI/TypeScript 连续两次生成 SHA-256 一致。
- 安全与供应链：Python/Node audit 均无已知漏洞；受控源码、测试、文档、Eval 路径及 51 个 Git 提交的 Gitleaks 扫描无发现。普通日志、Trace 和 AuditLog 不保存 Secret、原始 Tool 参数、Provider 原始响应或 chain-of-thought。
- 依赖/许可证 `N/A`：本步骤未新增 Python、Node、服务或 Provider 依赖；SQL lineage 复用 Day 3 已锁定的 SQLGlot，锁文件和 NOTICE 无变化；复核人 Codex，2026-08-21。
- 尚未远端验证：步骤 3 验收时没有执行 commit、push 或 GitHub CI，因此步骤 3 保持 `implemented_pending_verification`；当时 D4-04 Research L3 尚未完成，其后续本地结果见 3.4。完整 D4-05 Workbench 和 Message/Report Citation 仍未完成，不能提前进入 Day 5。
- 本机运行时偏差：仓库固定 Node `24.16.0`，当前本机为 `24.19.0`，pnpm 给出 engine warning；所有上述 Web 门禁仍通过，但精确版本的干净环境复核必须由 CI 完成。

### 3.4 步骤 4 本地验收记录

复核人：Codex；复核日期：2026-08-21。

- 用户入口与原子事实：`POST /api/v1/workspaces/{workspace_id}/research-runs` 要求显式 Brief 与 `Idempotency-Key`，返回 `202`。同一 PostgreSQL 事务创建 ResearchRun、Brief、AgentRun、Job、Outbox 和 queued Event；重复请求返回同一资源。member/owner 可创建，viewer 与跨 Workspace 请求不进入服务。
- 唯一 graph 与 Runtime：正式节点严格为 `clarify_scope → write_research_brief → plan → research_loop → normalize_evidence → synthesize_claims → outline → draft`。LangGraph 只负责编排；`research_loop` 复用唯一 bounded model/tool loop，生产 L2/L3 共用 ContextCompiler、Provider、Registry、ToolExecutor、Event committer 和 CancellationProbe。真实 PostgreSQL 链只有一个 AgentRun 和既有 MODEL/TOOL/MODEL/FINAL 四个 Step，不存在 research_steps/events/checkpoints 或第二条 Tool loop。
- Brief、State 与草稿：原始问题、确认范围、排除项、完成标准、预算、确认者和 revision 持久化；Planner scope drift 由领域校验拒绝。JSON-safe Research State 与 Run 原子创建，保存 node、Plan、Evidence/Claim refs、Step/Token/费用、取消和 stop reason，但明确不是 durable Checkpoint。草稿固定标为 explainable/uncertain L3 draft，不冒充 verified Report。
- Evidence/Claim：确定 Fake 路径产生 accepted Evidence、supported Claim 和 explainable draft；真实 `industry.web_search:v1` 在缺少不可变 SourceItem 快照时产生 rejected normalization、uncertain Claim 和 uncertain draft，而不是把 URL、摘要或 `[S1]` 伪装成 Evidence。两条路径都使用步骤 3 的正式 Application Service。
- 失败与预算：Research 专项测试覆盖取消、deadline、invalid Provider output、max steps、Token 和费用耗尽，并断言只有一个 terminal Event、失败后不保存 Draft；共享 L2 Runtime 的既有回归继续覆盖 Provider timeout/429、Tool deny/error、no-progress 和取消安全点。跨 Workspace 创建/读取不泄漏资源。
- 迁移与契约：`a3c5e7f9b021` 扩展 research_runs 并创建 briefs/plans/drafts，在 disposable PostgreSQL 完成 upgrade → Alembic check → downgrade → upgrade；OpenAPI 与 TypeScript 连续两次生成 SHA-256 一致。Evidence Inspector 已迁移到新的 `ResearchRunDetailResponse`，没有恢复步骤 3 的旧重复接口。
- 完整本地门禁：370 个 Python 文件通过 format，Ruff 无问题，mypy 检查 357 个源文件无错误；pytest 940 passed，默认未强制的两个 MinIO 测试随后以 `MINIO_TESTS_REQUIRED=1` 单独执行并 2/2 通过。Web format/lint/typecheck、16 个 Vitest 文件共 61 条测试、生产构建和 Playwright 5/5 通过；wheel/sdist 构建成功。
- 依赖与安全：精确锁定 `langgraph==1.2.11`；主包、checkpoint/prebuilt/sdk 和 LangChain Core 为 MIT，ormsgpack 为 Apache-2.0 OR MIT，xxhash/uuid-utils 为 BSD。import probe 与 `uv audit --locked` 通过，Python/Node 无已知漏洞；受控路径和 52 个提交的 Gitleaks 扫描无发现。
- 运行与文档：状态、事件、失败、L3/L4/L5 边界和回滚见 [Research L3 状态机](../research-state-machine.md)。本机 Node 与仓库固定版本均为 `24.16.0`，pnpm 为 `10.10.0`，没有步骤 2 曾记录的 Node engine 偏差。
- 尚未远端验证：步骤 4 验收时没有执行 commit、push 或 GitHub CI，因此步骤 4、D4-04 和 Research 部分的 D4-07 保持 `implemented_pending_verification`；当时尚未完成的 Research Workbench、对照 Eval 和 Day 4 总门禁，其后续本地结果见 3.5。

### 3.5 步骤 5 本地验收记录

复核人：Codex；复核日期：2026-08-22。

- 正式 Workbench：新增 Research workspace，通过生成 OpenAPI 类型调用 Research create/list/detail、Claim/Evidence graph、Agent cancel 和 safe Trace API；显式展示 Brief、scope/exclusions/criteria、Plan、八个 graph node、统一 Step、usage/stop reason、Evidence/Claim、coverage/conflict、uncertain draft 和不可用状态。Evidence↔Research 可关联导航，刷新后只从 PostgreSQL/API/Event/Trace 重建；没有前端轨迹或分数缓存。
- 真实用户旅程：Chromium 从页面创建 Research L3，经 ResearchRun/AgentRun/Job/Outbox、统一 Runtime、正式 Web Tool、Evidence Normalizer、Claim 和 draft，再查看安全节点时间线、跳转 Evidence/Claim，并在整页刷新后恢复同一 Run。冻结 Web Provider 缺少不可变 SourceItem snapshot 时稳定得到 rejected Evidence、uncertain Claim 和 uncertain draft，未被伪装成成功。
- Trace 安全修复：Research 节点 Event 加入后端显式安全字段表，模块导入时强制该表穷举全部 `AgentEventType`；前端 Trace 解码器同步接受三个版本化 Research Event。`error_summary`、Prompt、Tool 原文、Memory/Evidence 正文和 chain-of-thought 不进入 Trace。
- 独立 Eval：保留 Day 2/3 的 24 条基线；Day 4 使用 8 条 Memory、2 条同输入 Memory off/on、6 条 Evidence/Claim 和 10 条 Research Scenario，累计 50 条。`memory-scorer-v1`、`memory-ablation-scorer-v1`、`evidence-scorer-v1`、`research-scorer-v1` 分开计算；同题 L0/L2/L3 报告同时展示步骤、Token、费用、延迟、Evidence、Claim 和不确定项。执行代理人工抽样同题 L0/L2/L3、Evidence 缺失、冲突、取消与权限 case，确认规则字段与 frozen fixture/Trace ref 一致；这些报告不冒充真实 Provider 质量。
- Memory 消融：同一问题、Provider fixture、Prompt、Runtime 和预算下，Memory on 相对 off 的规则任务质量 `+1.0`、污染 `0.0`、冲突处理 `+1.0`，fixture 成本为 `+64` input tokens、`+7 ms`。该单一确定性用例不能外推为“Memory 对所有任务都有净收益”。
- 完整本地门禁：固定 Python 3.13.14、Node 24.16.0、pnpm 10.10.0；373 个 Python 文件通过 format，Ruff 通过，mypy 检查 360 个源文件无问题。真实 PostgreSQL/Redis/MinIO 强制开启且无 skip，pytest `946 passed`；migration smoke 完成全历史 upgrade → Alembic check → downgrade base → upgrade。17 个 Vitest 文件 `75 passed`，全量 Playwright `6 passed`，Python wheel/sdist、Web production build、OpenAPI/TypeScript hash 确定性、Python/Node audit 均通过。
- 覆盖率：后端全量 statement/branch 综合覆盖率 `82.12%`，超过整体 80% 门槛；前端关键 `chat-workbench-model.ts` 的 statement/branch/function/line 均为 `100%`，超过 75% 门槛。Day 4 选定 Domain/Application/Research workflow 合集为 `85%`，未达到目标 90%，记录为明确例外：原因是 PostgreSQL adapters 与公开 API/E2E 已覆盖主要纵向链，但多个纯校验失败分支尚未逐一补齐；风险是罕见畸形输入的分支回归发现较晚；缓解为 CI 固定 85% 不退化、全量 80%、真实集成和权限/失败 E2E，并在进入 Day 7 完整门禁前补到 90%。例外复核人 Codex，2026-08-22；它不等于豁免远端 CI。
- 安全与供应链：受控源码/测试/文档/Eval 路径与完整历史 Gitleaks 无发现（当前受控路径含未提交变化，历史扫描 53 个可达提交）；`uv audit --locked` 与 `pnpm audit --audit-level high` 无已知漏洞。覆盖率工具锁定 `pytest-cov>=7.1.0,<8.0.0` 与匹配 Vitest 的 `@vitest/coverage-v8==4.1.10`，许可证均为 MIT；Day 4 LangGraph 许可证结论保持不变。安全/隐私和生命周期复核见 [Day 4 专项复核](../security/day-4-memory-research-review.md)。
- 生命周期、Citation 与 N/A：在线 Memory deletion residual 为 0；Evidence tombstone/Claim 重算通过；备份一致性、恢复核对和回滚步骤已文档化，完整隔离恢复/物理 purge 按主计划保留为 Day 7，未虚报完成。Evidence scorer 的当前 locator 可解析率为 `2/2`，Research L3 不生成 Message/Report Citation，因此不存在新增悬空 Citation；最终 Report/Citation 完整门禁仍归 Day 6。durable graph resume/HITL/Verifier/bounded revise 为阶段性 `N/A`，原因是主计划明确分别归 Day 5/6，Day 4 用唯一终态、停止原因和 hard-stop 收敛替代；复核人 Codex，2026-08-22。
- 当时尚未远端验证：步骤 5 本地收口时还没有 commit、push 或 GitHub CI，因此当时步骤 1～5 与 D4-01～D4-07 只能保持 `implemented_pending_verification`。后续提交与分支 CI 结果见 3.6；本段不覆盖本地关闭时点的原始记录。

### 3.6 提交、分支 CI 与最终关闭审计

复核人：Codex；复核日期：2026-08-22。

- 提交与推送：五个步骤依次形成 `4243cb0`、`7f8c7ac`、`446c9cc`、`27b75ea`、`b99ca7a`，均已推送到 `origin/feat/day-4`；工作树审计时本地分支与远端分支指向同一最终提交。
- 干净分支 CI：最终提交 [`b99ca7a`](https://github.com/hrw991009/industry-intelligence-platform/commit/b99ca7a8eca3f51a726449bc2aa7462aa51c9cff) 的 [CI 32547497639](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/32547497639) 状态为 `success`。7 个适用 Job 全部通过：Browser E2E、Python dependency audit、PostgreSQL integration、Python quality、Secret history、Node dependency audit、Web quality；覆盖率门槛也在正式 CI 步骤中执行。步骤 4 提交曾有失败运行 `32541399291`，最终提交修复后由成功运行取代，不以 rerun 掩盖失败。
- 覆盖率例外：最终 CI 保持 Day 4 核心合集 85% 不退化、后端全量 80% 和前端关键状态 75% 门槛；本地测得 85%/82.12%/100%。核心合集距离 90% 的原因、风险、缓解和复核人仍以 3.5 为准，必须在 Day 7 总门禁前清偿，不能因 CI 通过删去。
- 主分支审计：GitHub `main` 当前仍为 `279112383bc70e9be10481b11c7d38e08538c7c7`，`feat/day-4` 没有 open/closed/merged PR，也没有 Day 4 合并提交。用户所说的“合并”已被确认至少覆盖五个步骤在功能分支上的提交收拢，但尚不能表述为已合入 `main`。
- 文档审计：README、主计划、架构、能力矩阵、Harness、Memory/Evidence/Research 策略、运行手册和安全复核统一改为“分支 CI 已通过、`main` 合并待完成”；本次文档变更发生在 `b99ca7a` 之后，只能以本地格式/diff/Secret 检查验收，后续仍需随 Day 4 合并进入 CI。
- 最终结论：远端提交与分支 CI 缺口已经关闭；剩余关闭条件仅为 Day 4 文档随实现合入 `main`、合并提交 CI 全绿，以及项目所有者用 Trace/运行证据完成复盘。满足后再把步骤 1～5 与 D4-01～D4-07 统一改为 `complete` 并进入 Day 5。

## 4. 每一步的详细实施说明

### 4.1 可控 Memory 写入纵向闭环

#### 目标与完成后的用户结果

本步只解决“什么内容可以在用户控制下成为长期记忆”。用户从已有会话选择一条或一组消息发起保存，系统生成候选摘要并展示来源、作用域和置信度；只有用户确认或编辑后，候选才能成为后续可召回的长期 Memory。系统不得默认把全部聊天永久保存，也不得让模型用一句“已记住”绕过正式写入链路。

完成后应能演示：

1. 用户在 ChatWorkbench 的会话菜单中选择“保存为记忆”。
2. 后端从当前 Workspace、Conversation、Message 引用创建候选，而不是接受浏览器提交的任意 user_id 或 workspace_id。
3. 用户查看候选摘要、来源、scope、confidence 和 write reason，随后确认、编辑或拒绝。
4. 确认后生成 Memory 当前投影和不可变的 MemoryRevision；刷新页面后仍能查看相同事实。
5. 再次保存相同或冲突内容时，系统明确执行 create、update、merge 或 reject，不静默覆盖旧事实。

#### 必须复用的现有实现

- 复用 **modules/conversations** 的 Conversation、Turn、Message、服务端 Workspace 授权和逻辑删除状态；Conversation 就是当前项目的 Thread 载体，不再创建平行的 Session/Thread 表。
- 复用 **agent_runtime/domain.py** 的 schema version、UUID、UTC、canonical JSON、错误码和预算校验模式。
- 复用 **agent_runtime/models.py** 与现有 Alembic 命名、复合外键、RESTRICT 删除和数据库 CheckConstraint 模式。
- 复用生成式 OpenAPI 契约和统一 Web API Client，不在前端手写第二套 DTO。
- 复用现有审计和 Trace 脱敏规则；Memory 正文、候选正文和来源消息不得进入普通结构化日志。

#### 后端工作清单

1. 在真实职责出现时建立 **modules/memory**，按仓库现有模式放置 domain、ports、service、adapters、models、schemas、router 和 resources；不先创建空文件或通用基类。
2. 使用一份线性 Alembic migration 创建：
   - thread_memory_states：绑定 workspace_id、conversation_id，保存消息引用、摘要、compaction revision、freshness、schema version 和乐观 revision。
   - memories：绑定 workspace_id、user_id，保存 scope、kind、current_revision_id、confidence、status 和 expires_at。
   - memory_revisions：绑定 memory_id 和递增 version，保存经最小化的 content、provenance/source ref、write reason、policy decision、editor、validity/status。
3. 对 workspace、user、conversation、message 和 memory 使用复合外键或等价的同租户约束，避免只靠应用层检查。
4. 冻结候选与修订的 typed contract。候选至少包含 candidate id、source refs、suggested content、scope、confidence、write reason、policy result 和 expiry proposal。
5. 写入服务必须从可信 Principal 与 WorkspaceScope 派生 owner，不接受模型或客户端提供的权限、user_id、最终 confidence 或 policy decision。
6. 对 candidate、confirmed、disabled、expired、deleted 等状态建立合法转换；重复确认、重复拒绝和并发修改必须使用版本或 If-Match/CAS 语义收敛。
7. 冻结敏感内容策略。认证材料、Secret、密码、Cookie、完整敏感文档、原始 chain-of-thought、低置信推断和无明确复用价值的内容必须拒绝或要求用户重新编辑。
8. 在正式事件或审计投影中只记录 memory id、revision、decision、source type、scope、稳定错误码和脱敏摘要，不记录完整正文。
9. 在 **/api/v1/memories** 范围内冻结候选、确认/编辑、拒绝、列表和详情契约；候选入口必须关联正式 Conversation/Message 资源。

#### 前端工作清单

1. 在现有 ChatWorkbench/Conversation 菜单中增加“保存为记忆”，复用当前 Workspace、会话和消息选择状态。
2. 提供候选确认界面，明确展示系统建议与用户最终确认内容的差异；不使用一个无说明的“保存”按钮直接永久写入。
3. 展示 loading、empty、validation error、forbidden、conflict、source unavailable 和 retry 状态。
4. 刷新后从正式 API 重建候选、当前 Memory 和 revision 历史，不把候选只放在 Zustand 或组件内存。
5. 用户拒绝候选后，界面和后端都保持拒绝事实，不在下一次刷新时重新显示为已确认。

#### 测试与 Scenario 清单

- 领域单元：状态转换、版本递增、scope、confidence、expiry、create/update/merge/reject、敏感内容策略和重复请求幂等。
- 数据库：fresh upgrade、复合外键、跨 Workspace 消息引用、并发确认、并发编辑、最后写入冲突和删除来源后的候选行为。
- API：未登录、viewer/member/owner 适用权限、伪造 workspace/user、无效 source ref、正文超限、非法状态转换和稳定 problem+json。
- 组件：候选确认、编辑、拒绝、冲突、错误和刷新恢复。
- E2E：从真实会话选消息 → 生成候选 → 编辑确认 → 刷新 → 查看当前 Memory 和 revision。
- 安全：日志、Trace、前端缓存和测试快照中没有 Secret、完整敏感原文或原始 chain-of-thought。

#### 本步交付证据

- 一份 Alembic migration 及 upgrade/downgrade/upgrade 证据。
- OpenAPI 生成无漂移。
- Memory 写入领域/数据库/API/组件/E2E 测试。
- 一条成功 Trace 和至少一条拒绝或冲突 Trace。
- 本文步骤状态及 feature matrix 的过程状态更新；未完成后续召回前不得把 D4-01～D4-03 整体标为 complete。

#### 明确不在本步完成

- 不做长期 Memory 召回排名和 Context 注入，它们属于步骤 2。
- 不引入 Milvus、Embedding 或把 Memory 伪装成 Knowledge/RAG。
- 不做 Evidence、Research graph、durable Checkpoint 或 HITL。

### 4.2 Memory 召回、治理与 Context manifest 闭环

#### 目标与完成后的用户结果

本步让已确认 Memory 在正确的后续 Run 中被可解释地使用，并让用户的修改、停用、过期和删除立即生效。重点不是做一个相似度 Top-K，而是证明 Runtime 为什么选中或排除某条 Memory，以及 Memory 是否改善任务而没有污染 Context。

完成后应能演示：

1. 用户在 Conversation A 确认一条长期 Memory。
2. 用户在 Conversation B 提交相关问题，同一 UnifiedAgentRuntime 通过 Context Compiler 召回该 Memory。
3. Trace 和 Workbench 显示候选、排名因素、是否注入、Token 消耗和包含/排除原因。
4. 用户修改或停用该 Memory，下一 Run 立即使用新版本或不再使用。
5. 用户删除该 Memory，下一 Run、Context manifest、搜索结果和前端均无残留。

#### 必须复用的现有实现

- 扩展 **ContextCompilerV1** 和 **ContextSourceKind/ContextDecisionReason**，不创建 Memory 专用 Prompt builder。
- 复用 **ContextManifestRecord**、SqlAlchemyContextManifestStore、Trace 查询和 TracePanel 的正式链路。
- 复用 **UnifiedAgentRuntime** 的 budget、deadline、取消、唯一终态和 Provider 调用入口。
- 复用 **agent_harness/scenarios.py**、HarnessRunner、Fake/Replay 和版本化报告结构。
- 复用 PostgreSQL 作为 Memory 当前投影和修订事实源；Day 4 不因召回方便而新增不可治理的进程内 dict。

#### 后端工作清单

1. 为 Short-term Memory 定义消息引用、摘要、compaction revision、freshness 和 schema version；摘要必须指向源 Message，而不是复制无限历史。
2. 为 Long-term Memory 定义可组合的候选检索和策略接口，输入至少包含 current goal、user/workspace scope、conversation、时间、敏感等级和 Context budget。
3. 冻结排序与决策因素：任务相关性、scope、freshness、冲突、重复、敏感度、用户反馈、status/expiry 和 Token 成本。Day 4 可以使用可解释的确定性基线，不为追求相似度提前引入向量基础设施。
4. 扩展 Context Compiler，使 Short-term 与 Long-term Memory 成为独立 source kind，并为每条候选写入 included、not available、stale、conflicted、duplicate、sensitive、disabled、expired、deleted 或 token budget 等明确决策原因。
5. Context manifest 必须保存 run id、step id、source kind/id/version、included、decision reason、token count 和 budget snapshot；只保存资源引用和脱敏摘要，不保存不必要的全文。测试必须同时断言 manifest 与实际 ModelInput 一致，不能只证明数据库存在一条 manifest。
6. 在 Provider 调用前完成预算结算；必需输入无法容纳时沿现有稳定 stop reason 失败，不能静默删除 system instructions 或可信约束。
7. 完成 Memory 搜索、详情、反馈、修改、停用、恢复启用、过期和删除 Application Service。所有写操作使用当前 revision 防止并发覆盖。
8. 删除先在 PostgreSQL 当前投影中立即过滤并失效任何派生缓存，再异步清理可重建派生数据；重复删除幂等。
9. 建立版本化 Memory Scorer 与数据集口径：write accuracy、retrieval precision、utility、pollution、conflict handling、edit effectiveness、deletion residual、input token 和 latency；报告必须记录 dataset/scorer version 和分母，避免删除残留指标随样本变化失真。

#### 前端工作清单

1. 增加 Memory 管理界面：列表、搜索、状态筛选、详情、revision、来源、修改、停用、过期和删除确认。
2. 在回答或 TracePanel 中展示“本次使用了哪些 Memory”，并能导航到具体 revision。
3. 展示未注入原因，但不得把其他 Workspace 候选或敏感正文泄露给用户。
4. 修改、停用或删除后，当前页面和下一 Run 都从服务端重新加载；不依赖乐观 UI 假装删除成功。
5. 提供 empty、forbidden、conflict、delete failed、retry 和 stale revision 状态。

#### 测试与 Scenario 清单

- 相关跨 Thread 召回成功。
- 无关 Memory 不召回或不注入。
- 过期、disabled、deleted Memory 不注入。
- 旧事实与新事实冲突时保留版本和不确定性，不静默覆盖。
- 相同内容去重，多个 scope 的优先级可解释。
- Context budget 竞争时选择稳定，manifest 原因和 token 计数一致。
- 用户修改后下一 Run 使用新 revision。
- 删除后下一 Run 不引用，deletion residual 为 0。
- 用户 A/Workspace A 无法搜索、读取或注入用户 B/Workspace B 的 Memory。
- Provider failure、取消和预算耗尽仍保持唯一终态，不因 Memory 组件异常回退为无记录成功。

#### 本步交付证据

- Memory Context source 与 manifest 契约测试。
- 真实 PostgreSQL 的跨 Thread、修改、停用、过期、删除和跨租户集成测试。
- 版本化 Memory Scenario、确定性 fixture、Memory Eval JSON/Markdown 初始报告。
- 成功召回 Trace、冲突或预算排除 Trace、删除后无残留 Trace。
- **docs/memory-policy.md** 初稿，说明写入、召回、冲突、治理、删除和回滚。

#### 明确不在本步完成

- 不把聊天摘要自动提升为长期 Memory。
- 不把 Memory 当成 Knowledge、Evidence 或 Checkpoint。
- 不做 Hybrid RAG、Embedding 调优或 Memory 向量检索基础设施。

### 4.3 Observation→Evidence→Claim 账本闭环

#### 目标与完成后的用户结果

本步把 Day 3 已有的 Tool Observation 从“不可信但可追溯的结果”提升为经过重新授权、Schema/locator 校验、来源与许可检查、规范化和去重的 Evidence，并以 Claim–Evidence 关系表达支持、反驳、上下文、覆盖和冲突。成为 Evidence 只表示来源可授权、可定位、可追溯，不表示来源天然真实。

完成后应能演示：

1. 从一个正式 Web/行业 Tool Run 和一个正式 Text2SQL Tool Run 读取 Observation。
2. Normalizer 重新检查 Workspace、来源资源、版本、locator、content hash、许可和敏感等级。
3. 合格候选生成 active Evidence；不合格候选生成明确拒绝原因，不产生伪 Evidence。
4. Research Claim 关联 supports、refutes 或 context Evidence，并计算 supported、refuted、uncertain 或 conflicted 状态。
5. Workbench 能从 Claim 反查 Evidence，再反查 origin Run、Step、ToolCall 和来源资源。

#### 必须复用的现有实现

- 复用 **modules/tools/domain.py** 的 ToolObservation、source locator、content hash 和有界 model-visible envelope。
- 复用 **modules/tools/models.py** 的 ToolCall/ToolRun 关联与审计投影。
- 复用 **modules/industry** 的 SourceItem、provider/source version、terms/readiness 和 canonical public locator。
- 复用 **modules/data_explorer** 的 QueryRun、SchemaSnapshot、validated SQL、结果 Artifact 和只读权限事实。
- 复用 ADR 0003 的统一 Evidence 模型，不为 Web、SQL、行业数据各建一套 Citation 表。
- 复用 AgentRun、AgentStep、Context manifest、Trace 和 Workspace 复合外键模式。

#### 后端工作清单

1. 在真实职责出现时建立 **modules/evidence**，包含 Evidence、locator 判别联合、Normalizer、Claim、claim_evidence、查询服务和 PostgreSQL Adapter。
2. 先建立最小 **ResearchRun 聚合壳**，只保存 workspace/research ownership、agent_run_id 和生命周期，用来给 Claim 提供真实归属；本步不引入 graph、plan、research_steps、research_events 或第二套运行历史。
3. 使用 Alembic 创建或完善：
   - evidence：workspace、kind、schema version、title、canonical URL/snapshot ref、locator、excerpt、content hash、source/retrieved time、license/terms、status、invalidation、origin refs、normalizer version、authorization snapshot 和 source resource version。
   - research_claims：workspace、research run、statement、confidence、verification status、coverage 和 conflict。
   - claim_evidence：claim、evidence、supports/refutes/context、relation version、verification status、ordering 和 origin Run/Step。
   - graph_nodes/graph_edges：只保存指向正式 Claim/Evidence/Entity 的派生关系，不保存新的事实副本。
4. locator 必须是版本化判别联合，Day 4 至少真正支持现有 Web/行业和 SQL 结果类型。JSON 字段必须先经过 typed Schema，不接受任意 metadata。
5. Web Evidence 只有在来源快照或等价的不可变来源版本满足 ADR 0003 时才能 active；若 Day 3 当前候选缺少必要 snapshot/resource version，应补足正式来源事实或返回稳定拒绝，不能降低 locator 规则。
6. SQL Evidence 必须绑定真实 QueryRun、SchemaSnapshot、allowlisted table/column 和返回行范围，不能只保存模型生成 SQL。
7. 相同 hash 只能在同一授权对象和来源版本内去重；不得跨 Workspace 或跨来源版本合并授权。
8. Evidence 状态至少支持 active、superseded、tombstoned、unavailable。底层资源删除、权限收回或许可变化后，历史关系保留最小失效说明，但 excerpt、私有对象和签名 URL 不再返回。
9. Claim 与 relation 分开建模。缺证据使用 uncertain，证据矛盾使用 conflicted；不得伪造一条 relation 来掩盖缺失。
10. 扩展 Event/Trace 的版本化词汇，记录 Evidence added/invalidated、Claim updated 和 coverage/conflict 摘要；不在 payload 中写入大段原文。
11. Evidence 与 Claim 查询每次都重新检查当前 Workspace 和底层资源权限，不只相信 Evidence 自身 workspace_id。
12. Day 4 新生成的任何用户可见 Citation 都必须落到真实 Evidence，并在当前授权下 100% 可解析；来源失效时返回明确状态，而不是悬空链接。

#### 前端工作清单

1. 增加 Evidence/Claim Inspector，显示来源类型、状态、locator 摘要、origin Run/Step/Tool、supports/refutes/context 和 uncertain/conflicted。
2. 从 Tool Inspector 的 Observation 导航到 Evidence，从 Claim 导航回 Evidence 和来源。
3. 失效来源显示“来源已失效”及最小原因，不继续提供可点击私有对象或过期签名 URL。
4. Evidence 图只渲染服务端返回的受控节点和边，不接受模型提供任意 HTML、脚本、外部图片或事件处理器。
5. 跨 Workspace、forbidden、unavailable、conflicted 和 empty 状态具有明确 UI。

#### 测试与 Scenario 清单

- 合法 Web/行业 Observation 提升成功。
- 合法 Text2SQL Observation 提升成功。
- 无效 locator、缺 source version、缺 snapshot、许可不允许、dependency failure、内容超限和未知 kind 全部拒绝。
- 相同候选幂等去重，不重复产生 Evidence。
- 相同 hash 跨 Workspace 不合并。
- Evidence lineage 可反查 Run、Step、ToolCall、source/query version 和 normalizer version。
- supports/refutes/context 与 supported/refuted/uncertain/conflicted 的组合规则可重复。
- 缺证据 Claim 明确 uncertain；冲突 Evidence 不被 finalizer 改写为 supported。
- Evidence 失效后 excerpt/签名 URL 不再可读，历史关系仍可解释。
- 图节点/边可反查正式资源，跨 Workspace 查询为 0。
- Day 4 新生成的 Citation 全部可解析到当前用户有权查看的 Evidence；失效 Citation 返回稳定状态，不形成悬空链接。

#### 本步交付证据

- Evidence/Claim migration、typed locator/OpenAPI 契约和权限矩阵。
- 正常、拒绝、去重、失效、冲突和跨租户测试。
- Web/行业与 SQL 各一条正式 lineage Trace。
- Evidence/Claim 规则 Scorer 初始报告。
- 如 locator、状态或关系语义偏离 ADR 0003，先更新 ADR 并记录迁移和回滚；无偏离则不为“有改动”而修改 ADR。

#### 明确不在本步完成

- 不实现 Day 5 文档/PDF/图片/表格的真实 Knowledge locator。
- 不宣称 Day 6 Citation 可解析率或多模态引用门禁已经完成。
- 不实现 Verifier 或自动 revise。

### 4.4 ResearchBrief 与唯一 Research L3 graph 闭环

#### 目标与完成后的用户结果

本步把已有 L2 Tool loop 组织成一个有明确问题范围、预算、Evidence/Claim 和可解释草稿的 Research L3。LangGraph 只负责 Deep Research 外层 typed graph；模型/工具内循环、预算、事件、终态、权限和 Trace 仍由统一 Runtime/Harness 执行。

完成后应能演示：

1. 用户创建 Research Run，系统先澄清问题并保存 ResearchBrief。
2. 用户确认范围、排除项、完成标准和预算后进入 plan。
3. research loop 复用 Day 3 Tool surface，获取 Observation 并通过步骤 3 的 Normalizer 形成 Evidence。
4. 系统生成 Claim、coverage、conflict、outline 和带不确定项的可解释草稿。
5. 用户取消或遇到预算/Tool/Provider 失败时，Run 以统一 stop reason 收敛；Worker hard stop 在 Day 4 明确失败，不冒充可恢复。

#### 必须复用的现有实现

- 复用 **AgentRunType.RESEARCH**、AgentRun/Step/Event/State/Budget、统一 SSE 和 Trace。
- 复用 **UnifiedAgentRuntime**、ToolRegistry/ToolExecutor、ContextCompiler、Tool Observation 和正式 Harness profile。
- 复用 Job/Outbox/Dispatcher/Worker 承载长任务，不在 HTTP 请求内同步执行 Research。
- 复用 **agent_harness/scenarios.py** 和 HarnessRunner；Fake/Replay 只替换 Provider/Tool 边界。
- 复用 Evidence/Claim 服务；LangGraph 节点只能调用统一 Runtime 或 Domain/Application Port，不得直接访问 ToolExecutor、Provider SDK、Tool Adapter、数据库连接或具体外部客户端。ToolExecutor 只能由 Runtime 经既有权限、预算、幂等和审计校验后调用。

#### 依赖引入检查结果

1. 已通过最小 import、typed graph、异步 Runtime、Python 3.13/Pydantic v2 和真实测试确定 `langgraph==1.2.11`。
2. 精确依赖已写入 backend workspace 与 `uv.lock`，没有无上限声明。
3. PyPI/官方 pyproject、本地 wheel metadata 和依赖树共同确认许可证；`uv audit --locked` 与 Node audit 无已知漏洞，当前分发方式不需要额外 NOTICE 归属文本。
4. LangGraph import 只位于 `workflows/research` adapter；普通聊天、CRUD、Memory、Evidence 和入库模块不依赖它。
5. 暂停新 Run、保留统一执行/Evidence/Claim 事实、应用先回滚、明确接受 L3 Brief/Plan/Draft 数据损失后才 downgrade/卸载的顺序已写入 [Research L3 状态机](../research-state-machine.md)。

#### 后端工作清单

1. 在真实职责出现时建立 **modules/research** 与 **workflows/research**，前者拥有 Research 业务事实，后者只做 LangGraph adapter。
2. 扩展步骤 3 建立的 research_runs 聚合壳，并使用 Alembic 增加版本化 ResearchBrief、research_plans 及 L3 草稿/报告所需事实；通过 agent_run_id 扩展统一 AgentRun，不建立 research_steps、research_events 或 research_checkpoints 第二套历史。
3. 冻结 ResearchBrief：original question、confirmed scope、exclusions、completion criteria、budget、revision、confirmed by/at。
4. 冻结 ResearchState：schema/run/scope、brief、plan/current node、pending actions、Evidence/Claim/Artifact refs、budget/step/revise counters、cancel flag、stop reason 和脱敏错误摘要。
5. Graph 只包含 Day 4 节点：clarify scope、write brief、plan、research loop、normalize evidence、synthesize claims、outline、draft。
6. research loop 只能把推进请求交给统一 Runtime；节点不得直接调用 ToolExecutor，也不得复制 model → tool → observation 循环。
7. 每个节点通过正式 Application/Domain Port 读写业务事实，并产生统一 Agent Event/Step；Event sequence 与 Run revision 保持单调。
8. Research 创建 API 返回 202，并要求 Idempotency-Key；Application Service 在同一事务建立 ResearchRun、AgentRun、Job 和 Outbox，重复请求返回同一资源。Worker 从 PostgreSQL 重新装载可信 Runtime Context。
9. 取消传播到 Job、graph 和 Runtime 的协作式安全点；取消后不继续提交 Claim 或草稿 Artifact。
10. max steps、max concurrency、Token、费用、deadline、Tool allowlist 和 scope 都来自可信 Runtime/Harness profile；模型不能扩大。
11. ResearchRun、Brief、Plan、节点进度、Evidence、Claim、统一 Event 和草稿状态必须落在 PostgreSQL，不允许依赖进程内字典保存业务事实。Day 4 不实现 durable graph resume；Worker hard stop 或进程丢失应由现有 Terminalizer/Reconciler 收敛为明确 failed/cancelled，并保留已提交 Evidence/Claim，不能从头静默重跑。
12. 暂不开放 resume/HITL API；若现有公共契约含 resume 路径，应返回稳定 readiness/error，而不是假成功。

#### 前端工作清单

1. 提供 Research 创建与澄清界面，必须让用户看见 original question、scope、exclusions、completion criteria 和 budget。
2. 用户确认 brief 后才能启动 plan；Planner 修改建议需要生成新 revision，不能覆盖用户确认内容。
3. Research 时间线显示节点、正式 Step/Event、Tool、Evidence/Claim、usage、预算和 stop reason，不展示原始 chain-of-thought。
4. 草稿明确标为 L3 explainable draft，不显示为 verified/complete Report。
5. loading、clarification required、running、partial evidence、uncertain、failed、cancelled、budget exhausted 和 provider/tool unavailable 状态可区分。

#### 测试与 Scenario 清单

- 正常 L3：澄清 → brief → plan → 两种 Tool → Evidence → Claim → draft。
- 不需要澄清与必须澄清两种路径。
- Planner scope drift 被拒绝或生成显式 brief revision。
- Tool timeout/failure、Provider timeout/429/invalid response。
- Evidence 缺失、locator 拒绝、多源 conflict 和 uncertain Claim。
- max steps、Token、费用、deadline 与取消。
- Prompt injection 不能改变 Tool surface、WorkspaceScope、预算或 system instructions。
- 用户 A 无法访问用户 B 的 ResearchRun、Evidence、Claim、草稿或 Trace。
- Worker hard stop 明确失败且唯一终态，不宣称 resume；已提交业务事实不重复。
- 确定 Fake 下同一 Scenario 的关键 Event 骨架、brief、plan、Claim 状态和 stop reason 可重复。

#### 本步交付证据

- LangGraph 兼容性、锁文件、依赖/许可证复核。
- Research migration、OpenAPI/SSE typed contract 和稳定错误码。
- 正式 Job/Outbox/Worker → UnifiedAgentRuntime → graph adapter → Evidence/Claim 草稿链路。
- L3 正常、失败、预算、取消、跨租户和 hard-stop 明确失败测试。
- **docs/research-state-machine.md**，明确 L3/L4/L5 边界、状态、事件、失败和回滚。

#### 明确不在本步完成

- 不实现持久 interrupt/resume、ApprovalRequest/Decision 或副作用账本；它们属于 Day 5。
- 不把普通 State 行保存称作 Agent Checkpoint。
- 不实现 Verifier、bounded revise 或 complete/partial/uncertain 最终 Report；它们属于 Day 6。
- 不引入多 Agent、specialist handoff 或并行角色系统。

### 4.5 Workbench、对照 Eval 与 Day 4 门禁收口

#### 目标与完成后的用户结果

本步不是最后补一个页面，而是把前四步的正式事实、失败语义和评测证据串成可解释的学习闭环，并按 Definition of Done 逐项决定 Day 4 是否真的完成。

完成后用户应能在同一 Workbench 中：

1. 查看 L0、L2、L3 同题结果、Trace、步骤、费用和延迟。
2. 查看 Memory 候选、确认、召回、冲突、修改、停用、删除和实际 Context 注入。
3. 从 Tool Observation 导航到 Evidence，再导航到 Claim、coverage 和 conflict。
4. 刷新后由正式 API/Event/Trace/manifest 重建全部状态。
5. 看到失败、取消、预算耗尽、uncertain 和来源失效，而不是统一显示“已完成”。

#### Workbench 工作清单

1. 扩展现有 **ChatWorkbench.tsx**、**TracePanel.tsx** 和 chat-workbench-model；根据职责拆分 Memory、Evidence/Claim 和 Research 面板，避免形成万能 Page。
2. 前端只消费生成的 OpenAPI 类型、统一 Agent Event、Trace、Context manifest 和正式资源 API。
3. Memory 面板显示 candidate、revision、source、scope、confidence、included/excluded reason、token count、feedback 和 status。
4. Evidence/Claim 面板显示 Observation→Evidence lineage、locator/status、supports/refutes/context、coverage/conflict 和 uncertain。
5. Research 时间线显示 brief revision、plan、node/Step、Tool、Evidence/Claim、budget、usage、stop reason 和草稿状态。
6. 所有面板支持关联导航、刷新恢复以及 loading/empty/error/forbidden/cancelled/budget exhausted/uncertain/unavailable。
7. 不建立只供展示的 trajectory、Memory、Evidence、Claim 或分数缓存；Zustand 只保存短期 UI 状态。

#### Eval 与数据集工作清单

1. 保留 Day 2/3 的 24 条版本化 Scenario、fixture、Trace snapshot 和报告作为不可变基线；另建一个明确版本号的 Day 4 数据集，禁止把新 case 混入旧版本。
2. 新 Day 4 数据集必须增加覆盖以下行为的 Scenario：
   - Memory candidate、确认、编辑、merge/reject。
   - 相关召回、无关污染、冲突、过期、停用和删除残留。
   - Evidence 正常提升、locator/许可/权限拒绝、去重、失效和 conflict。
   - Research scope、Tool/Provider failure、Evidence 缺失、预算和取消。
3. 数据集继续记录 runtime、harness、model、prompt、context、toolset 和 policy/version；不能只写问题与答案。
4. Memory Scorer 与 Evidence/Research Scorer 分开：
   - Memory：write accuracy、precision/utility、pollution、conflict、edit effectiveness、deletion residual、Token/latency。
   - Evidence/Research：Evidence validity、lineage、Claim support、coverage、conflict、scope preservation、task result、steps、Token、费用和延迟。
5. 完成同一问题 L0/L2/L3 对照。报告必须说明增加 Memory/Evidence/L3 带来的质量变化和成本，而不是默认复杂流程更好。
6. 使用规则 Scorer、确定性 fixture 和人工抽样；LLM judge 只能补充，不能作为唯一门禁。
7. 机器可比较 JSON 与学习复盘 Markdown 使用同一 case/run/version 和正式 Trace 引用。
8. 对相同输入执行 Memory off/on 消融，分别报告任务质量、Memory 污染、冲突处理、Context Token 和延迟；没有对照结果时不能宣称 Memory 带来净收益。

#### 全量测试与质量门清单

- Python format、Ruff、mypy、全量 pytest、wheel/sdist build 和 dependency audit。
- 真实 PostgreSQL/Redis/MinIO 强制门禁无 skip；fresh migration 与支持的 downgrade/upgrade 往返。
- OpenAPI 生成无漂移，Agent SSE/未知事件兼容通过。
- Web format、lint、typecheck、Vitest、production build 和 Node audit。
- 关键 Playwright：Memory 写入/召回/删除、Research L3、Workbench 关联导航和刷新恢复。
- Gitleaks 当前受控路径与完整历史；新增依赖许可证、NOTICE、来源/隐私复核。
- 日志/Trace/快照不含 Secret、敏感全文或原始 chain-of-thought。
- 跨 Workspace Memory/Evidence/Research 泄漏为 0。
- Memory deletion residual 为 0。
- Day 4 新生成 Citation 可解析率为 100%，且每条都通过当前授权复核。
- 覆盖率门槛：新增核心 Domain/Application 代码不低于 90%，后端整体不低于 80%，前端关键 hooks/state 分支不低于 75%；任何例外都需记录具体原因、风险和复核人。
- Memory/Evidence/Research 的 retention、隐私删除、备份与恢复策略有文档和自动化验证；Day 7 的完整隔离恢复演练仍按主计划执行，不在 Day 4 虚报完成。
- 所有 Run 保持唯一终态和明确 stop reason。
- 无静默 Mock、Router/graph 直连 Provider、第二 Tool loop 或图外 Research。

#### 文档与交付物清单

- **docs/memory-policy.md**：写入、召回、冲突、治理、删除、隐私和回滚。
- **docs/research-state-machine.md**：L3 state/graph/events/budget/error，以及与 Day 5/6 的边界。
- **docs/learning-log/day-4.md**：逐步证据、失败、知识突破、保留/回退结论和复盘题。
- Day 4 Scenario、fixtures、snapshots 和 Memory/Research 对照报告。
- README 与必要 Runbook：启动、Provider/readiness、故障、限制、回滚和本地演示。
- feature matrix D4-01～D4-07 的实际状态和证据链接。
- 如架构、Evidence schema 或 graph 边界发生实质变化，更新对应 ADR、主计划版本和变更记录；没有变化时不做形式化改写。

#### 本步关闭条件

只有以下条件同时满足，Day 4 才能关闭：

1. 主计划 Day 4 五条验收门禁全部通过。
2. 全局 Definition of Done 和 Agent 追加 Definition of Done 的所有适用项通过。
3. 每个 N/A 都有具体理由、复核人和日期，且没有把用户旅程、权限、失败/恢复或安全标为 N/A。
4. 本地全量门禁、最终功能分支 CI 和 `main` 合并提交 CI 均通过，不能以定向绿色测试或仅分支绿色替代。
5. 学习者能用 Trace 和运行证据回答复盘题，而不是只复述名词。
6. D4-01～D4-07 才从过程状态更新为 complete；任何一项未满足则不进入 Day 5。
7. 完成步骤 1～5 与 D4-01～D4-07 的双向映射审计；每个能力项至少有一个实现证据、一个测试证据和一个用户可见验收证据，且不存在无人负责的门禁。

## 5. 跨步骤复用与依赖顺序

依赖顺序固定为：

    Day 3 Observation/Tool loop/Context Compiler v1
      → 步骤 1：可控 Memory 写入
      → 步骤 2：Memory 召回与 Context manifest
      → 步骤 3：Evidence/Claim 账本
      → 步骤 4：ResearchBrief 与 L3 graph
      → 步骤 5：Workbench/Eval/DoD

不允许为了并行开发破坏语义顺序：

- 前端可以依据已冻结的 OpenAPI 和 Event contract 并行实现，但不能用 Mock 数据先宣布功能可用。
- Evidence/Claim 可以在 Memory 工作后期开始编码，但步骤 3 关闭前必须使用正式 Day 3 Observation 和真实 PostgreSQL。
- Research graph 可以先做依赖兼容性试验，但步骤 3 的 Evidence/Claim typed input 未冻结前不能形成第二套临时 Evidence。
- Eval 数据和 Scorer 可以逐步增加，最终报告必须引用每一步的正式 Run/Trace，而不是事后手工编造摘要。

## 6. Definition of Done 逐步落点

| DoD 类别 | 步骤 1 | 步骤 2 | 步骤 3 | 步骤 4 | 步骤 5 |
|---|---|---|---|---|---|
| 真实用户旅程 | 保存/确认 Memory | 召回/修改/删除 | Evidence/Claim 反查 | 创建 Research L3 | 完整 Workbench |
| 正常/边界/失败 | 写入策略与并发 | 冲突/预算/删除 | 拒绝/失效/冲突 | Tool/Provider/取消 | 全量回归 |
| 权限与租户 | source 与 owner | 搜索与注入 | 底层资源重授权 | Run/Claim/草稿 | 跨模块 E2E |
| Migration/契约 | Memory 表/API | Context/manifest | Evidence/Claim 表/API | Research 表/SSE | fresh migration/OpenAPI diff |
| Trace/错误码 | write decision | recall decision | lineage/status | graph/stop reason | 关联导航与报告 |
| 生命周期/删除 | revision/source | disable/expiry/delete | tombstone/unavailable | cancel/failure | 统一恢复说明 |
| 威胁与隐私 | 敏感写入策略 | Context 污染 | 不可信 Evidence | Prompt injection | Secret/快照扫描 |
| 许可证/供应链 | 新依赖复核 | 无额外系统 | 来源许可 | LangGraph/lock | audit/NOTICE |
| 可重复 Eval | 写入 cases | Memory Scorer | Evidence Scorer | L3 cases | L0/L2/L3 报告 |
| README/Runbook | 写入限制 | 删除/回滚 | 失效语义 | L3 故障 | 一键演示与总回滚 |
| 干净环境 | 局部门禁 | 局部门禁 | 局部门禁 | 局部门禁 | 完整 CI |

## 7. 每一步的关闭记录模板

每完成一步，在本文对应位置追加以下记录，不覆盖原计划：

| 字段 | 必填内容 |
|---|---|
| 状态 | planned、thin_slice、implemented_pending_verification 或 complete |
| 实现提交 | 精确 commit；未提交时写工作树，不冒充干净 CI |
| 正常证据 | 测试、Trace、浏览器旅程和正式数据引用 |
| 失败证据 | 至少一个边界/失败/权限/恢复或明确失败场景 |
| Eval 证据 | dataset/case/version、Scorer 和报告路径 |
| DoD 复核 | 每个适用项结论；N/A 理由、复核人、日期 |
| 遗留边界 | 明确归属后续步骤或 Day 5/6/7，不能静默隐藏 |
| 回滚办法 | migration、契约、依赖、profile 或功能入口的回退方式 |

步骤状态不能由文件数量、代码行数或页面截图决定。状态变化必须能从用户旅程追到 Application Service、统一 Runtime/Harness、正式数据、Event/Trace/manifest 和 Scorer。
