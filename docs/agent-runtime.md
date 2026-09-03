# Agent Runtime v0

> 更新日期：2026-08-26
>
> 计划基线：Day 2 历史复核 + `docs/master-plan.md` 2.0.4 Day 5
>
> 当前状态：D2-01～D2-09 的仓库内实现、版本化 Eval、全量本地门禁、干净 GitHub CI 与学习者职责复盘均已关闭，统一为 `complete`。

## 1. Day 2 的边界

Day 2 交付的是 L0 Direct Answer：一次有明确输入、预算、事件和停止原因的模型调用，再把结构化 Markdown 结果保存为正式消息。它复用 Agent Runtime 的完整信封，但还不是 Day 3 的有界 Tool loop，也不包含 Memory、Knowledge/RAG、审批或 Deep Research graph。

各层职责固定如下：

| 组件 | 负责什么 | 不负责什么 |
|---|---|---|
| Conversation Application Service | 检查 Workspace 权限和模式 readiness；原子创建 Conversation、Turn、用户 Message、AgentRun、Job 与 Outbox | 不直接调用模型，不实现 Agent loop |
| Celery Worker | 领取可靠 Job，在独立进程调用正式执行服务 | 不选择 Provider、Tool 或 Context，不复制业务状态机 |
| Agent Runtime | 按 typed State、Budget、Event 和 stop reason 推进一次 Run | 不管理 HTTP、Celery lease 或业务页面 |
| Agent Harness | 固定 profile、Scenario、Fake/Replay 和 Scorer，并把这些边界交给同一 Runtime | 不实现第二套测试 loop |
| ModelProvider Port | 提供 `stream/complete`、usage、费用和稳定 Provider 错误 | 不决定 Workspace 权限或运行策略 |
| Checkpoint | 保存可恢复执行状态的版本化信封和 CAS 规则 | 不等于 Trace、Memory；Day 2 不承诺 LangGraph resume |
| Trace | 解释 Run、Step、Context manifest、usage、费用、事件和停止原因 | 不保存原始 chain-of-thought、Provider Secret 或附件原文 |

依赖方向由 [ADR 0001](adr/0001-modular-monolith.md) 和 [ADR 0005](adr/0005-langgraph-research-only.md) 冻结：API、Worker、Harness 和未来 LangGraph Adapter 都必须回到唯一 Runtime 语义。

## 2. 代码地图

主要实现位于：

- `apps/backend/src/industry_platform/modules/agent_runtime/`：领域对象、Event、State、Checkpoint、Context Compiler、Runtime、SSE、Trace 与应用边界；
- `apps/backend/src/industry_platform/modules/agent_runtime/adapters/`：PostgreSQL Run/Event/manifest/Trace 查询和正式执行加载；
- `apps/backend/src/industry_platform/modules/agent_harness/`：Scenario/EvalCase、Fake Model、回放记录、Direct Answer profile、Runner 与 CLI；
- `apps/backend/src/industry_platform/adapters/openai_compatible.py`：服务端 OpenAI-compatible Adapter；
- `apps/backend/src/industry_platform/modules/conversations/`：Conversation/Turn/Message、原子提交、管理和 HTTP；
- `apps/backend/src/industry_platform/modules/files/`：私有附件状态机、真实文本/图片校验、MinIO 与 HTTP；
- `apps/backend/src/industry_platform/workers/`：可靠 Job 到正式 Direct Answer 执行服务的入口；
- `apps/web/src/chat/`：聊天工作台、fetch-SSE 客户端、安全 Markdown 和 Run/Context Trace 面板。

数据库事实由 Alembic 迁移创建。Day 2 的主要迁移是：

- `ea2832a756ce_create_conversation_and_agent_runtime_.py`；
- `0ed29898ae52_create_file_objects_and_message_.py`。

## 3. 一次 Direct Answer 怎样执行

