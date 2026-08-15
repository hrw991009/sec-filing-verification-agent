# Day 2 学习日志

> 更新日期：2026-08-15
>
> 计划基线：`docs/master-plan.md` 1.7.0
>
> 当前结论：Day 2 正式实现已经写入，D2-01～D2-09 均为 `implemented_pending_verification`。统一本地门禁、真实 PostgreSQL/Redis/MinIO 与浏览器 E2E 已通过；但恢复收敛、生产 SSE 背压/snapshot、可观测性、部分端到端证据、干净 CI 与学习者复核仍未关闭，所以不能写成 `complete`。

## 1. Runtime、Harness 和 Worker 不是一回事

Runtime 决定一次智能任务如何推进：当前 State 是什么、下一步是什么、发哪些 Event、预算是否允许继续、最后为什么停止。Harness 决定这次 Run 使用哪个 profile、Instructions、Context 策略、Provider/Fake、Tool 集合和 Scorer。Worker 只是在另一个进程可靠领取 Job，再调用正式 Application Service/Runtime。

如果 Worker 直接调用 Provider，或者 Harness 自己写一套 model loop，生产和评测就会出现两种状态、两种错误和两种恢复语义。因此 Day 2 最重要的架构结果不是“能调用模型”，而是 API、Worker、Harness 和未来 Research 都回到同一 Runtime 信封。

## 2. L0 为什么复用 Runtime，却还不是 Agent loop

Direct Answer L0 只有一次模型步骤和一次结构化 final，没有 Action→Observation→下一轮 Model 的循环。它已经需要 Run/Step/Event/Budget/Trace，是因为即使一次调用也会遇到超时、429、取消、半截响应、断线和费用核算；但 Tool 选择、有界循环、no-progress 与审批从 Day 3 才开始。

这让我区分了“模型调用”和“Agent loop”：前者是一次外部能力调用，后者还要根据结构化结果决定是否调用 Tool、更新 State、继续或停止。

## 3. State、Checkpoint、Trace 和 Memory 回答不同问题

- State 回答“这个 Run 现在推进到哪里、已经用了多少预算”；
- Checkpoint 回答“进程中断后，从哪个版本化执行边界安全恢复”，Day 2 只完成 envelope/schema/CAS 基础；
- Trace 回答“这次 Run 实际发生过什么、使用了哪版 Context、花了多少 Token/费用、为什么停止”；
- Memory 回答“哪些跨 Turn 信息以后还值得召回”，属于 Day 4，不能拿 Event 日志或 Checkpoint 冒充。

Day 2 没有提前实现 LangGraph resume。这样可以先把统一语义和并发不变量做对，再在 Day 5 把 graph state 映射进来。

## 4. Runtime Context 不能原样送入模型

Runtime Context 含可信 user/workspace、capability、预算、deadline 和运行依赖，其中数据库连接、HTTP client、权限对象与 Secret 都不是模型输入。Context Compiler 只生成安全投影，并用 manifest 记录“哪些来源被采用、使用什么版本、估算多少 Token”。

附件也不能把 MinIO object key 或未经检查的浏览器声明直接交给模型。正式链路重新读取安全 final bytes、核对 hash，并把每个附件标成不可信用户数据。文本进入独立 USER 消息；图片只有在 route 明确支持图片时才进入 `ModelImagePart`。用户明确选择的附件放不进预算时，整个编译失败，而不是悄悄少看一个附件后继续回答。

## 5. 流式恢复依赖已提交事实

SSE 的 sequence 是业务游标，心跳 comment 不能推进它。浏览器断线后带 `Last-Event-ID` 重连，服务端只查 PostgreSQL 已提交 Event；这个读取路径不调用 Runtime，所以重连不会重复当前 Model Step。

用户问题和 Job/Outbox 在模型调用前已原子写入。模型 delta 作为 Event 提交，因此超时或半截响应后仍能重放已经收到的片段。取消只是先持久化请求，只有 Runtime/Worker 观察后写入唯一终态，HTTP 202 本身不等于“已经取消完成”。

## 6. Harness 证明什么，不证明什么

`evals/scenarios/day2-v2.json` 当前有 6 个用例：正常、格式错误、timeout、429、半截响应和取消。`day2-model-v1.json` 固定外部 Provider 边界，`day2-traces-v1.json` 固定不敏感的 Event 骨架。

同一个 Scenario 跑两次得到同样的 Event 类型/顺序和 stop reason，证明 Runtime 边界、Fake/Replay 和 Scorer 可重复。它不证明真实模型每次会写出同样答案，也不证明真实 Provider 已经调用成功。正式 Provider 未配置时必须返回 `provider_not_configured`，不能偷偷切换 Fake。

## 7. 支撑产品切片学到的事

Conversation、Turn、Message、AgentRun、Job 和 Outbox 必须先在一个 PostgreSQL 事务里建立正式关系。Turn 保存当时的 mode、industry 和 knowledge-base IDs；刷新时消息 API 恢复这个快照，而不是依赖浏览器 LocalStorage 猜测。

