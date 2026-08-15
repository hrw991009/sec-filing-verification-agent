# Agent Runtime v0

> 更新日期：2026-08-14
>
> 计划基线：`docs/master-plan.md` 1.7.0 Day 2
>
> 当前状态：D2-01～D2-09 的正式实现已经写入，统一本地门禁、真实 PostgreSQL/Redis/MinIO 和浏览器 E2E 已通过，均为 `implemented_pending_verification`。恢复收敛、生产 SSE snapshot/背压、可观测性、部分端到端证据、干净 CI 与学习者复核仍未关闭，因此不能标为 `complete`。

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

非法、超前或已过期的 cursor 会返回稳定错误；需要时服务端先返回 snapshot，再继续连续 Event。取消端点只持久化幂等取消请求，不提前声称 Run 已终止；Runtime/Worker 在正式边界观察取消并写唯一终态。

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

- Scenario：`evals/scenarios/day2-v2.json`；
- Provider 回放：`evals/fixtures/day2-model-v1.json`；
- Trace 骨架：`evals/snapshots/day2-traces-v1.json`。

v2 数据集包含 6 个用例：正常 final、格式错误、timeout、429 rate limit、半截响应和取消。回归测试将每个用例通过同一 `DirectAnswerRuntime` 执行两次，比较 Event 类型/顺序、final 或 stop reason，并验证断线重放不会再次调用 Provider。

CLI 只输出不敏感的元数据：

```powershell
uv run --locked --package industry-platform-backend industry-platform-agent-harness validate --dataset evals/scenarios/day2-v2.json
uv run --locked --package industry-platform-backend industry-platform-agent-harness list --dataset evals/scenarios/day2-v2.json
```

## 10. HTTP 产品切片

Day 2 的主要正式接口如下：

- Conversation：创建 Turn、列表、详情、消息分页、重命名和软删除；
- File：预签名上传、complete、状态查询、短期下载 URL 和删除；
- Agent Run：committed SSE、取消和脱敏 Trace。

Turn 持久化 `search_mode`、`industry_id` 和 `knowledge_base_ids`，消息响应恢复同一快照。Day 2 只启用 `none`；`web` 留到 Day 3，`local/both` 留到 Day 5，未就绪模式返回 `CONVERSATION_MODE_NOT_READY`，不生成 Mock 搜索结果。

前端 Markdown 采用显式 allowlist 解析，只允许安全的文本结构和经过协议检查的链接，不使用 `dangerouslySetInnerHTML`，也不展示原始 chain-of-thought。

## 11. 验证入口与完成条件

针对性证据包括：

- `apps/backend/tests/modules/agent_runtime/` 与 `agent_harness/`：领域不变量、Checkpoint、Context、Runtime、SSE、Trace、6 个 Scenario 双次回放；
- `apps/backend/tests/adapters/test_openai_compatible.py`：complete/stream、usage/费用、timeout、429、格式和半截响应；
- `apps/backend/tests/integration/test_conversation_agent_postgres.py`、`test_agent_execution_loader_postgres.py`、`test_agent_trace_postgres.py`：原子提交、附件入模和正式 Trace；
- `apps/backend/tests/integration/test_file_lifecycle_postgres_minio.py` 与 `test_file_attachments_minio.py`：真实私有 MinIO；
- `apps/web/src/chat/*.test.ts(x)` 与 `tests/e2e/app-shell.spec.ts`：聊天 API、fetch-SSE、安全 Markdown 与浏览器旅程。

真实 MinIO 测试需要服务运行并设置 `MINIO_TESTS_REQUIRED=1`；PostgreSQL/Redis 对应使用 `POSTGRES_TESTS_REQUIRED=1` 和 `REDIS_TESTS_REQUIRED=1`。

本轮最终工作树已实际通过：

- Ruff format/check：238 个 Python 文件；mypy：233 个 source files；
- pytest 快速集：624 passed、51 skipped；这些 skip 全部是受环境开关保护的真实依赖测试，随后已强制开启并单独通过 51 个；
- 真实 PostgreSQL `alembic upgrade head`，以及 PostgreSQL/Redis/MinIO integration：51 passed；
- Web：Prettier、ESLint、TypeScript、22 个 Vitest、production build；
- OpenAPI 与 `schema.d.ts` 重新生成后 SHA-256 不变；
- Playwright：2 passed，其中 Day 2 旅程真实调用 Conversation 202、committed SSE、cancel 202，并在刷新后恢复消息；
- `uv audit`、`pnpm audit --audit-level high`、本次改动目录和完整 Git 历史的 Gitleaks 均通过。

当前仍不把任何 Day 2 项标为 `complete`：Worker 中断后的 AgentRun 唯一终态收敛、首个 delta 前取消、生产 SSE snapshot/背压、结构化日志/指标、部分端到端证据、当前提交的干净 GitHub CI 与学习者复核尚未关闭。真实 Provider smoke 可补充验证；若没有付费 Provider，则必须用正式 `provider_not_configured` 全链路失败证据替代，不能让 Fake 冒充生产成功。D2-06 的 Citation 对 Day 2 L0 明确不适用，因为主计划把 Evidence/Claim 放在 Day 4、把真实 Citation gate 放在 Day 6；Day 2 不生成没有 Evidence locator 的空引用或伪引用。
