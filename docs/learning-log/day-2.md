# Day 2 学习日志

> 更新日期：2026-08-16
>
> 计划基线：`docs/master-plan.md` 1.7.0
>
> 当前结论：Day 2 的仓库内实现、可比较 Eval、全量本地门禁、干净 GitHub CI 与学习者职责复盘均已收口，D2-01～D2-09 全部为 `complete`。

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

用户问题和 Job/Outbox 在模型调用前已原子写入。模型 delta 作为 Event 提交，因此超时或半截响应后仍能重放已经收到的片段。PostgreSQL 首次只回放最新 256 条，旧游标收到对齐最新 sequence 的权威 snapshot；后续逐批拉取，慢 socket 不会让应用无限预读。

取消只是先持久化请求，HTTP 202 本身不等于“已经取消完成”。排队中的 Run/Job 在同一事务收敛；运行中的 Runtime 即使尚未收到首个 delta，也会检查持久取消并关闭 Provider stream。Worker 硬停、loader/runtime 异常或 Job dead-letter 无法安全 resume 时，Terminalizer/Reconciler 写一个明确失败/取消终态，而不是让 Run 永久卡住。

## 6. Harness 证明什么，不证明什么

`evals/scenarios/day2-v2.json` 当前有 6 个 Provider 边界用例：正常、格式错误、timeout、429、半截响应和取消。`day2-model-v1.json` 固定外部 Provider 边界，`day2-traces-v1.json` 固定不敏感的 Event 骨架，`evals/reports/day2-v1.json` 保存可比较的 Scorer/stop reason/轨迹一致性结论和明确的测量限制。

仅靠这 6 个用例不能证明预算、Worker 中断和请求幂等，因为后两项不发生在 Fake Provider 内。`evals/scenarios/day2-reliability-v1.json` 因此增加 3 个版本化场景：费用预算耗尽直接走正式 Runtime；不可恢复 Worker 中断走正式 PostgreSQL Terminalizer/Reconciler，并覆盖已经进入 retry_wait 的 Job/Run 原子收敛；重复 Turn 请求走 Conversation Application Service。每个场景都绑定实际测试函数，测试还会检查引用存在和 DoD 类别没有漏项；这个登记层不执行模型，也没有第二套 loop。

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
- 可靠性 Scenario：`evals/scenarios/day2-reliability-v1.json`，分别绑定 Runtime 预算、PostgreSQL 中断收敛和 Conversation 幂等测试；
- PostgreSQL 原子提交、执行加载与 Trace：`apps/backend/tests/integration/test_conversation_agent_postgres.py`、`test_agent_execution_loader_postgres.py`、`test_agent_trace_postgres.py`；
- 正式成功执行：`test_agent_fake_success_postgres.py` 贯通 Conversation、Outbox、JobExecutionRuntime、唯一 Runtime、PostgreSQL final Message/Event 与 committed replay；Fake 只替换 Provider Port，并断言只调用一次；
- 不可恢复执行与 Job dead-letter 收敛：`test_agent_run_reliability_postgres.py`；正式未配置 Provider 不出网、不回退 Fake：`test_agent_unconfigured_provider_postgres.py`；
- 真实附件：`apps/backend/tests/integration/test_file_lifecycle_postgres_minio.py`、`test_file_attachments_minio.py`；
- Web：`apps/web/src/chat/` 下的 API、SSE 和安全 Markdown 测试，以及 `tests/e2e/app-shell.spec.ts`；
- 版本化评测资产：`evals/scenarios/day2-v2.json`、`evals/scenarios/day2-reliability-v1.json`、`evals/fixtures/day2-model-v1.json`、`evals/snapshots/day2-traces-v1.json`、`evals/reports/day2-v1.json`。

## 9. 本轮门禁状态

2026-08-15 已按根 README 的统一门禁完成本地收口：245 份 Python 文件通过 Ruff format/check，240 个文件通过 mypy；强制 PostgreSQL/Redis/MinIO 后 708 个 pytest 全部通过且无 skip，fresh migration、Python build/audit 通过。前端 format/lint/typecheck、10 个 Vitest 文件共 42 个测试、build、OpenAPI 确定性、Node audit 与 3 条 Playwright 旅程通过。浏览器旅程新增了“页面 POST → PostgreSQL Outbox/Job → 正式 JobExecutionRuntime → 正式 DirectAnswerRuntime（只替换 ModelProvider 测试边界）→ 两段 committed SSE → final Message → 刷新恢复”的成功链；受控路径和 39 个 Git 提交的 Gitleaks 扫描未发现 Secret。版本化 Eval JSON 仍只是可重复报告，不代替这些命令。

提交 [`bf4feaff`](https://github.com/hrw991009/industry-intelligence-platform/commit/bf4feaff2e0fa5487a6f01ed0fd4cd63f5b4f659) 的 push 与 pull request CI 均成功，[CI 31922391846](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/31922391846) 的全部适用 Job 通过。学习者于 2026-08-16 用自己的话说明了五个组件的职责；复核结论为：

- Runtime 负责一次 Run 从状态推进、预算判断到 Event/终态输出的统一编排；
- Harness 使用 Scenario、Fake、Scorer 和同一 Runtime 隔离模型随机性，证明执行契约可重复，但不冒充真实模型质量；
- Outbox Dispatcher 把 PostgreSQL 已提交的 Job 通知可靠发布到 Redis/Celery，Worker 消费通知后从 PostgreSQL 领取权威数据，再交给 Handler 和正式 Runtime；
- Day 2 Checkpoint 保存版本化状态信封并用 CAS 防止并发覆盖；真正从中断点继续执行属于 Day 5 的 durable resume；
- Trace 记录已提交的 Run/Step/Event、Context manifest、usage 和 stop reason，用于解释、调试和评测，但不是执行状态或恢复点。

真实 Provider smoke 可补充验证，但没有付费 Provider 时，只能用正式 `provider_not_configured` 全链路证明不会回退 Fake 成功，不能声称已经验证真实模型质量。

## 10. 当前明确边界

- L0 没有 Tool、Memory、Knowledge/RAG、Evidence/Claim、审批或 Deep Research；
- Checkpoint 尚未接 LangGraph interrupt/resume；
- `web` 计划 Day 3 接真实 Tool，`local/both` 计划 Day 5 接知识能力；
- 当前前端重试把最近用户文本作为新 Turn 提交，不静默复用旧附件；
- 用户输入、model delta、稳定失败原因和重新提交入口已有持久事实。Day 2 L0 没有 Evidence locator，所以 Citation 明确不适用；主计划把 Evidence/Claim 放在 Day 4，把正式 Citation gate 放在 Day 6，当前不会生成空引用或伪引用；
- Fake/Replay 是离线评测边界，不是生产 Provider fallback；
- Agent 追加 DoD 中的 Tool 失败对 Day 2 L0 阶段性 `N/A`：本日 `available_tools=[]`，没有 Action/Observation；复核人为执行代理，日期 2026-08-15。Day 3 L1/L2 必须恢复此义务并新增 Tool 失败 Scenario；
- Day 2 对不可恢复 Worker 中断的义务不是 `N/A`，`day2-unrecoverable-worker-interruption` 必须证明唯一 failed 终态。只有同一次模型调用的 durable graph resume 因主计划明确归属 Day 5 而阶段性 `N/A`；复核人为执行代理，日期 2026-08-15；
- 当前工作树的最终全量本地门禁、对应提交的干净 GitHub CI 和学习者复盘均已通过，D2-01～D2-09 已标为 `complete`。

详细职责、执行链路、接口、限制与命令见 [Agent Runtime v0](../agent-runtime.md)。
