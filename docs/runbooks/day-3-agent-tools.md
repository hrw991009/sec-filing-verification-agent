# Day 3 Agent Tool、行业与 Text2SQL 运行手册

> 适用范围：Day 3 的 L1/L2 Runtime、Agent Web 模式、行业采集、Tool Inspector、数据库浏览、安全 Text2SQL 和受校验图表。
>
> 继承范围：身份、Workspace、Job/Outbox、SSE、附件和基础备份先遵循 [Day 2 Agent Runtime 运行手册](day-2-agent-runtime.md)。

## 1. 启动与迁移

在仓库根目录启动正式依赖并升级到唯一 Alembic head：

```powershell
docker compose --env-file '.env' -f infra/compose/compose.yaml up -d --wait postgres redis minio
docker compose --env-file '.env' -f infra/compose/compose.yaml run --rm --no-deps minio-init
uv run --env-file '.env' --locked --package industry-platform-backend alembic -c apps/backend/alembic.ini upgrade head
uv run --env-file '.env' --locked industry-platform-backend-dev
```

另开终端运行 `pnpm run dev:web`，访问 `https://localhost:5173`。统一后端会启动 API、Dispatcher、Worker、Beat 和 Reconciler；不要另写脚本直接消费 Agent Job 或复制 Tool loop。

## 2. Web Tool 与行业来源

登录后先在“行业情报”确认当前行业，再在 Agent Composer 选择“Web 搜索 · L2”。Web Turn 会把行业 ID、L2 profile、精确 Tool allowlist 和预算持久化到 Run/Job，Worker 只从可信 Loader 物化 command。

Provider readiness 必须按 [真实来源复核](../security/day-3-source-review.md)处理：

- World Bank News 只有 `WORLD_BANK_NEWS_TERMS_APPROVED=true` 时启用；
- Alpha Vantage 同时需要服务端 key 和 `ALPHA_VANTAGE_TERMS_APPROVED=true`；
- Federal Register 与 TED 仍受固定 host、字段、请求/响应预算和来源标识约束；
- 未配置、未批准、429 或合同漂移必须显示稳定失败，不得切到测试 Fake。

手动或定时采集只通过 Schedule/Occurrence→Job/Outbox→Worker Application Service。排查时记录 Workspace、Schedule、Occurrence、Job、Outbox、CollectionRun 和 trace ID，不直接改状态或重复调用 Provider。

## 3. Tool Inspector

Inspector 从 Trace API 读取已提交事实。确认一次成功 Web Run 至少包含：

1. Action Model Step；
2. `agent.tool.requested/started/completed`；
3. Tool execution Step；
4. 带 envelope digest 的 `tool_observation` Context source；
5. Final Step、唯一 Run terminal 和 stop reason。

Inspector 不显示 raw arguments、Provider body 或 Observation 正文是安全设计，不是日志缺失。需要排查时使用 call/run/step/trace、稳定 error code、策略版本和 digest 关联数据库记录，不要把原始响应临时写入日志或前端。

## 4. Text2SQL

Text2SQL 默认未配置。启用时为 `TEXT2SQL_DATABASE_URL` 创建与应用 owner 不同的只读 PostgreSQL 账号，只授予合成样例 allowlist 的 `SELECT`；不得复用应用 DSN。超时、最大行、计划成本/扫描行和陈旧 QueryRun 阈值使用 `.env.example` 中的 `TEXT2SQL_*` 设置。

页面展示 generated/validated SQL、计划与 Artifact；只有后端 SQLGlot AST、SchemaSnapshot、数据库只读事务和计划/结果预算全部通过才会执行。任何 DML/DDL/COPY/CALL、多语句、危险 CTE、系统对象或未知函数必须形成失败 QueryRun，不得人工改成成功。

Reconciler 会把超过 `TEXT2SQL_QUERY_STALE_SECONDS` 的 `running` QueryRun 以 `query_execution_interrupted` 收敛。若页面长期显示运行中，先检查 Reconciler 心跳和 PostgreSQL 时间，不要直接删除 QueryRun。

## 5. 删除、备份与恢复

普通会话删除是逻辑删除，不物理删除 Run/Event/ToolCall/ToolRun；这是为了保持用户可见删除与安全审计的边界。当前没有面向用户的物理 purge。收到法定擦除请求时不得手工级联删除，必须在 Day 7 发布前完成的显式授权 purge 流程中处理共享引用、最小安全审计留存和外部副作用对账。

PostgreSQL 保存 Run、Tool、行业、QueryRun 和 Artifact 元数据；MinIO 保存私有大对象。两者必须按 Day 2 手册作为同一恢复点备份。隔离恢复后至少核对：唯一 Alembic head、Workspace 复合外键、一个 Web Tool Trace、一个 QueryRun/Chart Artifact、逻辑删除的 Conversation 不回到普通列表、Tool audit 仍存在。未完成这项演练前不得宣称 Day 7 备份恢复门禁通过。

## 6. 回滚

应用故障优先回退到上一份已通过 CI 的提交，同时保留数据库、Redis、MinIO 和故障 Trace。不要让旧应用连接它不理解的新 schema；先在备份副本验证兼容性。

本阶段 migration 包含行业/采集、Tool audit、数据浏览和 Web Turn 合同。默认不执行 downgrade；只有确认目标 migration 的 downgrade 不会删除仍需保留的数据，并已在隔离备份验证后才允许。若必须停用 Web Tool，先通过配置/部署入口关闭 Worker 的该能力并保留失败可见性，不得把 Web Turn 静默改成 L0 或 Mock 成功。

## 7. 验收

完整门禁始终以根 [README 统一验证](../../README.md#统一验证)为准；Day 3 的版本化场景和报告位于 `evals/scenarios/day3-l1-v1.json`、`day3-l2-v1.json` 与 `evals/reports/day3-v1.json`。测试报告只是可比较结论，pytest 与 Playwright 才是 pass/fail authority。
