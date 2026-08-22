# Day 4 Memory、Evidence 与 Research 安全和隐私复核

> 复核日期：2026-08-22  
> 复核人：执行代理  
> 适用范围：Day 4 的 Memory 写入/召回/治理、Evidence/Claim 账本、Research L3 graph、Workbench、Trace 和确定性 Eval。

## 1. 信任边界与权限

- 浏览器只提交用户输入、显式 scope、排除项、完成标准和有界预算；Workspace、owner、Tool capability、allowlist、deadline 与预算上限由认证 Context 和服务端策略重新物化，模型与前端都不能扩大。
- ResearchRun 只能绑定同 Workspace/owner 的正式 `research` AgentRun。Research detail、Trace、Evidence、Claim 和 graph 每次读取都重新验证 WorkspaceScope；跨 Workspace 返回稳定拒绝或不可见，不通过前端隐藏代替授权。
- LangGraph 节点只编排 typed state，并经 `UnifiedAgentRuntime`、ToolRegistry/ToolExecutor 和 Evidence/Claim Application Service 工作；没有节点、Router 或 Workbench 直连 Provider SDK，也没有第二条 Tool loop。
- Memory 候选不会自动成为长期事实。只有显式用户确认或允许的策略决策才能创建 revision；召回再次检查 owner/scope/status/expiry/conflict/sensitivity 和 Token 预算。

## 2. 不可信内容、Prompt Injection 与输出边界

- Conversation、Memory、Tool Observation、外部来源和 Research draft 都是不可信数据，不是 system instruction。它们通过 typed contract、长度/数量限制和 Context manifest 进入模型，不能修改 capability、预算、approval 或 WorkspaceScope。
- Observation 只有在当前授权、source/version/hash、许可和 typed locator 全部通过 Normalizer 后才能成为 Evidence。Provider 文本不能自行声明“已验证”“有许可”或伪造 Evidence ID。
- Claim 的 supports/refutes/context、coverage、conflict 和 uncertain 从持久化关系派生；没有有效 Evidence 的关键结论保持 uncertain。Workbench 不把 rejected Evidence 或失败 Run 渲染成成功。
- Research 草稿使用 React 文本/受限 Markdown 渲染，不使用 `dangerouslySetInnerHTML`，不执行模型代码、HTML、脚本、外部图片或任意链接协议。

## 3. Trace、日志与前端最小披露

- Research Event 的安全投影只允许 node、graph/state schema version、state revision 和稳定 error code。模块导入时校验 `_SAFE_EVENT_FIELDS` 覆盖全部 `AgentEventType`，新增事件若未显式审查会 fail closed。
- Trace、Context manifest、AuditLog 和浏览器状态不包含 raw prompt、Memory/Evidence 全文、Tool 原始参数/响应、认证材料、幂等键、原始 chain-of-thought 或 Research `error_summary`。前端解码器只接受生成契约中的事件枚举和标量 detail。
- Eval 只提交去敏的确定性 fixture 引用、计数、资源指标和结论摘要；不提交真实 Provider 响应、用户原文、运行日志或访问令牌。

## 4. 数据生命周期、删除与恢复

- Long-term Memory 删除后在线当前投影、召回、Context manifest 和下一 Run 的 residual reference 必须为 0；修订和最小审计边界遵循 [Memory 策略](../memory-policy.md)。
- Evidence 失效使用 tombstone/unavailable 语义，清空可见 excerpt、失活 Claim 关系并重算 coverage/conflict；不能用前端删除掩盖仍可读取的底层来源。
- ResearchRun、Brief、Plan、节点状态、Claim/Evidence 引用和 draft 与正式 AgentRun 一起保存在 PostgreSQL。Day 4 没有面向用户的物理 Research purge；法定擦除不得靠手工级联，必须进入 Day 7 的显式授权 purge、共享引用和最小审计留存流程。
- PostgreSQL/MinIO 必须作为一致恢复点备份；隔离恢复至少核对 Workspace 复合外键、Memory 已删除不复活、Evidence tombstone 不复活、Research terminal/stop reason/Trace 一致。完整隔离恢复和 purge 演练仍是 Day 7 发布门禁，本次没有虚报完成。

## 5. 依赖、许可证与供应链

- Day 4 唯一新增运行时依赖为精确锁定的 `langgraph==1.2.11` 及锁文件解析出的传递依赖；主包、checkpoint/prebuilt/sdk 与 LangChain Core 为 MIT，`ormsgpack` 为 Apache-2.0 OR MIT，`xxhash`/`uuid-utils` 为 BSD 系列。没有新增前端运行时依赖或外部素材。
- `uv audit --locked`、`pnpm audit --audit-level high`、构建、锁文件确定性和 Gitleaks 是提交前门禁。发布物必须保留依赖自身要求的许可证与归属；锁文件或许可证发生变化时重新复核，不能沿用本结论。
- 外部行业来源继续遵循 Day 3 条款/readiness；冻结测试 Provider 只存在于测试驱动，不得进入正式资源组合，也不得被描述为在线来源质量。

## 6. 自动验证与剩余限制

自动验证覆盖候选/确认/修改/停用/删除、无关与冲突召回、Evidence 正常/拒绝/失效、Research scope/Tool/Provider/预算/取消、跨 Workspace、唯一终态、safe Trace、刷新恢复、Memory off/on 和 L0/L2/L3 对照。

最终功能分支 CI 已通过 Python/Web 质量、真实 PostgreSQL/Redis/MinIO 集成、Browser E2E、Python/Node 依赖审计与完整历史 Secret 扫描共 7 个适用 Job。[PR #7](https://github.com/hrw991009/industry-intelligence-platform/pull/7) 随后合入 `main`，合并提交 [`c0b854e`](https://github.com/hrw991009/industry-intelligence-platform/commit/c0b854e64ef1966b76cdcc38c41a507959c836cb) 的 [CI 32549438592](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/32549438592) 再次通过相同 7 个适用 Job，因此 Day 4 安全复核随 D4-01～D4-07 关闭。核心 Day 4 合集 85% 覆盖率债务仍按学习日志登记，并由 CI 85% 不退化门槛约束；Day 7 前必须补到 90%。本结论关闭 Day 4，不等于 Day 7 主分支发布批准。

剩余限制不是安全豁免：Day 4 不提供 durable graph resume、持久 HITL、Verifier/bounded revise、文档/多模态 Evidence、最终 Report/Citation 完整门禁或物理 purge。它们分别属于 Day 5～7；UI 和文档必须明确显示当前能力，不把普通 state 持久化或 uncertain draft 冒充这些能力。
