# ADR 0001：采用模块化单体、独立 Dispatcher、Worker 与 Beat

> 状态：已接受
>
> 日期：2026-08-03
>
> 修订日期：2026-08-12
>
> 依据：`docs/master-plan.md` v1.7.0 第 3.1、3.3、4、6.4、Day 2 与 17.1 节

## 背景

本项目需要在七天版本中高质量完成能力矩阵从两个参考项目映射并冻结的全部目标能力，包括身份、聊天、知识库、文档入库、检索、工具、记忆和 Research 等。

普通回答、Tool Use 和 Deep Research 还必须共享同一套 Agent Run/Step/Event/Budget、model/tool loop、Checkpoint 与 Trace 语义。Agent Runtime 决定一次智能任务如何推进，Agent Harness 决定该 Run 可使用的 Instructions、Context、Tool/Skill、Memory、Knowledge/RAG、Approval、Artifact 与 Eval hook；Celery 只决定代码在哪个进程可靠执行。这三层如果混在 Router、Worker 或 LangGraph 节点中，同样会形成难以恢复和评测的第二套正式链路。

OCR、Embedding、索引、模型调用、采集和 Research 都可能长时间运行，不能占用 Web 请求。同时，项目仍处于单团队、单仓库和快速验证阶段，如果立即拆成微服务，会引入服务发现、网络契约、分布式部署、跨服务事务和额外运维成本。

另一方面，如果只建立一个没有边界的普通单体，Router、数据库、Provider SDK 和任务逻辑会相互耦合，最终形成难以测试和替换的“大泥球”。

## 决定

七天版本采用：

```text
模块化单体 + 独立 FastAPI API + Outbox Dispatcher + Celery Worker + Celery Beat
```

具体约束如下：

1. 所有后端业务代码只有一个正式包入口：`apps/backend/src/industry_platform`。
2. 按业务能力划分 `identity`、`agent_runtime`、`agent_harness`、`files`、`knowledge`、`ingestion`、`retrieval`、`context`、`evidence`、`conversation`、`memory`、`tools`、`research`、`industry`、`data_explorer`、`jobs` 和 `evaluation`。
3. Router 只能调用 Application Service。
4. Service 通过 Repository 或 Port 使用数据库和外部能力。
5. 具体供应商 SDK 只能位于 `adapters/`。
6. Worker 复用与 API 相同的 Application Service；Agent 命令由该 Service 调用唯一 `AgentRuntime.run`，Worker 不直接调用 Provider、选择 Tool 或复制第二套业务逻辑。
7. Outbox Dispatcher 只负责把 PostgreSQL 已提交的 Outbox 至少一次发布给 Celery，不执行业务任务。
8. Celery Beat 只计算到期计划，并调用同一 Application Service 幂等创建 ScheduleOccurrence、业务 Run、Job 和 Outbox；不得直接向 Redis/Celery 发布。
9. 模块文件按真实职责创建，不为了目录整齐生成空文件。
10. 不允许出现 `backend/app`、`backend/service` 等第二套正式入口。
11. 不允许 v1、v2、v3 多条正式业务链路并存。
12. `agent_runtime` 负责 AgentRun/Step、typed State、model/tool loop、Event、Budget、取消、唯一终态、Checkpoint 与 Trace；它只依赖领域 Port，不依赖具体 Adapter、Router 或前端。
13. `agent_harness` 在 Runtime 上组合 Context Compiler、Tool/Skill、Memory、Knowledge/RAG、Guardrail、Approval、Artifact 与 Eval hook，不实现第二套生产 loop；Evaluation Harness 使用同一 Runtime/Harness，只替换外部边界、注入故障和运行 Scorer。
14. `conversation` 和 `research` 不得直连 Model Provider；它们通过 Harness/Runtime 执行。LangGraph 只承载 Deep Research typed graph，并映射统一 AgentRun/Event/Checkpoint，不复制 Application Service 或 ToolExecutor。
15. 依赖方向固定为：`conversation → agent_harness/evidence`、`research → agent_harness/retrieval/industry/data_explorer/evidence`、`agent_harness → agent_runtime/context/tools/memory/knowledge/retrieval/approval/evaluation ports`、`agent_runtime → provider/tool/checkpoint/trajectory ports/jobs/evidence`。

## 结果

### 收益

- 在一个仓库中保持较低的开发和调试成本；
- 业务模块具有明确边界；
- API 和 Worker 可以复用同一业务规则；
- 普通回答、Tool Use、Research 和 Evaluation Harness 使用同一生产执行语义；
- Provider、存储和消息系统可以通过 Port/Adapter 替换；
- 模块边界能够通过导入规则、契约和测试独立验证；
- 单元测试和集成测试不需要跨多个网络服务才能运行。

### 代价与风险

- 模块边界主要依靠目录、导入规则、测试和代码审查维护；
- 一个部署单元中的错误可能影响多个业务模块；
- 如果 Service 不断吸收无关职责，仍可能退化为大泥球；
- 如果 Harness、LangGraph 节点或 Worker 私自实现 loop，会形成难以察觉的第二套 Runtime；
- API、Dispatcher、Worker 与 Beat 虽然复用代码和镜像，但必须分别管理进程生命周期和数据库 Session。

## 否决方案

### 第一周立即拆分微服务

否决原因：在没有稳定领域边界、负载数据和独立团队的情况下，微服务只会提前引入网络和运维复杂度，违反主计划第一周不拆微服务的边界。

### 所有逻辑放入 Router 或万能 Service

否决原因：会造成 HTTP、领域规则、数据库和 Provider 实现相互耦合，难以测试，也无法让 Worker 安全复用。

### API 与 Worker 分别实现业务逻辑

否决原因：会产生两套正式链路，导致权限、事务、错误码和状态规则逐渐漂移。

### 让 Worker、Conversation 或 LangGraph 节点直接调用 Provider

否决原因：这会绕过 Runtime 的 State/Event/Budget/stop reason、Harness 的 Tool/Context/Approval policy 和统一 Trace，使聊天、Research 与评测形成不同执行语义。

### 让测试 Harness 自己实现 model/tool loop

否决原因：测试结果无法代表生产路径。确定性 Fake、Replay、Fault injection 与 Scorer 必须通过同一 Runtime/Harness，只替换正式 Port 边界。

## 验证

- 代码审查确认 Router 只调用 Service；
- Provider SDK 只出现在 `adapters/`；
- Worker task 复用 Application Service；
- Worker、Conversation 和 Research 不直接导入具体 Provider Adapter；
- 普通回答、Tool Use 和 Research 都能反查到同一 `AgentRuntime.run` 入口；
- Harness 和 Evaluation Harness 中不存在第二套 model/tool loop；
- LangGraph state、Step、Event 和 Checkpoint 能映射到统一 AgentRun 语义；
- Beat 复用 Application Service 且不能直接调用 Celery 发布 API；
- 只有 Dispatcher 从持久 Outbox 发布 Celery 消息；
- 为关键模块建立导入边界测试；
- 确认不存在第二套后端入口；
- 确认每个 HTTP 请求、Celery task 和并发协程使用独立 SQLAlchemy Session；
- CI 持续执行类型检查、单元测试、集成测试和构建。

## 变更与回滚

任何改变模块化单体、进程职责或部署边界的决定都必须新增 ADR，说明证据、数据所有权、契约兼容、迁移、双写风险和回滚方案。

在新的架构决策完成验收前，本文决定仍是唯一正式链路，不能同时维护两套业务实现。
