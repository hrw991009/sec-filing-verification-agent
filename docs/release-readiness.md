# SEC 披露核验 Agent 发布就绪合同

> 合同编号：`IIP-RELEASE-SEC-001`
>
> 版本：`0.4.0`
>
> 制定日期：2026-08-31
>
> 权威范围：[主计划](master-plan.md) v2.2.11 Day 10、[能力矩阵](feature-matrix.md) D10-01～D10-08
>
> 当前状态：Step 1～Step 3 本地实现已完成；D10-01/D10-02 为 `implemented_pending_verification`，D10-04/D10-05/D10-07 为 `thin_slice`，当前判定仍为 `NO_GO`

## 1. 目标与真值来源

Day 10 不再扩张产品范围，而是证明现有 SEC 披露与财务事实核验 Agent 在冻结范围内可用、可评、可恢复、可审计。发布结论必须从正式事实生成，不能由页面截图、人工改库、Mock、文档勾选或一次成功运行代替。

证据优先级固定为：

1. 正式 PostgreSQL/对象存储事实、Run/Trace/Event、Evidence/Calculation、Checkpoint/Approval、Monitor/Case 与版本化 Eval report；
2. 可重复执行的测试、迁移、恢复演练、浏览器旅程和受控 release job；
3. branch/PR/main 三层 CI、项目所有者签字和外部 Provider/许可证处置记录；
4. 说明性文档与截图，只能解释前述证据，不能替代前述证据。

`evals/reports/sec-release-failure-taxonomy-v1.json` 是评测阻断项的机器真值，本文只做跨 Day 的发布投影，不复制或手工覆盖其计数。能力状态以 `docs/feature-matrix.md` 为准；两者不一致时必须 fail closed 并修复生成链或文档，不选择更宽松的一方。

## 2. 发布状态机

| 状态 | 含义 | 允许动作 |
|---|---|---|
| `NO_GO` | 任一 P0 能力、机器 blocker、外部硬门或必需证据未关闭 | 继续修复和补证；不得创建发布标签或对外宣称完整能力 |
| `RC_READY` | D10-01～D10-08 均为 `complete`，所有发布硬门通过，分支已合并且 main CI 全绿，所有者已签字 | 可创建 `v0.2.0-sec-disclosure-verifier` 候选标签并执行最终 smoke |
| `RELEASED` | 候选标签对应不可变提交，标签后 smoke、报告 hash、回滚点和发布说明复核通过 | 可发布冻结范围内的能力声明 |

状态变化必须由新证据驱动；任何必需报告变更、依赖漏洞、secret、数据/许可问题或回归都会退回 `NO_GO`。创建标签是显式所有者操作，不由普通 Step 或 CI 自动执行。

## 3. 初始阻断台账

| 阻断族 | 当前事实 | 关闭证据 | 负责步骤 |
|---|---|---|---|
| D1-09 凭据处置 | 参考仓 6 组候选仍需 Provider 侧吊销/轮换与复扫 | Provider 处置记录、仓库与历史复扫、所有者确认 | Step 5 |
| Day 4 覆盖率债务 | 核心 domain/application 最近登记为 86%，目标为 ≥90% | 固定模块集合、真实依赖 coverage artifact、未用排除规则降低分母 | Step 4 |
| Day 5 浏览器链 | 页面已连接 Filing→Research→Verification→Evidence，但尚无无拦截真实依赖浏览器证据 | Playwright 真实 API/数据库旅程与失败制品 | Step 2 |
| Day 6 来源收口 | `sec-source-v1` 为 22/24，bulk watermark/post-watermark gap 与 live SEC 身份仍缺 | 保留 24 分母的重算报告、合法 SEC 身份、snapshot/watermark 与 gap 证据 | Step 3 |
| Day 7 Retrieval/Citation | Recall@5 未测，ranking/table locator/Citation 与真实链仍缺 | ranked candidates、Recall@5≥0.80、Citation 100% 可解析、正式 Run 绑定 | Step 3 |
| Day 8 Monitor/恢复 | 专用审批浏览器旅程和真实 hard-stop/lease/通知不确定性仍缺 | 组合故障演练、恢复率 100%、重复副作用 0、正式 Workbench 反查 | Step 2、Step 4 |
| Day 9 可比评测 | common-case A0～A4 合同已冻结，但 50 个 offline Run、Runtime binding、公开集 prediction 与默认策略仍缺 | 同 manifest/data/Scope/budget 的分层报告与可复算决策 | Step 3 |
| Day 9 外部治理 | FinanceBench 文档权利、中文抽样、live 依赖/≥3 次和 owner review 仍缺 | 明确纳入或排除决定、签字清单、受控 live 报告；不得伪造 N/A | Step 3、Step 5 |
| 产品与运维发布门 | 中文路径已有本地实现；真实浏览器、可观测、安全、fresh start、备份恢复、索引重建和上一镜像回滚尚未统一验收 | 浏览器、Trace/指标、安全集、Runbook 演练和 main CI | Step 2～Step 5 |