```text
浏览器 POST conversation turn
  → Conversation Application Service 校验 Workspace、mode、KB、附件和幂等键
  → 同一 PostgreSQL 事务写 Conversation/Turn/user Message/AgentRun/Job/Outbox
  → Dispatcher 发布已提交 Outbox
  → Celery Worker 调 DirectAnswerRunExecutionService
  → Loader 读取 Run、Turn、用户问题、可信 Runtime Context 和 READY 附件
  → ContextCompilerV0 生成 ModelRequest + ContextManifest
  → DirectAnswerRuntime 调 ModelProvider 并提交 agent.* Event
  → 唯一终态保存 stop reason；成功时保存 final assistant Message
  → 浏览器只重放已提交 Event，并可从正式 Trace 查看执行事实
```

相同 `Idempotency-Key` 和相同请求会返回同一组已创建 ID；同一个 key 搭配不同请求会返回冲突。Job payload 不保存用户问题，Worker 用 Run ID 回到 PostgreSQL 加载正式事实。

## 4. Runtime 的 typed contract

`AgentRun`、`AgentStep`、`AgentEvent`、`RunState`、`RunArtifact`、`RunBudget` 和 Checkpoint envelope 都带 schema/version。关键不变量包括：

- 一个 Event stream 的 sequence 从 1 连续递增；
- 一个 Run 只能有一个终态，并且终态必须有稳定 stop reason；
- Step sequence、Run state revision、usage 和费用只能按合法方向推进；
- Token、费用、deadline、max steps 与取消来自可信 Runtime Context，模型不能修改；
- Checkpoint 保存 schema version 和 optimistic revision，CAS 冲突及不兼容版本必须显式失败；
- Artifact 只保存 typed reference；L0 的最终 Markdown 是 Message，不伪装成 Artifact。

Day 2 的 Checkpoint 是基础契约与存储边界。LangGraph state 映射、interrupt/resume、审批恢复和副作用账本按主计划留到 Day 5。

## 5. Context Compiler v0 与附件

Compiler 把输入分成单独的模型消息：system instructions、可信 Runtime Context 的安全投影、可选会话摘要、当前问题，以及用户明确选择的每一个附件。Runtime Context 只投影当前 user/workspace 的允许字段，不会把数据库连接、HTTP client、Secret 或任意依赖对象序列化给模型。

附件规则是：

- 每个 Turn 最多 4 个附件，顺序写入 `message_attachments.ordinal`；
- 当前真实支持 UTF-8 TXT/Markdown，以及静态 PNG/JPEG/WebP；
- 文本源上限 1 MiB、提取文本上限 500,000 字符；图片上限 5,000,000 bytes、单边 4,096 像素、总计 16,000,000 像素；
- 服务端重新检查扩展名、MIME、magic bytes、实际大小、SHA-256、图片解码和尺寸，不能信任浏览器声明；
- 文本以“Untrusted attachment data”边界进入单独 USER 消息；图片以受控 `ModelImagePart` 进入 Provider 请求；
- 被用户明确选择的附件是必需 Context。预算放不下时整个编译稳定失败，不能悄悄丢附件后继续回答；
- manifest 记录 file ID、内容 hash、parser version、ordinal 和估算 Token，但不记录附件原文、base64、MinIO object key 或 Provider Secret。

图片只有在正式 model route 明确声明 `supports_image_input=true` 时才能进入调用；能力不匹配必须在调用前显式失败。

## 6. Provider 与失败语义

正式配置来自 `.env`：

- `AGENT_MODEL_PROVIDER_BASE_URL`；
- `AGENT_MODEL_PROVIDER_API_KEY`；
- `AGENT_MODEL_ROUTE_JSON`；
- `AGENT_MODEL_REQUEST_TIMEOUT_SECONDS`。

前三项必须一起存在。未配置时，正式 Run 返回明确的 `provider_not_configured`，不会退回 Fake。价格、上游 model ID、允许的响应 model ID 和图片能力都由 route 明确配置；usage 与价格版本用于计算可审计费用。

Adapter 把超时、429、无效 JSON/Schema、响应 model 不匹配、半截 stream 和上游拒绝映射为稳定错误与 stop reason。Harness 的 Fake/Replay 只证明项目边界和轨迹可重复，不证明真实模型输出确定，也不能代替真实 Provider 运行证据。

