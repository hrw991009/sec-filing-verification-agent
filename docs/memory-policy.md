# Memory 写入、召回与治理策略

> 版本：2.0（Day 4 步骤 1～2）；更新日期：2026-08-20；当前范围：候选与确认、Short/Long-term Memory、确定性召回、Context manifest、反馈、修改、停用、过期、删除与评测；明确不含：Embedding/向量检索、Knowledge/RAG、Evidence、Checkpoint 和自动长期保存全部聊天。

## 1. 目标与正式链路

Memory 是用户明确控制、可追溯且有版本的事实，不等同于聊天历史、Agent Run State、Checkpoint、Knowledge 或 Evidence。模型不能用一句“已记住”绕过写入策略，也不能把召回内容提升为可信系统指令。

唯一正式链路是：

```text
Conversation/Message
  → MemoryCandidate + CandidateSource
  → 用户编辑、确认或拒绝
  → Memory 当前投影 + MemoryRevision + RevisionSource
  → 下一次 queued Run 从 PostgreSQL 重新授权并召回
  → ContextCompilerV1 结算预算并生成 ModelInput + Context manifest
  → Provider → Event/Trace
```

候选正文不参与回答。只有当前授权下可见的正式 Memory 当前 Revision 才会成为召回候选；召回器异常会使 Run 明确失败，不会静默降级成“无 Memory 的成功回答”。生产、Harness 和 Workbench 复用同一 `UnifiedAgentRuntime`、Context Compiler 与 Trace，不建立第二套调用路径。

## 2. 分层与事实源

PostgreSQL 是唯一业务事实源，Redis 只承载短期事件，不保存长期 Memory：

| 实体 | 职责 | 关键不变量 |
|---|---|---|
| `thread_memory_states` | 同一 Conversation 的有界 Short-term 摘要投影 | 绑定 Workspace、owner、Conversation 和 1～8 个正式 Message；revision/compaction revision 单调递增 |
| `memory_candidates` / `memory_candidate_sources` | 用户决议前的候选及来源 | 候选与正式 Memory 分离；来源必须属于同一 Workspace/owner/Conversation |
| `memories` | Long-term Memory 当前投影 | 保存当前 Revision、资源 CAS revision、scope、kind、status、confidence 和 expiry |
| `memory_revisions` / `memory_revision_sources` | 内容版本与 lineage | 正常修改 append-only；当前 Revision 才可召回；来源不复制 Message 正文 |
| `memory_feedback` | 当前用户对具体 Revision 的反馈 | 同一 actor/Revision 只保留一份当前反馈；反馈不改写 Memory 正文 |

Short-term Memory 只服务同一 Thread，包含摘要、来源 Message 引用、freshness 和 compaction revision。Long-term Memory 可跨 Thread 召回，但必须经过用户确认和每次 Run 的重新授权。两者都作为不可信 `user` 数据进入 ModelInput，不得成为 `system` 指令；它们也不等于 Context manifest、State 或 Checkpoint。

本阶段不引入向量库、Embedding 或进程内业务事实缓存。确定性词法基线的目标是让选择、排除和预算行为可解释、可重复，再由 Eval 决定是否需要后续检索升级。

## 3. 写入、权限与并发

- `workspace_id` 来自受保护路由；`user_id`、成员角色和 owner 来自认证 Principal，客户端和模型不能覆盖。
- 候选必须引用当前 Workspace、同一 Conversation、未逻辑删除的正式 Message；每个候选允许 1～8 个不重复来源。
- 创建候选需要 `CREATE_RESOURCE`，读取需要 `VIEW`。`user` scope 仅 owner 可读；`workspace` scope 可由同 Workspace 成员读取，但只有 owner 可修改、停用、启用或删除。
- 助手单一来源必须由用户编辑；认证材料、密码、API key、token、Cookie、私钥等敏感模式在候选阶段拒绝，并在召回前再次检查。
- 正文规范化后为 1～4000 个字符且不含 NUL；expiry 写入时必须是未来 UTC 时间。
- 候选创建使用 `Idempotency-Key`；确认/拒绝使用候选 `If-Match`。Memory 修改、停用、启用、删除和反馈使用资源 `If-Match`，stale revision 返回 `MEMORY_CONFLICT`。
- `create` 产生 content v1；`update/merge` 追加 content vN+1。治理状态改变只递增资源 revision，不伪造新的内容版本。