外部凭据、数据权利、模型凭据或人工签字若在 Day 10 结束时仍缺，状态保持 `NO_GO`。可以提交代码和文档，但不能删除目标、改写为 `N/A`、降低分母或创建发布标签。

## 4. 五步证据链

| 步骤 | 入口 | 产物 | 退出门 |
|---|---|---|---|
| 1. 发布台账与双向审计 | 当前 `main`、能力矩阵、DoD、机器 failure taxonomy、Day 1～9 日志 | 单一 release manifest/ledger、每项 owner、命令、artifact、状态和依赖图 | 每个冻结目标与 blocker 均可双向追踪；未知项为阻断，不是通过 |
| 2. 真实中文闭环与工作台 | 已有 SEC/Research/Monitor API、Runtime 与 Workbench | 唯一路由、统一状态、正式数据驱动的中文 E2E、Evidence/Calculation/Approval/Case 反查 | 无人工改库/Mock；正常、拒答、冲突、审批、刷新和恢复路径均有浏览器证据 |
| 3. Release Eval、可观测与安全 | Day 6～9 manifests/scorers、生产 Runtime、Trace/Evidence | common-case A0～A4、Recall@5、offline/live 分报、Run binding、指标/告警与安全报告 | 硬阈值通过；不同证据层不平均；许可/人工/live 缺口仍阻断发布 |
| 4. 工程质量、故障恢复与回滚 | Compose/CI、迁移、存储、Worker 与发布镜像 | 覆盖率、全量真实依赖门、fresh start、备份恢复、索引重建、依赖故障和上一镜像演练 | 核心≥90%、后端≥80%、关键前端≥75%；恢复 100%、重复副作用 0 |
| 5. 完整性审计与候选发布 | 前四步不可变 artifacts、branch/PR/main CI | README/ADR/架构/评测/Runbook/限制、owner acceptance、RC 决策 | D10-01～D10-08 全部 `complete` 且 blocker=0 后才可进入 `RC_READY` |

## 5. Step 1 机器合同

唯一机器入口为：

```text
pnpm run eval:release-readiness
```

输入为 `evals/manifests/sec-release-readiness-v1.json`、正式能力矩阵和既有 failure taxonomy。生成器位于 `industry_platform.modules.evaluation.release_readiness`，输出：

- `evals/reports/sec-release-readiness-v1.json`；
- `evals/reports/sec-release-readiness-v1.md`；
- `evals/schemas/release-readiness-manifest-v1.schema.json`；
- `evals/schemas/release-readiness-report-v1.schema.json`。

当前报告绑定 88 个正式目标和 49 个 repository artifacts：45 个目标为 `complete`，43 个仍未完成；当前状态分布为 32 个 `implemented_pending_verification`、8 个 `thin_slice` 和 3 个 `planned`。9 个 evaluation taxonomy blocker 与 7 个跨 Day blocker 全部 open；Day 9 三层 CI 已验证，最终 owner acceptance、历史凭据处置、外部许可、中文抽样和 live Provider/SEC identity 共 5 个 external gate 仍 pending。因此 `release_decision=no_go`、`rc_ready=false`。

生成器要求矩阵十张正式能力表、目标 ID/digest/状态计数、taxonomy 映射、非完成目标↔开放 blocker、pending gate↔开放 blocker 双向一致。所有登记 artifact 必须存在于仓库内、非空并生成 SHA-256；缺失、越界、状态冲突、无证据却标记 verified、taxonomy blocker 伪关闭或 checked report 未重生成都会失败。