Day 2 只启用 `none`。`web/local/both` 虽然是正式枚举和持久字段，但在真实能力接通前返回稳定 readiness 错误，不用 Mock 搜索结果伪装成功。

附件是 PostgreSQL 管所有权/状态、私有 MinIO 保存 bytes 的跨存储流程。上传完成后必须重新检查实际对象，再生成安全 final object；删除失败不能假装数据库回滚能撤销 MinIO 操作。

前端通过生成的 OpenAPI 类型访问 REST，通过 fetch 解析带认证和游标的 SSE。Markdown 使用安全 allowlist；Run/Context 面板读取脱敏 Trace，不读取原始 chain-of-thought。

## 8. 当前可核对证据

- Runtime/Checkpoint/Context/SSE/Trace：`apps/backend/tests/modules/agent_runtime/`；
- Provider Adapter：`apps/backend/tests/adapters/test_openai_compatible.py`；
- Harness 双次回放：`apps/backend/tests/modules/agent_harness/test_day2_replay.py`；
- PostgreSQL 原子提交、执行加载与 Trace：`apps/backend/tests/integration/test_conversation_agent_postgres.py`、`test_agent_execution_loader_postgres.py`、`test_agent_trace_postgres.py`；
- 真实附件：`apps/backend/tests/integration/test_file_lifecycle_postgres_minio.py`、`test_file_attachments_minio.py`；
- Web：`apps/web/src/chat/` 下的 API、SSE 和安全 Markdown 测试，以及 `tests/e2e/app-shell.spec.ts`；
- 版本化评测资产：`evals/scenarios/day2-v2.json`、`evals/fixtures/day2-model-v1.json`、`evals/snapshots/day2-traces-v1.json`。

## 9. 本轮实际门禁结果

- Ruff format/check：257 个 Python 文件通过；mypy：236 个 source files 通过；
- pytest 快速集：645 passed、52 skipped；这些 skip 是显式开关保护的真实依赖测试，不计作通过；
- 强制打开 `POSTGRES_TESTS_REQUIRED`、`REDIS_TESTS_REQUIRED`、`MINIO_TESTS_REQUIRED`：52 passed；
- Web：Prettier、ESLint、TypeScript、8 个 Vitest 文件中的 31 个测试和 production build 全部通过；
- OpenAPI 与 TypeScript 契约确定性检查通过；
- Playwright：2 passed；Day 2 旅程真实调用 Conversation 202、SSE 200、cancel 202，等待 `agent.run.cancelled`，并在刷新后恢复终态；
- `uv audit` 报告 72 个已审计包无已知漏洞，`pnpm audit --audit-level high` 无已知漏洞；
- Gitleaks 已扫描本次改动目录和完整 Git 历史，未发现 Secret；本地 `.env` 不进入 Git，也没有输出其内容。

仍未关闭：

- Worker 中断、lease 过期或 handler/load/persistence 失败后，已运行的 AgentRun 仍需可靠收敛成唯一终态；Provider 在首个 delta 前挂起时，取消仍只能等待 Provider timeout；
- 生产 SSE 尚未真正接入 snapshot/裁剪与有界慢客户端背压，Agent Runtime 的结构化日志/指标也未达到门禁；
- `ChatWorkbench.tsx` 仍超过主计划禁止的千行万能 Page 阈值，需要按职责拆分；
- 未配置 Provider 的生产全链路失败、Worker 中断/恢复、真实断线/慢客户端、完整会话与附件浏览器旅程仍缺验收证据；
- 当前改动尚未提交，无法取得对应提交的干净 GitHub CI 链接；
- 由学习者用自己的话解释 Runtime、Harness、Worker、Checkpoint 与 Trace 的职责差异后，才满足主计划的最后一条学习门禁。真实 Provider smoke 可补充验证，但没有付费 Provider 时可以按主计划改用正式 `provider_not_configured` 全链路证据，不能回退 Fake 成功。

## 10. 当前明确边界

- L0 没有 Tool、Memory、Knowledge/RAG、Evidence/Claim、审批或 Deep Research；
- Checkpoint 尚未接 LangGraph interrupt/resume；
- `web` 计划 Day 3 接真实 Tool，`local/both` 计划 Day 5 接知识能力；
- 当前前端重试把最近用户文本作为新 Turn 提交，不静默复用旧附件；
- 用户输入、model delta、稳定失败原因和重新提交入口已有持久事实。Day 2 L0 没有 Evidence locator，所以 Citation 明确不适用；主计划把 Evidence/Claim 放在 Day 4，把正式 Citation gate 放在 Day 6，当前不会生成空引用或伪引用；
- Fake/Replay 是离线评测边界，不是生产 Provider fallback；
- 在上述代码、证据、干净 GitHub CI 和学习者复盘门禁完成前，任何 Day 2 项都不能标为 `complete`。

详细职责、执行链路、接口、限制与命令见 [Agent Runtime v0](../agent-runtime.md)。
