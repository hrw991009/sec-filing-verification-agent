# Day 1 学习日志

> 更新日期：2026-08-12
>
> 计划基线：`docs/master-plan.md` 1.7.0
>
> 当前结论：Day 1 正式实现、全量本地门禁和提交 `2c4e6e9` 的干净 CI 已通过；D1-02～D1-08、D1-10～D1-12 已复核为 `complete`。D1-09 的参考仓外部凭据处置仍为独立尾项。

## 1. 工程与验证

Ruff、mypy 和 pytest 解决不同问题：Ruff 负责格式、导入与静态规则，mypy 检查类型关系，pytest 执行行为断言。`uv.lock` 与 `pnpm-lock.yaml` 固定解析结果；`uv sync --locked` 和 `pnpm install --frozen-lockfile` 会在项目声明与锁文件漂移时失败，避免安装过程悄悄改依赖。

早期失败探针已经证明质量门能够阻断错误：Ruff 阻断无结构 `print`，mypy 阻断错误返回类型，pytest 阻断失败断言，ESLint 阻断不允许的 `console`，Vitest 发现可访问性结构回归，Playwright 在失败时生成报告和 Trace。历史 [CI 30797166192](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/30797166192) 证明了早期工程基线；当前提交 `2c4e6e9` 又通过了 [CI 31578083339](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/31578083339)，覆盖 PostgreSQL/Redis、迁移、身份与 Workspace、Job/Outbox、API 契约、Web 和真实浏览器旅程。

本轮必须牢记：

- “测试文件已经写好”不等于“测试已经通过”，本轮只有在全量本地门禁和干净 CI 均成功后才更新状态；
- 小范围测试通过不等于 PostgreSQL/Redis 全量集成、真实浏览器旅程和干净 CI 通过，本轮已经补齐这些证据；
- `gitleaks dir` 检查当前树，`gitleaks git --log-opts='--all'` 检查完整历史，二者不能互相替代；
- 只有实际执行适用门禁后，`implemented_pending_verification` 才能复核为 `complete`；本轮 D1-02～D1-08、D1-10～D1-12 已完成该复核。

## 2. 身份与浏览器安全

身份链路使用短期 Ed25519 Access Token，以及不可相互复用的 Refresh、CSRF、device、登录限流 HMAC 密钥和 refresh recovery AEAD 密钥。Access Token 只保存在浏览器内存；Refresh Token 使用 HttpOnly Cookie，浏览器凭据变更还要校验精确 HTTPS Origin 与 CSRF Token。

Refresh rotation 不能只做“旧 Token 立刻作废”：并发请求和响应丢失会让合法客户端无法恢复。因此数据库保存 rotation family/current session，并用短暂加密 successor envelope 支持限定窗口内恢复；窗口外重放会撤销 family。修改密码必须验证当前密码、原子更新哈希并撤销旧 Session，不能只改一列后继续接受旧 refresh。

登录限流使用 Redis 限制 IP 与规范化账户维度；Redis 不是身份事实源。用户、Session、Workspace 和审计事实仍在 PostgreSQL。

## 3. Workspace 授权

认证回答“你是谁”，Workspace membership 回答“你在这个租户里能做什么”。Router 不能因为拿到合法 Access Token 就信任客户端传入的角色；每次操作必须从服务端 membership 加载 owner/admin/member/viewer 策略。

最后 owner 保护是并发一致性问题，不是普通的 `count > 1` 检查。检查和角色变更/删除必须在同一数据库事务、适当锁与约束下完成，才能避免两个并发请求各自看到两个 owner，最终却同时移除。

## 4. 数据库迁移与可靠任务

ORM metadata 通过导入并注册声明式模型收集表定义；Alembic 再比较 metadata 与数据库，但正式 schema 只能由 migration 创建。当前两份线性迁移覆盖身份/Workspace 与 Job/Outbox/Schedule，并通过约束表达关键不变量；不能用 `Base.metadata.create_all()` 绕过迁移历史。

Job 和最终状态以 PostgreSQL 为准。业务事务写 Job 与 Outbox，Dispatcher 只负责把已提交意图发布到 Celery；Worker 使用 lease、heartbeat 和 fencing token 防止过期执行者覆盖新结果；Reconciler 修复已发布但未启动或 lease 过期的 Job。Redis AOF 降低消息丢失窗口，但不能替代 Outbox、幂等和对账。

Celery Beat 不直接向 broker 发布业务任务。数据库驱动的 Scheduler 先以 `(schedule_id, scheduled_for)` 唯一 occurrence 去重，再在同一事务写 Job 与 Outbox。多 Beat 使用 PostgreSQL 锁竞争，manual trigger 使用稳定触发 ID 保证并发只产生一次。DST 的跳时和回拨必须按时区与绝对时间计算，不能只比较本地墙上时间。

## 5. 前后端唯一契约

FastAPI OpenAPI 是唯一 API schema 来源，`packages/api-contract/openapi.json` 与 `schema.d.ts` 是生成物，Web 通过同包的 typed client 访问后端。正确流程是修改后端 schema，运行 `pnpm run api:generate`，评审生成 diff，再由 `pnpm run api:check` 验证干净重建；不能手写第二套 DTO 来消除类型报错。

真实身份 E2E 必须启动 API、PostgreSQL、Redis 和 HTTPS Web，覆盖注册、受保护首页、刷新、改密、旧 Session 失效和 Logout。只验证静态 App shell 不能证明身份闭环。

## 6. 当前事实状态

- D1-01 保留已有可复现工程基线的 `complete` 证据；
- D1-02～D1-08、D1-10～D1-12 的正式实现、formatter、全量本地验证和新 CI 已通过，状态为 `complete`；
- D1-09 仍为 `thin_slice`：参考仓 6 组凭据候选全部保持 `open`，只有 Provider 侧吊销/轮换、非敏感证据记录和复扫完成后才能关闭；
- Day 2～Day 7 尚未实现，后续严格按 `docs/master-plan.md` 1.7.0 的依赖顺序推进：Day 2 Agent Runtime/Harness v0，Day 3 Tool loop，Day 4 Memory/Evidence 与 Research L3，Day 5 Agent Knowledge 与 Durable Research L4，Day 6 Hybrid RAG 与 Research L5，Day 7 综合 Agent Eval 与 Learning Workbench。

统一安装、密钥生成、Compose、Alembic、运行入口、OpenAPI 和验证命令见根 [README](../../README.md)。

## 7. 待本人用自己的话复盘

1. 为什么历史 CI 不能证明当前未提交工作树已经通过？
2. 为什么 Access Token、Refresh Token、CSRF Token 与各类 HMAC/AEAD 密钥需要不同存储和密钥材料？
3. 为什么最后 owner 保护与 Refresh rotation 都必须考虑数据库并发？
4. 为什么 Outbox published 不等于 Job 已经 started？
5. 为什么 Beat、Dispatcher、Worker 和 Reconciler 必须复用同一套 Job/Application Service？
6. 为什么 6 组参考仓凭据未处置不否定已完成的 Day 1 新仓工程门禁，却仍不能关闭 D1-09 或创建 Day 7 发布标签？