## 7. SSE、取消与刷新恢复

浏览器使用带认证头的 fetch-SSE，而不是无法附带项目认证信息的原生 `EventSource`。正式端点是：

- `GET /api/v1/workspaces/{workspace_id}/agent-runs/{run_id}/events`；
- `POST /api/v1/workspaces/{workspace_id}/agent-runs/{run_id}/cancel`；
- `GET /api/v1/workspaces/{workspace_id}/agent-runs/{run_id}/trace`。

每个业务帧固定为 `id: <sequence>`、`event: <agent.* type>` 和 `data: <versioned envelope>`。心跳是 comment，不推进游标。客户端保存最后成功处理的 sequence，并在重连时发送 `Last-Event-ID`；服务端只查询已提交 Event，不会因为浏览器断线而再次调用 Runtime 或重复当前 Model Step。

非法或超前 cursor 返回稳定错误。首次 PostgreSQL 回放只读取最新 256 条 Event；游标早于窗口时，数据库按已提交 delta 聚合出与最新 sequence 对齐的权威 snapshot，随后只拉取新 Event。每批最多 256 条，ASGI 只有在上一批已经交给 socket 后才查询下一批，所以慢客户端不会形成无限应用内队列，也不会丢编号事件。

取消端点只持久化幂等取消请求，不提前声称 Run 已终止。排队 Run 与 Job 在同一事务终止；运行中的 Runtime 即使还在等待 Provider 的首个 delta，也会轮询持久取消、取消 pending read、关闭 Provider stream 并写唯一终态。Worker/loader/Runtime 不可恢复异常和 Job dead-letter/lease 放弃由 Terminalizer/Reconciler 收敛为一个 `failed` 或 `cancelled` Event；Day 2 不伪装成从中断位置 resume。

用户问题在 Run 创建前已持久化。模型 delta 也是已提交 Event，因此超时、半截响应或断线后能够重放已收到片段；成功 final 另外保存为 assistant Message。当前前端的“重试”会把最近一次用户文本作为一个新的 Turn 再提交，并且不会静默复用旧附件。

## 8. Trace 与 Learning Workbench

Trace API 从 PostgreSQL 的正式 Run、Step、Context manifest 和 Event 表生成 Workspace-scoped 只读视图，包含：

- run/runtime/harness version、状态、stop reason 和 trace ID；
- Step 顺序、种类、状态、耗时和 usage；
- Context compiler/prompt/counter version、预算和每个来源的 included/decision/hash/version；
- 经过 allowlist 的 Event 详情。

Trace 不返回问题、回答正文、附件原文、object key、Secret 或原始 chain-of-thought。聊天工作台把正式消息和脱敏 Trace 分开展示：消息区域给用户看内容，Trace 面板解释执行过程。

## 9. Harness 数据集

Day 2 数据位于：

- Provider 边界 Scenario：`evals/scenarios/day2-v2.json`；
- Runtime/Application 可靠性 Scenario：`evals/scenarios/day2-reliability-v1.json`；
- Provider 回放：`evals/fixtures/day2-model-v1.json`；
- Trace 骨架：`evals/snapshots/day2-traces-v1.json`；
- 可比较 Eval 报告：`evals/reports/day2-v1.json`。

v2 Provider 数据集包含 6 个用例：正常 final、格式错误、timeout、429 rate limit、半截响应和取消。回归测试将每个用例通过同一 `DirectAnswerRuntime` 执行两次，比较 Event 类型/顺序、final 或 stop reason，并验证断线重放不会再次调用 Provider。

可靠性数据集另外登记 3 个版本化场景：费用预算耗尽、Worker 不可恢复中断收敛、重复 Turn 请求幂等。它不实现另一套 Runner：预算场景直接调用正式 `DirectAnswerRuntime`；中断场景调用正式 PostgreSQL Terminalizer/Reconciler，并验证已进入 `retry_wait` 的 Job 与 Run 原子收敛而不会安排一个注定失败的重试；重复请求场景调用 Conversation Application Service。每个场景绑定仓库内实际执行这些边界的测试函数。这样既保留 Scenario 版本和预期事实，又不会用 Fake Provider 假装覆盖 Worker/PostgreSQL 行为。

