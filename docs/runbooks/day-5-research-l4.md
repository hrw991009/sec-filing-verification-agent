# Day 5 Research L4 运行与回滚手册

## 1. 正常暂停与恢复

1. 查询 `GET /api/v1/workspaces/{workspace_id}/research-runs/{research_run_id}`，确认 Run 为
   `paused`、stop reason 为 `approval_required`。
2. 查询同一 Run 的 `/durability`，记录最新 Checkpoint revision、approval request、到期时间和
   `duplicate_side_effect_count`；不得从 Trace 猜测恢复位置。
3. 由当前有 `RUN_RESEARCH` 权限的用户提交 `/approval-decisions`。allow 响应中的 proof 只用于
   对应 request/revision，不写日志或工单正文。
4. 提交 `/resume`。`202` 返回 Job；重复请求应返回同一 Job 且 `created=false`。
5. 再次查询 Run/durability/Trace，确认只出现一个 `approval.decided`、一个 `run.resumed`，并从
   Checkpoint 的 `next_node` 继续。

deny 不调用 `/resume`。到期后提交 decision 会固化 `approval_timed_out`；当前没有后台自动过期
扫描器。

## 2. 故障诊断

| 现象 | 先检查 | 处理 |
|---|---|---|
| `approval_conflict` | request ID、revision、已有状态 | 使用 `/durability` 的当前事实；不要创建新 request 绕过冲突 |
| `resume_token_invalid` | proof 是否属于同一 request/run/revision | 重新读取允许态响应；不得从数据库恢复原始 proof |
| `resume_state_invalid` | Run 状态、取消、deadline、Step/Token/费用、到期时间 | 按权威 Run 修正；不要手工改 Job 状态 |
| Worker 在节点后终止 | 最新 Event 是否为匹配 Checkpoint 的 `checkpoint.saved` | 让既有 Job retry/Reconciler 重新加载；不从头建新 Run |
| Loader fail closed | graph/schema、scope、node/next node、Event 尾部 | 保留现场并回滚应用；不要修改 Checkpoint JSON |
| 重复副作用非 0 | side-effect 唯一键、Tool/Evidence/Artifact refs | 停止 resume，保留数据库证据并按缺陷处理 |

## 3. 本地验证

```powershell
uv run --env-file .env --locked --all-packages pytest -q `
  apps/backend/tests/workflows/research/test_runtime.py `
  apps/backend/tests/integration/test_research_durability_postgres.py `
  apps/backend/tests/modules/research/test_api.py `
  apps/backend/tests/modules/research/test_eval.py

uv run --env-file .env --locked --all-packages alembic `
  -c apps/backend/alembic.ini downgrade b1d5e7f9a320
uv run --env-file .env --locked --all-packages alembic `
  -c apps/backend/alembic.ini upgrade head
uv run --env-file .env --locked --all-packages alembic `
  -c apps/backend/alembic.ini check
```

迁移往返必须在隔离测试数据库执行。浏览器/组件验证还要确认审批请求先于 resume 持久化，刷新后
Checkpoint/HITL 时间线来自正式 API。

## 4. 应用回滚

1. 停止接受新的 Research 和 resume 请求，暂停 Research queue 消费。
2. 等待当前 Job 到安全节点或取消；备份 PostgreSQL，并记录非终态 Run、Checkpoint、审批和 Job。
3. 部署不读取 L4 表的新版本。只要仍需保留 paused Run 的恢复能力，就不要 downgrade。
4. 明确接受 L4 审批/副作用账本丢失后，在隔离验证通过的备份上 downgrade 到
   `b1d5e7f9a320`，再按变更窗口执行生产 downgrade。
5. 恢复队列前确认旧应用不会领取 L4 resume Job。旧 L3 Brief/Plan/Draft/Evidence/Claim 保留，
   paused L4 Run 不得被静默当作新 Run 重放。

## 5. 完成边界

本地测试、迁移往返和 Workbench 组件通过只支持 `implemented_pending_verification`。Step 5
提交、功能分支 CI、`main` 合并提交 CI、完整 Day 5 DoD 和项目所有者复核全部完成后，才可关闭
D5-09 并进入 Day 6。