候选基线策略保持确定性：用户来源允许，用户/助手混合来源允许但必须展示，助手单一来源要求编辑，敏感内容直接拒绝。用户确认是写入授权，不能由模型或后台任务代替。

## 4. 召回授权、排序与冲突

每个 queued Run 在执行前从 PostgreSQL 重载当前状态。查询边界只允许：当前用户自己的 Memory，以及同 Workspace 的 `workspace` scope Memory；其他用户的 `user` scope 和其他 Workspace 数据不得进入候选集合。

Long-term Memory 最多考虑 20 条，依次执行：

1. 重新检查 status、expiry、当前 Revision validity、来源 Conversation、敏感内容和当前用户反馈。
2. 对当前 goal 与 Memory 正文按英文/数字 token 和中文字符计算词法覆盖率；相关性为 0 时排除。
3. `fact`/`note` 超过 365 天未更新时视为 stale；`preference`/`instruction` 不套用该固定时限。
4. 按“可注入、相关性、helpful 反馈、user scope、更新时间、Memory id”确定性排序。
5. 正文规范化后完全相同的候选标记 `excluded_duplicate`；同 kind 且 token Jaccard 相似度至少 0.6、但正文不同的低优先候选标记 `excluded_conflicted`，不静默覆盖高优先事实。
6. 最多 6 条 Long-term Memory 可进入 Context；其余或无法容纳者标记 `excluded_token_budget`。

当前排除原因全集为：`not_available`、`excluded_not_relevant`、`excluded_stale`、`excluded_conflicted`、`excluded_duplicate`、`excluded_sensitive`、`excluded_disabled`、`excluded_expired`、`excluded_deleted`、`excluded_negative_feedback` 和 `excluded_token_budget`。`not_helpful` 会让该 Revision 在后续 Run 中排除；`helpful` 只提高同等候选中的排序，不越过权限、敏感、状态、时效、冲突或预算规则。

## 5. Context 与 Trace 不变量

`ContextCompilerV1` 的稳定可选顺序是 Conversation summary → Short-term Memory → 已排序 Long-term Memory；之后仍按既有顺序加入附件、当前问题和 Tool Observation。System instructions、Runtime projection、当前问题等必需输入不会为了 Memory 静默删除；必需输入或输出保留无法满足时沿现有预算 stop reason 失败。

每条 Long-term Memory 的 manifest 项至少保存：Memory id、Revision id、content version、scope、relevance score、feedback score、included、decision reason、message role 和 token count。manifest 不保存 Memory 正文；允许保存非敏感内容 digest。被排除项的 `estimated_token_count=0` 且没有 `message_role`；被包含项的 token 总数必须与实际 ModelInput 和预算快照一致。

Provider 只看到明确标为 untrusted historical data 的 Memory 消息。TracePanel 从正式 Trace 展示使用或排除原因，并可按 Memory id 导航到当前授权下的治理详情；前端不缓存另一份事实，也不能据旧 Trace 绕过当前删除或权限状态读取正文。

## 6. 治理与删除语义

正式 API 支持列表/搜索/状态、scope、kind 筛选，详情/Revision，反馈，修改，停用，恢复启用和删除。所有 UI 操作等待服务端响应并重新加载；409、删除失败或网络失败不能用乐观 UI 假装成功。

- 修改：追加 Revision、更新当前投影并递增资源 revision；下一次 Run 只读取新 Revision。
- 停用/启用：递增资源 revision；停用项保留可治理记录但召回时明确排除。
- 过期：当前时间达到 `expires_at` 后立即从召回排除，manifest 原因为 `excluded_expired`。
- 删除：在单个 PostgreSQL 事务内把当前投影 tombstone、将 Revision 正文改为 `[deleted]` 并 withdraw、删除 Revision source、清空已决候选中的建议正文；默认列表、详情和下一次召回均不可见。
- 重复删除：同一 owner 对已删除资源重复请求返回成功，即使携带删除前的原 revision；不存在的或未授权资源仍返回 404。