CLI 只输出不敏感的元数据：

```powershell
uv run --locked --package sec-filing-verification-agent-backend sec-filing-verification-agent-harness validate --dataset evals/scenarios/day2-v2.json
uv run --locked --package sec-filing-verification-agent-backend sec-filing-verification-agent-harness list --dataset evals/scenarios/day2-v2.json
```

可靠性数据集的 Schema、测试引用和 DoD 类别覆盖由 `test_day2_replay.py` 校验；被引用的 Runtime/Application/PostgreSQL 测试仍由 pytest 真正执行，JSON 报告不是测试通过与否的事实源。

## 10. HTTP 产品切片

Day 2 的主要正式接口如下：

- Conversation：创建 Turn、列表、详情、消息分页、重命名和软删除；
- File：预签名上传、complete、状态查询、短期下载 URL 和删除；
- Agent Run：committed SSE、取消和脱敏 Trace。

Turn 持久化 `search_mode`、`industry_id` 和 `knowledge_base_ids`，消息响应恢复同一快照。Day 2 只启用 `none`；`web` 留到 Day 3，`local/both` 留到 Day 5，未就绪模式返回 `CONVERSATION_MODE_NOT_READY`，不生成 Mock 搜索结果。

前端 Markdown 采用显式 allowlist 解析，只允许安全的文本结构和经过协议检查的链接，不使用 `dangerouslySetInnerHTML`，也不展示原始 chain-of-thought。

## 11. 验证入口与完成条件

针对性证据包括：

- `apps/backend/tests/modules/agent_runtime/` 与 `agent_harness/`：领域不变量、Checkpoint、Context、Runtime、SSE、Trace、6 个 Provider Scenario 双次回放和 3 个可靠性 Scenario 的严格引用校验；
- `apps/backend/tests/adapters/test_openai_compatible.py`：complete/stream、usage/费用、timeout、429、格式和半截响应；
- `apps/backend/tests/integration/test_conversation_agent_postgres.py`、`test_agent_execution_loader_postgres.py`、`test_agent_trace_postgres.py`：原子提交、附件入模和正式 Trace；
- `test_agent_run_reliability_postgres.py`：Worker 中断、运行 Step 和 dead-letter 的唯一终态补偿；
- `test_agent_fake_success_postgres.py`：Conversation → Outbox → JobExecutionRuntime → 唯一 `DirectAnswerRuntime` → PostgreSQL final Message/Event → committed replay 的正式成功链；只有外部 Provider Port 使用确定性 Fake，重放不再次调用模型；
- `test_agent_unconfigured_provider_postgres.py`：使用真实生产 composition 验证未配置 Provider 不出网、不回退 Fake、保留用户消息并写一个可解释失败终态；
- `apps/backend/tests/integration/test_file_lifecycle_postgres_minio.py` 与 `test_file_attachments_minio.py`：真实私有 MinIO；
- `apps/web/src/chat/*.test.ts(x)` 与 `tests/e2e/app-shell.spec.ts`：聊天 API、fetch-SSE、安全 Markdown 与浏览器旅程。

真实 MinIO 测试需要服务运行并设置 `MINIO_TESTS_REQUIRED=1`；PostgreSQL/Redis 对应使用 `POSTGRES_TESTS_REQUIRED=1` 和 `REDIS_TESTS_REQUIRED=1`。

2026-08-15 的最终本地收口结果为：Ruff format/check 覆盖 245 份 Python 文件，mypy 覆盖 240 个文件；真实 PostgreSQL、Redis、MinIO 门禁全部强制开启时 708 个 pytest 全部通过且无 skip，fresh migration、Python build/audit 同时通过。Web format/lint/typecheck、10 个 Vitest 文件共 42 个测试、生产构建、OpenAPI 确定性、Node audit 和 3 条 Playwright 浏览器旅程通过；受控源码/配置路径与 39 个 Git 提交的 Gitleaks 扫描未发现 Secret。版本化报告只保存可重复测量和测试绑定，不冒充这些命令的事实来源。

