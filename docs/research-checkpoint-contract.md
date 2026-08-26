# Research L4 Checkpoint 与 HITL 合同

> 合同版本：`research_l4_v1`  
> graph 版本：`research-l4-graph-v1`  
> 状态：Day 5 Step 5 `implemented_pending_verification`（PR #9 与 main CI `32924732755` 已完成；缺 ready SEC fixture 暂停/审批/resume/刷新浏览器 DoD）

## 1. 数据所有权

| 事实 | 权威位置 | 不可替代为 |
|---|---|---|
| Run、Budget、当前状态 | PostgreSQL `agent_runs` | Job/Celery 状态 |
| Brief、FinancialScope | `research_briefs` revision | 模型输出或前端缓存 |
| 可恢复状态 | `agent_checkpoints` | `research_runs.state`、Trace |
| 审批请求与决定 | `research_approval_requests`、`research_approval_decisions` | Event payload 单独存在 |
| resume work | `jobs` + `outbox_events` | 进程内任务 |
| 已完成副作用 | `research_side_effects` | Tool Trace |

## 2. CheckpointEnvelope

通用信封字段为 envelope/state schema、checkpoint/run/workspace ID、独立 Checkpoint revision、
保存时间、`RunState` 和领域 payload。Store 必须以 `expected_revision` 执行 CAS；创建期望
`None`，后续保存必须精确匹配当前 revision。

Research payload 只允许：

```text
kind = research_l4_v1
graph_version / research_state_schema_version / research_run_id
financial_scope
node / next_node
graph_state
execution.observations / steps / final_decision / final_response / final_markdown / outline
```

`FinancialScope` 必须与当前 Brief revision 的规范化值完全一致。payload 只保存恢复必要的
结构化 Observation、Step 摘要与资源引用；禁止 Secret、访问令牌、完整 filing、任意依赖对象和
原始 chain-of-thought。

## 3. 提交顺序与恢复条件

节点成功后的顺序固定为：

```text
node completed business facts/Event
→ Checkpoint CAS save
→ side-effect ledger upsert
→ checkpoint.saved Event
→ next node or approval interrupt
```

只有最后 committed Event 与最新 Checkpoint 相互指向时，`recovery` 才可执行。approval resume
还要求同一 request/checkpoint 的 allowed Decision、已校验的 proof、`resume_claimed=true` 和同一
resume Job。Loader 从 `next_node` 继续同一 AgentRun；任何不一致都 fail closed。

## 4. Approval 与 proof

- 目前唯一 reason 是 `company_or_period_ambiguity`，仅用于带 `FinancialScope` 的本地 Research。
- request ID 由 Run + Checkpoint revision 确定；同一 Checkpoint 只能有一个 request。
- proof 由服务端 HMAC 签发，数据库只保存域分离 SHA-256 摘要；HTTP/Trace 不记录原始 proof。
- allow/deny 每个 request 最多一条 Decision；相同提交幂等，冲突提交拒绝。
- resume 在同一事务领取 proof 并创建 Job + Outbox；重复请求返回已有 Job。
- 到期后的 decision 固化 `timed_out`；到期、取消、终态或 Budget 超限的 resume 均拒绝。

## 5. Side-effect ledger

唯一键为 `workspace_id + effect_kind + idempotency_key_hash`。当前 Runtime 在节点 Checkpoint 后
登记 Tool call、Evidence 和 Artifact 的已完成结果；重复 upsert 不新增记录。恢复前使用已保存
Observation/资源 refs，不重新调用已经完成的 Knowledge/calculator Tool。

该合同保证业务幂等收敛，不宣称基础设施 exactly-once。未来写 Tool 必须先扩展 intent/failed
状态和补偿策略，不能仅依赖 completed upsert。

## 6. 兼容与验证

- envelope/state/graph/payload kind 任一不兼容均拒绝恢复；本步没有隐式迁移器。
- Checkpoint node 与 `next_node` 必须符合唯一 `RESEARCH_NODE_ORDER`。
- `research_loop` 之后的 Checkpoint 必须具有完整 decision、response 和 Step snapshot。
- Scope、Workspace、Run、Event 尾部、审批和 Budget 每次恢复都重新验证。
- 规则数据集：`evals/scenarios/sec-fixture-l4-v1.json`。
- 机器报告：`evals/reports/sec-fixture-l4-v1.json`。

当前证据只覆盖成功节点边界 hard stop；任意节点中点恢复、后台审批超时扫描和 Day 8 跨刷新/
Worker 重启组合验收不在本合同的已证明范围。
