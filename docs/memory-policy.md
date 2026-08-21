# Memory 写入与治理策略

> 版本：1.0（Day 4 步骤 1）  
> 更新日期：2026-08-20  
> 当前范围：候选生成、用户确认/编辑、create/update/merge/reject、Revision 与来源追溯  
> 后续范围：召回、Context 注入、停用/过期/删除及 residual 评测在 Day 4 步骤 2 完成

## 1. 目标与边界

Memory 是用户明确控制、可追溯且有版本的长期事实，不等同于聊天历史、Short-term Memory、Context manifest、Agent Run State、Checkpoint、Knowledge 或 Evidence。系统不得因为模型声称“已记住”而写入 Memory，也不得默认永久保存全部会话。

Day 4 步骤 1 的唯一正式写入链路是：

```text
已持久化 Conversation/Message
  → MemoryCandidate + CandidateSource
  → 用户编辑、确认或拒绝
  → Memory 当前投影 + 不可变 MemoryRevision + RevisionSource
  → AuditLog（仅脱敏元数据）
```

候选和正式 Memory 必须分开。候选正文不参与后续回答；只有 `confirmed` Memory 的当前有效 Revision 才具备进入步骤 2 召回策略的资格。步骤 1 不执行召回、排序、Embedding 或 Context 注入。

## 2. 数据所有权与事实源

PostgreSQL 是写入事实源：

| 实体 | 职责 | 关键不变量 |
|---|---|---|
| `thread_memory_states` | 当前 Conversation 的有界 Short-term 摘要投影 | 绑定 Workspace、owner 和 Conversation；1～8 个来源；revision/compaction revision 单调递增 |
| `memory_candidates` | 尚待用户决定或已决议的候选 | 幂等键只保存 SHA-256；状态与决议字段由数据库约束保持一致 |
| `memory_candidate_sources` | 候选到正式 Message 的有序引用 | Message 和候选必须属于同一 Workspace/owner 边界 |
| `memories` | 长期 Memory 的当前投影 | 当前 Revision、版本、scope、kind、confidence、status 和 expiry 受约束 |
| `memory_revisions` | append-only 的内容与决策历史 | `(memory_id, version)` 唯一；记录 editor、write action/reason、policy decision 和 validity |
| `memory_revision_sources` | Revision 到正式 Message 的有序 lineage | 不复制来源正文；复合外键阻止跨 Workspace 拼接 |

Conversation 是 Thread 载体，不另建第二套 Session/Thread。Redis 不保存长期 Memory 事实；本步骤也不引入向量库或进程内业务事实缓存。

`scope=user|workspace` 是已冻结的内容作用域。步骤 1 的列表和详情仍按当前登录用户过滤，即使候选选择 `workspace` 也不会提前向其他成员共享；Workspace 级召回与共享授权在步骤 2 冻结并测试前不得开放。

## 3. 可信输入与权限

- `workspace_id` 来自受保护路由，`user_id`、成员角色和 owner 来自认证 Principal；客户端和模型不能提交或覆盖它们。
- 候选必须引用当前 Workspace 内、属于同一 Conversation、尚未逻辑删除的正式 Message；每个候选允许 1～8 个不重复来源。
- 创建候选需要当前角色具备 `CREATE_RESOURCE`，查看需要 `VIEW`。跨 Workspace、伪造 Conversation/Message 或其他用户的候选/Memory 对调用者表现为拒绝或不可见，不泄露资源是否存在。
- 确认时再次检查来源仍可用；候选创建后若 Conversation 或 Message 已失效，返回 `MEMORY_SOURCE_NOT_FOUND`，不得使用候选中的旧副本绕过权限。
- 正文规范化后必须为 1～4000 个字符、不得包含 NUL；expiry 若提供必须是未来 UTC 时间。

## 4. 候选策略

步骤 1 使用确定、可测试的基线策略，不把模型判断当成授权：

| 来源/内容 | decision | confidence | 行为 |
|---|---|---:|---|
| 仅用户消息 | `allowed / user_authored` | 0.95 | 可在用户确认或编辑后写入 |
| 用户与助手混合 | `allowed / mixed_sources` | 0.80 | 展示来源后由用户决定 |
| 仅助手消息 | `requires_edit / assistant_only_requires_edit` | 0.60 | 用户必须修改建议正文，原样确认返回 `MEMORY_CANDIDATE_EDIT_REQUIRED` |
| 命中认证材料、密码、API key、token、Cookie、私钥等敏感模式 | `rejected / sensitive_content` | 0 | 候选直接记为 rejected，`suggested_content` 为 `NULL`，不可确认 |

多条来源的建议正文按来源顺序确定性构造，供用户审阅，不是模型生成的最终事实。当前敏感规则是阻断基线，不代表完整 DLP；任何漏检都不能成为把正文写入日志、Trace 或前端持久缓存的理由。

## 5. 状态、幂等与并发

候选仅允许以下状态流转：

```text
candidate ──confirm(create/update/merge)──> confirmed
    │
    └────────────────reject───────────────> rejected

sensitive content ──policy───────────────> rejected
```