提交 [`bf4feaff`](https://github.com/hrw991009/industry-intelligence-platform/commit/bf4feaff2e0fa5487a6f01ed0fd4cd63f5b4f659) 的 push 与 pull request CI 均成功；其中 [CI 31922391846](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/31922391846) 的 Python、PostgreSQL/Redis/MinIO、Web、Browser E2E、依赖审计和 Secret history Job 全部通过。学习者于 2026-08-16 用自己的话完成职责复盘；复核时进一步明确：Outbox Dispatcher 负责把已提交 Job 通知发布到 Redis/Celery，Worker 消费通知后从 PostgreSQL 领取权威数据并调用 Handler/Runtime；Day 2 Checkpoint 只完成版本化信封和 CAS 基础，真正的中断续跑属于 Day 5。真实 Provider smoke 可以补充信心，但没有配置 Provider 时只能使用 Fake/冻结回归证明契约，并用正式 `provider_not_configured` 链路证明不会回退到 Fake，不能把它写成真实模型质量成功。D2-06 的 Citation 对 Day 2 L0 明确不适用，因为主计划把 Evidence/Claim 放在 Day 4、把真实 Citation gate 放在 Day 6；Day 2 不生成没有 Evidence locator 的空引用或伪引用。

### 11.1 Day 2 Definition of Done 复核

| 条目 | Day 2 结论 | 证据或边界 |
|---|---|---|
| 真实用户旅程 | 通过（本地） | 3 条 Playwright 旅程覆盖身份、真实附件、停止/重试、会话管理，以及浏览器 POST → PostgreSQL Outbox/Job → 正式 JobExecutionRuntime → 唯一 DirectAnswerRuntime（仅 ModelProvider 使用测试 Fake）→ 两段 committed SSE → final Message → 刷新恢复的成功链 |
| 正常/边界/失败/权限/恢复 | 通过（本地） | 6 个 Provider Scenario、3 个可靠性 Scenario、SSE snapshot/慢客户端、deadline、partial 历史和 Worker 中断收敛均包含在 708 个无 skip 的 pytest 与 42 个 Vitest 中 |
| Migration、OpenAPI/SSE、兼容 | 通过 | 4 份线性 Alembic migration、OpenAPI 无漂移、SSE v1/游标/snapshot 测试 |
| 日志、指标、Trace、错误码 | 通过（Day 2 范围） | Runtime/Worker 终态输出不含正文的可聚合字段；Run/Step 持久 usage、费用、耗时和 stop reason 并由 Trace/Workbench 展示；完整 OTLP 聚合栈留 Day 7 |
| 数据所有权、删除、补偿、备份策略 | 通过 | PostgreSQL/MinIO 所有权、删除/故障补偿测试和 Day 2 Runbook；真正恢复演练仍是 Day 7 发布门禁，不冒充已演练 |
| 威胁、隐私、Secret | 通过 | Context/附件/Markdown/egress 负向测试、[Day 2 安全复核](security/day-2-third-party-review.md) 与 Gitleaks |
| 第三方许可和条款 | 通过（当前使用范围） | httpx2、MinIO SDK/Pillow、MinIO 镜像和 Provider 数据边界已人工记录；真实 Provider 启用前需复核其具体条款 |
| 可重复 Eval | 通过（本地） | 6 个 Provider Scenario、3 个绑定可执行测试的可靠性 Scenario、冻结 fixture/Trace 与版本化报告全部通过；不声称 Fake 代表真实模型质量 |
| README/Runbook/回滚 | 通过 | 根 README、本文和 [Day 2 Runbook](runbooks/day-2-agent-runtime.md) |
| 干净环境演示 | 通过 | 提交 `bf4feaff` 的 CI 31922391846 在 GitHub 干净环境通过全部适用 Job |
| 学习复盘 | 通过 | 学习者已解释 Runtime、Harness、Worker、Checkpoint 与 Trace；复核时校正 Dispatcher/Worker 数据流和 Day 2 Checkpoint 不提供真实 resume 的边界 |

### 11.2 Agent 追加 DoD 的适用性复核

| 主计划要求的路径 | Day 2 结论 | 版本化 Scenario 或边界 | 复核记录 |
|---|---|---|---|
| 成功 | 适用 | `day2-direct-answer-basic` | 执行代理，2026-08-15 |
| Provider 失败 | 适用 | invalid format、timeout、429、half response 四个 v2 场景 | 执行代理，2026-08-15 |
| 取消 | 适用 | `day2-direct-answer-cancelled` | 执行代理，2026-08-15 |
| 预算耗尽 | 适用 | `day2-cost-budget-exhaustion`，绑定正式 Runtime 测试 | 执行代理，2026-08-15 |
| 中断/恢复 | Day 2 的失败收敛适用；同一次模型调用的 durable resume 阶段性 `N/A` | `day2-unrecoverable-worker-interruption` 证明不可安全恢复时 Run/Job 原子收敛且各自只有一个终态；即使 Job 已进入 retry_wait 且仍有次数，也不会留下一个注定失败的重试。LangGraph graph state、Checkpoint resume 和幂等副作用恢复由主计划 D5-09 验收，Day 2 不能提前冒充 | 执行代理，2026-08-15；进入 Day 5 前由项目所有者复核范围仍未降级 |
| 重复请求 | 适用 | `day2-duplicate-turn-request`，证明相同 payload 复用 Run/Job/Outbox，变更 payload 明确冲突 | 执行代理，2026-08-15 |
| Tool 失败 | 阶段性 `N/A` | Day 2 是 `available_tools=[]` 的 L0 单模型调用，没有 Tool Action/Observation 或 Tool 副作用。主计划 D3 的 L1/L2 必须新增工具失败 Scenario，本结论不豁免或提前完成 Day 3 | 执行代理，2026-08-15；进入 Day 3 后由项目所有者复核对应义务已经恢复为适用 |

## 12. Day 5 Research L4 扩展

Day 2 的 `CheckpointEnvelope`/CAS 现由 Day 5 Step 5 接入正式 PostgreSQL Store，并作为唯一
Research graph 的恢复事实。`ResearchL3Runtime` 保留兼容名称；配置 CheckpointStore 与
ResearchDurabilityService 时执行 `research-l4-graph-v1`：每个成功节点保存 typed payload 和
`checkpoint.saved` Event，审批或安全节点 hard stop 后从 `next_node` 继续同一 Run。

L4 没有改变 L0/L1/L2 的公共执行语义：

- Loader 重新加载当前 Workspace membership、Brief/FinancialScope、Run Budget 和 Tool policy；
- Checkpoint scope 必须与 Brief scope 一致，schema/node/Event 尾部不一致时 fail closed；
- 持久 Approval/Decision 与 resume proof 绑定 Checkpoint revision；proof 原文不落库；
- resume Job 与 Outbox 原子创建，重复 resume 返回已有 Job；
- side-effect ledger 以稳定幂等键收敛 Tool/Evidence/Artifact，恢复不重复 Knowledge/calculator；
- `approval_denied`、`approval_timed_out` 加入统一 stop reason，Trace 只展示安全摘要。

正式接口、payload、迁移与回滚见 [Research L4 Checkpoint 与 HITL 合同](research-checkpoint-contract.md)
和 [Day 5 Research L4 运行手册](runbooks/day-5-research-l4.md)。该实现已由 PR #9 合入 `main`，
分支/PR/main CI 均成功；但同一 ready SEC fixture 的暂停/审批/resume/刷新浏览器旅程尚无证据，
因此 D5-09 保持 `implemented_pending_verification`。后台超时扫描和 Day 8 跨刷新/Worker 重启组合证据也未完成；这既不扩大 L4 声明，也不反向改写本文件前述 Day 2 历史验收结论。