Step 1 本地证据为聚焦 `8 passed`、evaluation `65 passed`、readiness branch coverage `84%` 和无强制真实服务全量 `1184 passed, 88 skipped`；Python/Web/构建/OpenAPI、依赖审计与 98-commit Gitleaks 通过。真实依赖、Chromium 和远端三层 CI 未在本步执行，不能据此关闭相应 blocker。

## 6. Step 2 产品路径边界

SEC Workbench 只从已锁定、状态为 `ready` 的 Filing import 生成 typed `FinancialScope` 草稿，Research Workbench 消费后仍调用正式 `StartResearch` API。最新 Verification Report、Trace、Claim、Durability、Approval、Monitor、Case 和 Evidence 均从各自正式 owner API 读取；页面不保存第二套业务状态，也不把 amendment 静默转成原始 form。服务端没有报告时明确显示未生成，不以旧结果或 Draft 推断四态。

组件与 API 回归证明 scope 交付、四态报告、Evidence drilldown、Monitor/Case 重建和刷新逻辑；它们不是完整用户旅程。只有 Playwright 在不拦截业务 API、不人工改库的条件下，使用真实认证及 PostgreSQL/Redis/MinIO/Elasticsearch/Milvus、受控 SEC source 和 Worker 完成中文问题→核验→引用→审批→Case，并覆盖拒答/冲突/forbidden/cancel/retry/refresh，才能关闭 Step 2 浏览器门。当前该证据缺失，所以 Day 5/8 相关 blocker 继续 open。

## 7. Step 3 评测、观测与安全边界

`pnpm run eval:release-evidence` 复用 `sec-tool-v1` 的 10 个 case，冻结 A0～A4 相同 case/Scope/budget、offline 1 次与 live 至少 3 次的合同。Run evidence 必须包含正式 Run/Trace/Workspace、Evidence/Calculation、数据库终态 hash、ranked candidates、Citation、Token/成本/延迟以及 future source、跨 Workspace、注入、未授权写、重复副作用和恢复计数。scorer 生成 checked JSON/Markdown/schema 和 11 个告警状态；`release-suite-v1` 只消费该受检报告，不重新解释 observation。

当前 checked observation 明确为 `not_executed`：offline 分母固定为 10×5=`50`，实际为 `0`；Recall@5、Citation、runtime binding、安全和恢复指标均为 `not_measured`，对应告警为 `unknown`，生产默认策略为 `null`。因此原“common-case manifest 缺失”已精确收敛为“common-case production Runs 缺失”，但 taxonomy 仍有 9 个 release blocker。真实 Runtime、公开集 prediction、live≥3、合法 SEC/provider、许可与中文签字都没有被本地合成测试替代。

Step 3 本地证据为聚焦 `22 passed`、evaluation `72 passed`、后端 `1191 passed, 88 skipped` 和 Web `94 passed`；Ruff format/check、mypy、Prettier、ESLint、TypeScript、生产 build 与 OpenAPI 确定性检查通过。88 个 skip 均需显式启用 PostgreSQL、MinIO 或 Redis，不能当作真实依赖通过；本地结果也不替代远端 branch/PR/main CI。

## 8. CI 与运行分层

- PR/push CI 只运行确定性、无公网、无付费模型的 quick suite 和工程门禁；不能因外部 SEC/provider 波动阻塞普通 PR。
- release job 运行真实依赖、公开 benchmark prediction、受控 live suite、恢复演练和完整 artifact 归档；失败必须分类，不能回填 deterministic 成绩。
- main CI 验证合并提交本身。PR CI 全绿不等于 main CI 全绿，两者都不能替代 owner acceptance。
- GitHub Actions 的 Linux runner 只是可复现 CI 环境，不是 Day 10 新增的 Linux 客户端或产品版本；本版本不建设桌面发行版。

## 9. 发布声明边界

允许的声明必须带 evidence layer、数据/模型/Tool/Prompt 版本、日期、分母与已知限制。禁止把 frozen replay 写成 live 能力，把 Adapter 可运行写成 benchmark 得分，把公开 benchmark 得分写成 SEC 产品可用性，或把局部 A1→A2、A2→A3、A3→A4 结果拼成全局 A0～A4 结论。

产品仅提供 SEC 披露事实核验，不输出预测、估值、目标价、荐股、交易动作或审计意见。页面、导出与发布说明必须保留这一边界。