- 创建候选要求 `Idempotency-Key`。同一 Workspace、owner、key 和相同请求返回原候选并标记 `created=false`；同 key 不同请求返回 `MEMORY_CONFLICT`。
- 确认和拒绝要求 `If-Match` 候选 Revision。成功决议将候选 Revision 加一；并发决议至多一个成功。
- 完全相同的确认重试通过 resolution fingerprint 返回同一 Memory，`created=false`；内容或动作不同的重复确认返回 `MEMORY_CONFLICT`。
- `create` 产生 Memory v1；`update` 和 `merge` 都要求目标 Memory id 与当前 `target_revision`，成功后追加 vN+1 并更新当前投影。旧 Revision 不改写。
- `update` 表示用户用新的完整内容替换当前解释；`merge` 表示用户已在确认框中给出合并后的完整内容。系统不在服务端隐式拼接或静默覆盖。
- 重复拒绝幂等返回 rejected 候选；已拒绝候选不能再次确认。

## 6. HTTP 契约与稳定失败

正式端点位于 `/api/v1/workspaces/{workspace_id}/memories`：候选创建/列表/详情、确认、拒绝，以及 Memory 列表/详情。OpenAPI 是 Web DTO 的生成源；浏览器不得维护第二套请求结构。

| code | HTTP | 含义与恢复 |
|---|---:|---|
| `MEMORY_NOT_FOUND` | 404 | 候选、Memory 不存在或不属于当前 owner；返回列表后重选 |
| `MEMORY_SOURCE_NOT_FOUND` | 404 | 来源已失效；回到会话重新选择有效消息 |
| `MEMORY_CONFLICT` | 409 | 候选/目标 Revision 或幂等请求冲突；重新加载后再次决定 |
| `MEMORY_CANDIDATE_EDIT_REQUIRED` | 422 | 助手单一来源仍是原建议正文；用户编辑后重试 |
| `MEMORY_REQUEST_REJECTED` | 422 | 正文、expiry 或结构不合法；修正请求，不自动降级 |
| `MEMORY_UNAVAILABLE` | 503 | PostgreSQL 暂时不可用；使用同一幂等键安全重试 |

错误响应沿用统一 `application/problem+json` 与 trace id；不在 detail 中回显候选或 Memory 正文。

## 7. 隐私、审计与保留

- 普通日志不记录候选正文、Memory 正文、来源消息、认证材料或原始 chain-of-thought。数据库完整性告警只记录 SQLSTATE 与约束名。
- AuditLog 只记录 resource/candidate id、action、candidate/memory revision、policy decision/reason、scope、source count、outcome 和 trace id。
- Candidate/Revision 通过正式 Message id 保留 provenance；API 只有在当前授权下才读取正文和来源 id。
- 步骤 1 保留用户决议与不可变 Revision 以支持审计。停用、过期、删除、隐私擦除、派生索引清理和 deletion residual=0 属于步骤 2；完成前不得宣称已满足完整删除治理。
- PostgreSQL 备份继承项目统一备份边界。Day 7 前仍需完成隔离恢复演练；当前仅验证 fresh migration 和事务回滚，不能把数据库存在等同于已验证灾备。

## 8. 运维、回滚与故障恢复

应用发布顺序为 migration upgrade 后部署兼容应用。确认写入在单个数据库事务内完成；任一 FK、CAS 或审计写入失败都会整体回滚，不留下“候选已确认但 Revision 不存在”的半状态。

故障恢复顺序：

1. 503 或连接中断时，先用同一 `Idempotency-Key` 重试候选创建，或用同一确认 payload 重试决议。
2. 409 时不要盲重试；重新 GET 候选与目标 Memory，显示最新 Revision，由用户重新决定。
3. 来源 404 时终止当前候选确认，让用户从仍有效的正式 Message 创建新候选。
4. 若需回退发布，先停止 Memory 新写入并备份 PostgreSQL，再回退应用。只有明确接受删除本步骤数据时，才可执行 `alembic downgrade a8f42d91e3b7`；生产数据不得为迁就旧代码直接降级。
5. 恢复后重新执行 fresh upgrade、候选幂等、确认 CAS、跨 Workspace 与审计脱敏测试。

## 9. 当前验收证据与未完成项

实现与测试入口：

- Domain/Application/API：`apps/backend/tests/modules/memory/`
- 真实 PostgreSQL、权限、来源失效、审计与并发：`apps/backend/tests/integration/test_memory_write_postgres.py`
- Migration 约束：`apps/backend/tests/integration/test_migration_smoke.py`
- Web 组件/API：`apps/web/src/chat/ChatWorkbench.test.tsx`、`apps/web/src/chat/chat-api.test.ts`
- 浏览器旅程：`tests/e2e/app-shell.spec.ts`

本策略不会提前承诺下列能力：实际召回和 Context manifest、跨 Thread 使用说明、修改/停用/过期/删除后的下一 Run 生效、Memory Scorer 和 deletion residual。它们必须在步骤 2 用同一 PostgreSQL/Application/UnifiedAgentRuntime 正式链路完成后再更新本文。
