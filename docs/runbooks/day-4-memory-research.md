# Day 4 Memory、Evidence 与 Research L3 运行手册

> 适用范围：可治理 Memory、Evidence/Claim、ResearchBrief、Research L3 graph、Agent Learning Workbench 和 Day 4 Eval。  
> 继承范围：身份、Workspace、Job/Outbox、SSE 和基础备份遵循 [Day 2 手册](day-2-agent-runtime.md)；行业/Web Tool 与来源 readiness 遵循 [Day 3 手册](day-3-agent-tools.md)。
> 已验证基线：`feat/day-4` 提交 [`b99ca7a`](https://github.com/hrw991009/industry-intelligence-platform/commit/b99ca7a8eca3f51a726449bc2aa7462aa51c9cff) 的 [CI 32547497639](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/32547497639) 已通过 7 个适用 Job；GitHub `main` 尚未合入 Day 4。

## 1. 启动与迁移

在仓库根目录启动正式依赖并升级唯一 Alembic head：

```powershell
docker compose --env-file '.env' -f infra/compose/compose.yaml up -d --wait postgres redis minio
docker compose --env-file '.env' -f infra/compose/compose.yaml run --rm --no-deps minio-init
uv run --env-file '.env' --locked --package industry-platform-backend alembic -c apps/backend/alembic.ini upgrade head
uv run --env-file '.env' --locked industry-platform-backend-dev
```

另开终端执行 `pnpm run dev:web`，访问 `https://localhost:5173`。不要单独启动 Research graph 脚本：正式链路固定为 API → ResearchRun/AgentRun/Job/Outbox → Worker → `UnifiedAgentRuntime` → LangGraph typed orchestration → Tool/Evidence/Claim → PostgreSQL/Event/Trace。

## 2. Memory 操作与排障

在 Conversation 中选择消息并创建候选，检查写入原因、来源、scope、confidence 和策略结论；用户确认/编辑后才形成 Long-term Memory revision。新建 Conversation 运行同一相关问题，Trace 的 Context manifest 应显示对应 `long_term_memory` source、included/excluded reason、revision/hash 和 Token 估算。

如果 Memory 未召回，按顺序检查：

1. owner/Workspace/scope 是否匹配；
2. status 是否 enabled，是否 expired/deleted；
3. 是否因 conflict、sensitive、irrelevant、duplicate 或 Token budget 被稳定排除；
4. manifest、ModelInput 与数据库 revision 是否一致。

删除后必须创建下一 Run 并确认 manifest 与模型输入都没有该 revision；只看列表消失不算删除验收。不要直接改表、清 revision 或伪造 recall decision。

## 3. Evidence/Claim 与 Research L3

Evidence Inspector 从正式 Trace/资源 API 展示 Observation → Evidence decision → Claim relation。`rejected`、`invalidated`、`unavailable` 和 `conflicted` 是有意义的安全状态，不应人工改成 active。

Research 页面要求显式保存原问题、确认范围、排除项、完成标准、行业和预算。提交返回 202 后，刷新列表与详情；节点时间线从 safe Trace 重建，Plan、统一 Step、usage、stop reason、Evidence/Claim 和 draft 从正式资源读取。真实来源缺少不可变 SourceItem snapshot 时，预期结果是 rejected Evidence、uncertain Claim 和 uncertain draft，而不是伪造成功。

常见失败处理：

| 状态 | 先检查 | 正确处理 |
|---|---|---|
| `provider_not_configured` / Provider 错误 | 服务端 Provider readiness、稳定 stop reason | 修复配置后创建新 Run；不回退 Fake |
| `tool_error` / `source_snapshot_missing` | ToolRun、Observation、normalizer decision | 保留失败/uncertain；修复来源合同后重跑 |
| `max_steps` / Token / cost / deadline | Brief budget、Run usage、节点/Step 数 | 缩小 scope 或由用户显式提高允许范围；模型不能自增预算 |
| `cancelled` | cancel request、唯一 terminal event | 不继续节点，不静默重启 |
| `approval_required` | capability/approval policy | Day 4 明确终止；Day 5 才实现 durable resume |
| 页面 unavailable/forbidden | 当前 Workspace 与资源 owner | 重新选择有权 Workspace；不得绕过服务端授权 |

Worker hard stop 由 Reconciler/Terminalizer 收敛为明确失败并保留已提交事实。Day 4 不从普通 Research state 自动续跑，也不从头静默重放 Tool 副作用。

## 4. Workbench 与 Eval

Workbench 的 Memory、Evidence/Claim 与 Research 面板只保存短期选择状态；刷新后必须从 OpenAPI、Event、Trace、Context manifest 和正式资源 API 重建。若 API 已完成但页面仍旧，先检查浏览器网络响应和契约解码错误，不要增加前端假缓存。

确定性报告位于：

- `evals/reports/day4-memory-v1.{json,md}`；
- `evals/reports/day4-memory-ablation-v1.{json,md}`；
- `evals/reports/day4-evidence-v1.{json,md}`；
- `evals/reports/day4-research-v1.{json,md}`；
- `evals/reports/day4-v1.{json,md}`。

JSON 由独立固定分母 Scorer 校验；Markdown 解释限制。它们是冻结契约基线，不是在线 Provider 质量、来源新鲜度、网络延迟或实际定价。pytest、真实 PostgreSQL 集成和 Playwright 才是实现 pass/fail authority。

## 5. 备份、删除与恢复

PostgreSQL 保存 Memory revision、Evidence/Claim、Research state/draft、Run/Event/Trace 和预算事实；MinIO 保存既有私有大对象。两者使用同一恢复点。隔离恢复后至少验证：

1. 唯一 Alembic head 与 Workspace 复合外键；
2. 已删除 Memory 不回到列表、召回或 Context manifest；
3. invalidated Evidence 不恢复 excerpt 或 active Claim relation；
4. ResearchRun/AgentRun terminal、stop reason、节点 revision 和 Trace 计数一致；
5. 普通 Conversation 删除不误删必要安全审计。

Day 4 只文档化和自动验证在线删除/失效语义；完整物理 purge 与隔离备份恢复演练由 Day 7 关闭。在完成前不得手工级联删除，也不得宣称发布级隐私擦除已经完成。

## 6. 回滚

应用故障优先回退到上一份已通过 CI 的提交，并保留 PostgreSQL、Redis、MinIO 和故障 Trace。旧应用不得连接它不理解的新 schema；先在备份副本验证兼容。

若必须暂停 Research，新建 Research 请求应明确返回 unavailable/readiness 错误；不要把 Research L3 静默改成 L0/L2、前端假成功或测试 Fake。Memory 召回可通过受控 Harness profile 禁用，但既有 Memory 不应被删除；Evidence Normalizer 可停止新提升，但历史 Evidence 状态和 Claim 图必须仍可解释。migration downgrade 只允许在隔离备份验证不会破坏仍需保留数据后执行。

## 7. 验收边界

完整门禁以根 [README 统一验证](../../README.md#统一验证)为准。Day 4 的本地全绿、五个提交、push 和最终功能分支 CI 已完成；当前仍以 `implemented_pending_verification` 记录步骤 1～5 与 D4-01～D4-07，因为 GitHub `main` 尚无 Day 4 合并提交，且项目所有者最终 Trace/复盘未记录。将本次文档与实现合入 `main`、确认合并提交 CI 全绿并完成所有者复盘后，才能统一改为 `complete` 并进入 Day 5。核心 Domain/Application/Research workflow 85% 覆盖率例外必须保留登记，并在 Day 7 总门禁前补到 90%。
