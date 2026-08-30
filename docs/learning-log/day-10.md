# Day 10 执行计划：SEC 工作台、发布评测与完整交付

> 制定日期：2026-08-30
>
> 计划基线：[Day 1～Day 10 主计划](../master-plan.md) 2.2.8 Day 10
>
> 能力边界：[Day 1～Day 10 目标能力矩阵](../feature-matrix.md) D10-01～D10-08
>
> 权威评测合同：[SEC 披露与财务事实核验 Agent 评测计划](../sec-agent-evaluation.md)
>
> 发布合同：[SEC 披露核验 Agent 发布就绪合同](../release-readiness.md)
>
> 当前状态：文档规划完成，Step 1～Step 5 尚未开始，D10-01～D10-08 均为 `planned`

## 1. 进入基线与最后一天边界

Day 9 已由 [PR #15](https://github.com/hrw991009/industry-intelligence-platform/pull/15) 合入 `main`，功能 head 为 [`6a79e4a`](https://github.com/hrw991009/industry-intelligence-platform/commit/6a79e4a)，合并提交为 [`4500505`](https://github.com/hrw991009/industry-intelligence-platform/commit/4500505)。功能 head 的 push CI [`33302689820`](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/33302689820)、PR CI [`33302716257`](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/33302716257) 与合并提交 main CI [`33303336316`](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/33303336316) 均已通过全部 7 个适用 Job。

合并只证明 Day 9 代码进入 `main`。`release-suite-v1` 当前仍有 9 个机器可读 release blocker：缺统一 A0～A4 common cases、Recall@5、真实 Runtime/Run/Trace/Evidence 绑定、公开集 prediction、外部许可复核、live 依赖、每项至少 3 次 live 重复、中文人工签字以及最终远端 CI/owner closeout。它们与 Day 1～8 遗留债务共同进入 Day 10，不因进入最后一天而删除、降级或改写分母。

Day 10 只做发布收口：补齐真实产品闭环、评测证据、质量安全、恢复回滚和发布文档。它不增加新市场、form、Provider、Agent 角色、微服务、第二套 Runtime/RAG/Evidence/Eval，也不建设独立 Linux 客户端。没有真实证据的能力宁可保持阻断，不以 Mock、截图、手工数据库操作或放宽阈值换取标签。

## 2. 复用边界与不变量

| 现有正式能力 | Day 10 复用方式 | 禁止做法 |
|---|---|---|
| `UnifiedAgentRuntime`、Research L5 graph、Tool Runtime、Checkpoint/HITL | 补齐正式装配、恢复和 E2E，不创建 release-only loop | 绕过 Runtime 直接调用模型或 Tool 生成演示结果 |
| SEC source/XBRL、Hybrid Retrieval、Calculation、Evidence/Citation | 沿 canonical source version 与正式 locator 完成真实中文链和 Recall/Citation 验收 | 用当前网页摘要、错误 accession、gold Context 或人工结果替代检索 |
| Verifier、Monitor、Case、Approval 与 side-effect ledger | 在同一 API/Worker/Workbench 链验证冲突、拒答、写审批和故障恢复 | 用前端状态模拟服务端完成，或为演示手工改库 |
| Day 6～9 manifests、scorers 与 `release-suite-v1` | 绑定真实 Run/prediction，按 deterministic/offline/live 分报并从 blocker 生成发布决定 | 修改 observation/gold、合并不同 case suite 或以 LLM judge 作为唯一硬门 |
| 现有 Compose、GitHub Actions 与质量脚本 | 扩充真实缺口并复用同一命令；Linux runner 仅用于 CI 可复现性 | 为通过 CI 跳过平台、服务、测试、coverage 或 secret/license 检查 |

始终成立：

- D10 的 `complete` 必须同时满足实现、真实链路、失败/恢复、测试/评测、远端 CI 和 owner acceptance；代码存在不是完成。
- deterministic contract、offline capability、live model 三层报告互不替代；未运行是缺失证据，不是 0 次运行下的成功率 100%。
- 外部凭据、许可、中文人工抽样和 live Provider 属于真实发布门。无法在代码内完成时保持 `NO_GO`，不伪装成技术完成。
- 所有业务结果必须锁定 Workspace、CIK、form、period、accession、`as_of`、source hash、unit、formula 和 Evidence；future leakage、伪造来源、越权写与重复副作用均为 0。

## 3. 五步实施计划

| 步骤 | 能力映射 | 实现范围 | 完成证据 |
|---|---|---|---|
| 1. 发布台账与全矩阵基线 | D10-07 部分 | 建立唯一 release manifest/ledger，双向审计 D1-01～D10-08、全局 DoD、Day 4～9 报告与机器 blocker；每项固定 owner、依赖、命令、artifact、状态和关闭条件 | 生成器/schema/负例；矩阵↔artifact↔CI/owner 可反查，未知/过期/缺 hash/状态矛盾均 fail closed；只关闭有证据的旧债 |
| 2. SEC 工作台与完整中文用户路径 | D10-01、D10-02、D10-04 部分 | 收敛 Filer/Filings、Verification、Evidence/Calculation、Monitor/Case、Eval 路由和统一状态；沿正式 API/Runtime 完成 resolve→核验→引用→审批→新 filing diff | Playwright 使用真实认证、PostgreSQL/Redis/MinIO/ES/Milvus 与受控 SEC source；覆盖刷新、拒答、冲突、forbidden、审批、恢复，无 Mock/人工改库 |
| 3. Release Eval、可观测与安全收口 | D10-04、D10-05、D10-07 部分 | 补齐 common-case A0～A4、ranked retrieval/Recall@5、case→Run/Trace/Evidence/DB final state、公开集 prediction、live≥3 分报；聚合 SEC freshness/leakage/citation/recovery 指标和安全套件 | 可复算 JSON/Markdown、阈值与回退决定；许可/中文抽样签字；跨 Workspace/future leakage/注入/Secret/未授权写/重复副作用均为 0 |
| 4. 完整 CI、真实故障恢复与回滚 | D10-03、D10-06 | 清偿核心 90% coverage；运行 format/type/test/build/migration/OpenAPI/SSE/Gitleaks/Semgrep/audit/license/NOTICE；演练 fresh start、备份恢复、索引重建、Worker/Redis/MinIO/ES/Milvus/SEC 故障与上一镜像 | 核心≥90%、后端≥80%、关键前端≥75%；全量真实依赖和关键 E2E 通过；正式 Scenario 恢复率 100%、重复副作用 0，Runbook 命令可复现 |
| 5. 完整性审计、文档与发布候选 | D10-07、D10-08 | 复核无重复正式链路，处理 D1-09 Provider 凭据、所有外部/owner 门；同步 README/ADR/架构/评测/Runbook/限制/rollback，完成 branch→PR→main CI 与标签前 smoke | D10-01～D10-08 全部 `complete`、机器 blocker=0、链接/格式/diff/secret scan 全绿、owner 签字后才可标记 `RC_READY`；否则保留 `NO_GO` 和明确清单 |

步骤按证据依赖执行。Step 1 先建立机器可审计的真值和优先级；Step 2、Step 3 关闭业务与能力证据；Step 4 证明环境与恢复；Step 5 只消费前四步不可变 artifact 做发布决定，不能在最后一步手工改写成绩或状态。

## 4. 分步验收口径

### Step 1：发布台账

台账必须从矩阵、版本化报告、测试产物和 CI/owner 外部证据生成，至少记录 requirement ID、evidence layer、artifact path/hash、producer command、source commit、执行时间、状态、blocker、owner 与过期规则。旧报告只能作为历史基线；其依赖版本变化后必须标记 stale 并重跑。第一步不会把 D10 或旧债整体升级为 `complete`。

### Step 2：真实中文闭环

唯一用户路径必须从中文问题进入正式公司解析和 `FinancialScope`，选择 accession/`as_of`，经过 XBRL + Filing Hybrid Retrieval、typed calculator、Verifier/one-revise，展示四种业务状态并反查 Evidence/Citation/Calculation。创建 Monitor 必须经过持久审批；模拟新 filing 后只生成一个可恢复 Case。正常、empty/ambiguous、partial/conflict/insufficient、forbidden、cancelled、approval-required、retry 和刷新恢复均由服务端事实驱动。

### Step 3：评测、观测与安全

统一 A0～A4 只能使用相同 case manifest、数据、Scope 和预算。若成本不允许全量策略运行，必须先缩小并版本化 release 子集，保持各策略相同分母，不能把 Day 7/8 两套旧 case 拼接。FinQA/TAT-QA/FinanceBench/FinSearchComp 各用官方或冻结 scorer 单独报告；FinanceBench 权利未获确认时从 release claim 排除但 blocker 保留。live suite 固定 provider/model/tool/prompt/version，每 case 至少 3 次，失败分类为依赖、数据漂移、模型或产品问题。

### Step 4：质量、恢复与回滚

coverage 必须使用冻结模块集合和真实依赖路径，不能通过新增 omission、删除文件或只测简单代码提高。恢复演练覆盖数据库备份、对象/索引重建、migration 往返、队列/Worker 中断、lease/fencing、429/backoff、dead-letter、通知结果未知和上一镜像回退；每次演练保存起点、故障、恢复命令、Run/Case/side-effect 终态和耗时。

### Step 5：发布决定

最终审计从参考能力→矩阵→实现→测试/评测→用户旅程正向检查，也从路由/API/Job/表/Tool/报告反向检查 owner、真实用途和重复链路。只有合并提交 main CI 通过、外部硬门和 owner review 关闭、所有报告 hash 与源码提交一致时，才允许提出 `v0.2.0-sec-disclosure-verifier` 标签候选；Codex 不自动提交、推送或打标签。

## 5. 当日完成定义

Day 10 只有同时满足以下条件才可关闭：

- D10-01～D10-08 以及所有仍非 `complete` 的冻结目标均有适用 DoD 证据并达到 `complete`；
- `sec-release-failure-taxonomy-v1` 的 release blocker 为 0，且没有通过删除 case、降低阈值或改证据层实现；
- 完整中文真实用户路径、正式故障恢复和安全回归通过，Citation 可解析率 100%、Recall@5≥0.80、无答案正确拒答率≥0.90；
- 核心/后端/关键前端覆盖率分别达到 ≥90%/≥80%/≥75%，branch/PR/main CI 与供应链门禁全部通过；
- D1-09、数据/代码/文档权利、中文人工抽样、live 运行和项目所有者验收都有实际记录；
- README、ADR、架构、评测、Runbook、限制、rollback 和 release notes 与不可变 artifact 一致。

任一条件未满足时，本日可以结束实施批次，但版本状态仍为 `NO_GO`，不得把计划完成等同于产品发布完成。

## 6. 复盘题

最终证据能支持哪些明确的产品声明？A0～A4 中哪一级在同一分母上产生了值得成本的净收益？哪些失败属于产品缺陷、外部依赖或治理阻断？在不读取开发者解释的情况下，运维人员能否只凭 Run/Trace/Evidence/报告与 Runbook 完成定位和回滚？