当前实现没有向量索引或业务缓存，因此删除不依赖异步清理才能生效。数据库备份的保留与彻底擦除仍继承项目统一备份策略；Day 7 隔离恢复演练完成前，不把当前在线数据 residual=0 表述为备份介质已擦除。

## 7. HTTP 失败与隐私

端点位于 `/api/v1/workspaces/{workspace_id}/memories`，OpenAPI 是 Web DTO 的唯一生成源。错误使用统一 `application/problem+json` 与 trace id：

| code | HTTP | 恢复动作 |
|---|---:|---|
| `MEMORY_NOT_FOUND` | 404 | 资源不存在、已删除或当前用户无权查看；返回列表重选 |
| `MEMORY_SOURCE_NOT_FOUND` | 404 | 来源已失效；从仍有效 Message 新建候选 |
| `MEMORY_CONFLICT` | 409 | 重新 GET 最新资源 revision 后由用户再次决定 |
| `MEMORY_CANDIDATE_EDIT_REQUIRED` | 422 | 编辑助手单一来源建议后重试 |
| `MEMORY_REQUEST_REJECTED` | 422 | 修正正文、expiry 或结构，不自动降级 |
| `MEMORY_UNAVAILABLE` | 503 | 保持当前 Run/操作失败状态，在事实源恢复后安全重试 |

普通日志、错误 detail、AuditLog 和 Context manifest 不记录候选/Memory/来源正文、认证材料或原始 chain-of-thought。AuditLog 只记录资源 id、动作、revision、scope、来源数量、结果和 trace id；敏感或 deleted 候选不暴露内容 digest。

## 8. 评测口径

版本化数据集 `day4-memory-v1` 通过共享 `agent_harness` Scenario loader 读取，确定性 scorer `memory-scorer-v1` 分别报告 write accuracy、retrieval precision、utility、pollution、conflict handling、edit effectiveness、deletion residual、平均 input token 和平均 latency。每项比率必须携带 numerator/denominator；删除样本的固定分母不能随残留数变化。

JSON/Markdown 基线报告来自冻结 observation fixture，不冒充真实 Provider 质量结论。真实 PostgreSQL 集成测试负责证明跨 Thread 召回、修改、停用、过期、删除、反馈和跨 Workspace 隔离；Context 契约测试同时断言 manifest 与实际 ModelInput 一致。

## 9. 发布、回滚与当前证据

应用发布顺序为 migration upgrade 后部署兼容应用。若要回退步骤 2，先停止新的 Memory 治理写入并备份 PostgreSQL；回退应用到步骤 1 代码后，只有明确接受丢失 feedback、资源 revision、expiry 与删除治理数据时，才能将 migration `d7c91e4a62bf` downgrade 到 `b4d8f3a9c210`。不得为迁就旧代码直接降级生产库，也不得在回退时重新暴露已 tombstone 的正文。

恢复后至少重跑 fresh upgrade、资源 CAS、跨 Thread 召回、跨 Workspace 负向、manifest/ModelInput 一致性、删除 residual、OpenAPI、组件和浏览器旅程。

实现与测试入口：

- Runtime/Context：`apps/backend/tests/modules/agent_runtime/test_context_v1.py`
- Memory Domain/API/Eval：`apps/backend/tests/modules/memory/`
- 真实 PostgreSQL 与删除残留：`apps/backend/tests/integration/test_memory_write_postgres.py`
- Migration：`apps/backend/tests/integration/test_migration_smoke.py`
- Web/API/Trace：`apps/web/src/chat/MemoryWorkspace.test.tsx`、`chat-api.test.ts`、`TracePanel.test.tsx`
- 浏览器旅程：`tests/e2e/app-shell.spec.ts`
- 版本化数据集与报告：`evals/scenarios/day4-memory-v1.json`、`evals/reports/day4-memory-v1.json`、`evals/reports/day4-memory-v1.md`

当前已通过本地验收，等待提交后的干净 CI 与项目所有者复核。只有远端复核也通过后，才能把步骤 2 或 D4-02/D4-03 标记为 `complete`；D4-07 还必须等待 Research 预算与策略边界完成。
