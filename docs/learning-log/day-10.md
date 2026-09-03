# Day 10 执行计划：SEC 工作台、发布评测与完整交付

> 制定日期：2026-08-30
>
> 计划基线：[Day 1～Day 10 主计划](../master-plan.md) 2.2.15 Day 10
>
> 能力边界：[Day 1～Day 10 目标能力矩阵](../feature-matrix.md) D10-01～D10-08
>
> 权威评测合同：[SEC 披露与财务事实核验 Agent 评测计划](../sec-agent-evaluation.md)
>
> 发布合同：[SEC 披露核验 Agent 发布就绪合同](../release-readiness.md)
>
> 当前状态：Step 1～Step 5 已由 PR #16 合入 `main`；D10-03 为 `complete`，D10-01/D10-02 为 `implemented_pending_verification`，D10-04～D10-08 为 `thin_slice`，最终机器判定仍为 `NO_GO`

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

## 5. Step 1 实现与证据

Step 1 在既有 `industry_platform.modules.evaluation` bounded context 新增唯一 `release_readiness` 生成器，没有建立第二套 Eval Runtime。机器 manifest 固定 Day 9 合并提交 `4500505` 为审计基线，并登记 Day 1～10 的 owner、依赖日、验证命令、30 个文档/代码/测试/报告/CI artifact，以及 9 个 Day 9 taxonomy blocker 和 7 个跨 Day 发布阻断族。每个 artifact 在报告中保存相对路径、byte size 与 SHA-256；manifest、矩阵、生成器、测试和命令入口也进入证据链。

矩阵读取器只接受十张正式能力表，按结构识别 88 个唯一目标，并对完整规范化行计算 digest；它不会把 D1 的历史证据表重复算作目标，也能正确处理 code span 内的 `|`。manifest 固定 requirement count、digest 和全部六种状态计数，因此目标增删、状态漂移、未知状态、重复 ID、表格缺失或 Day 10 target 不是 `complete` 时均 fail closed。

`sec-release-readiness-v1` 在 Step 3 后的当时重算结果为：45 个 `complete`、32 个 `implemented_pending_verification`、8 个 `thin_slice`、3 个 `planned`，共 43 个未完成目标；16 个开放发布阻断族、5 个待外部门，发布判定为 `no_go`/`rc_ready=false`。Day 9 push/PR/main 三层 CI 分别绑定实际 run 与 commit 并记为 `verified`；旧 failure taxonomy 中合并描述的 CI/owner blocker 仍保持 open，但新台账明确当时只剩最终 owner closeout，未改写 Day 9 不可变报告。

8 条聚焦测试覆盖 checked report/Markdown/schema 重算、全部 artifact hash、矩阵状态漂移、artifact 缺失、taxonomy 双向映射、仍 release-blocking 的 taxonomy 项伪关闭、无证据 external gate 和非法 hash。`pnpm run eval:release-readiness` 先规范化输入 manifest，再生成 JSON/Markdown 与 manifest/report 两份 JSON Schema，避免生成后格式化输入造成 hash 立即过期。

本地门禁为 evaluation 模块 `65 passed`、readiness 模块 branch coverage `84%`、无强制真实服务全量 pytest `1184 passed, 88 skipped`；Ruff format/check、mypy `513` 个源文件、Prettier、ESLint、TypeScript、Vitest `89 passed`、生产构建和 OpenAPI 确定性均通过。Python/Node audit 无已知漏洞，完整 98-commit Gitleaks 无泄漏。PostgreSQL/Redis/MinIO/Milvus/Elasticsearch 强制集成、Chromium、分支/PR/main CI 与 owner review 未在本步执行。该结果只证明发布台账合同和当前 `NO_GO` 基线可重算；它没有关闭任何业务、评测、恢复或外部 blocker，所以只有 D10-07 更新为 `thin_slice`。

## 6. Step 2 实现与证据

Step 2 没有复制 Research 或 Verifier。SEC Workbench 在用户选定并锁定正式 Filing import 后，构造 typed `SecReviewDraft`，一次性交付 CIK、form、report period、accession、`as_of`、Knowledge Base、unit/scale 和中文问题。Research Workbench 以该草稿预填既有 `StartResearchRequest.financial_scope`、明确的 scope/exclusion/completion criteria，并继续使用唯一 Research graph、Unified Runtime、Tool profile、Checkpoint/Approval 和 Monitor subscription 服务。`10-K/A`/`10-Q/A` 不会静默降为原表单；正式核验入口 fail closed，修订关系仍由 Filing Diff 处理。

Research Web API 新增已有 `GET /research-runs/{id}/verification-report` 的 generated-client wrapper。工作台从服务端报告显示 `verified`、`partial`、`conflict`、`insufficient_evidence` 四态、coverage、Claim verdict、Citation/Calculation 分母、typed issue、Runtime stop reason 和 Evidence snapshot；404 只表示报告尚未生成，其他读取失败仍显示为错误。active/paused Run 每 3 秒从正式列表、Run、Trace、Claim、Durability 和 Verification API 重建；Case 的 baseline/target Evidence 可直接进入 Evidence Workbench。页面不会按 Claim 数或 Draft 文本自行推导核验状态。

当前 readiness manifest 新增 11 个正式实现/测试 artifact，总数为 41；非金融 Run 不请求金融 Verification Report，聚焦 Web 组件/API 为 `15 passed`，全量 Web 为 `94 passed`，Research/Verifier/Monitor/readiness 聚焦 Python 为 `37 passed`，TypeScript 和 ESLint 通过。尚未执行无接口拦截的真实认证 + PostgreSQL/Redis/MinIO/Elasticsearch/Milvus 浏览器旅程，也未运行受控 SEC source、Worker 恢复、分支/PR/main CI 或 owner review。因此 D10-01/D10-02 仅为 `implemented_pending_verification`，D10-04 仅为 `thin_slice`；Day 5/8 浏览器与恢复 blocker、16 个总 blocker 和 `NO_GO` 均保留。

## 7. Step 3 实现与证据

Step 3 沿既有 `evaluation` bounded context 新增 checked `release_evidence` scorer，没有创建 release-only Agent loop。manifest 直接引用 `sec-tool-v1` 的 10 个 case ID 与 canonical hash，保留原 gold identity/Evidence/program 和共享预算，只补齐 A3 verifier 与 A4 monitor 策略合同。offline 分母固定为 10×5=`50`，live 每格至少 3 次，分母为 `150`；缺任一 case/strategy/repetition 或重复 key 都拒绝评分。

生产 Run observation 必须保存 Run/Trace/Workspace、Evidence/Calculation IDs、final-state hash、ranked candidates、Citation、Tool、Token/成本/延迟和 future source、跨 Workspace、注入、未授权写、重复副作用、恢复字段。scorer 从这些事实重算 case accuracy、Recall@5、Citation、正确拒答、runtime binding、security/recovery 指标与告警状态。7 条聚焦测试用合成完整/越权数据验证 50 格覆盖、Recall/绑定公式和 critical alert；合成数据不进入 checked report，也不被描述为生产能力。

当前 checked observation 显式为 `not_executed`：实际 Run 为 0/50，11 个指标均为 `not_measured`，11 个告警均为 `unknown`，全局 A0～A4 不可比且 production default 为 null。`release-suite-v1` 已消费该报告并将 `global-a0-a4-common-cases-missing` 精确替换为 `global-a0-a4-common-runs-missing`；blocker 总数仍为 9，readiness 总 blocker 仍为 16。新增 8 个代码/测试/manifest/observation/report/schema/suite artifact 后总数为 49，当前状态为 45 complete、32 implemented pending verification、8 thin slice、3 planned。聚焦 release evidence/suite/readiness 为 `22 passed`，evaluation 全集为 `72 passed`，后端全量为 `1191 passed, 88 skipped`，Web 为 `94 passed`；Ruff、mypy、Prettier、ESLint、TypeScript、build 与 OpenAPI 确定性检查通过。真实 Runtime、公开集 prediction、live≥3、合法 SEC/provider、许可、中文签字、远端 CI 与 owner review 均未执行，因此 D10-04/D10-05/D10-07 仅为 `thin_slice`。

## 8. Step 4 实现与证据

Step 4 没有更改 coverage omission 或删除生产文件。Evidence domain 与 Research service 增加 source/locator/hash、filing/XBRL/calculation、checkpoint/approval/revision/cross-run 等 fail-closed 边界回归，冻结核心 include 集合达到 90%；CI 将同一集合的 `--fail-under` 从 85 提升到 90，后端总体 80% 与关键 Web 75% 保持不变。本机 `.env` 未声明 Milvus/Elasticsearch 映射端口，最终验证命令显式使用 Compose 的 `19530`/`19200` endpoint，在 PostgreSQL、Redis、MinIO、Milvus、Elasticsearch 五个真实依赖强制开启且无 skip 的条件下，后端 `1345 passed`，总体 branch coverage 为 `80.76%`，同一冻结核心集合为 `90%`。

供应链门新增锁定 `semgrep==1.175.0`、`pip-licenses==5.5.5` 与 `license-checker-rseidelsohn==5.0.1`。Semgrep 以六条 repository rule 严格扫描生产 Python/Web 源码；其当前 parser 无法解析 `core/config.py` 的合法 Python 3.13 type alias，因此该文件被显式排除并继续由 Ruff、strict mypy 和配置测试覆盖。Python/Node license gate 拒绝未知及未允许的 GPL/AGPL metadata，NOTICE 记录单列 build-only 手工澄清、ECharts 和 MinIO 义务；工具通过不关闭 owner license review。

新 `sec-release-recovery-v1` 冻结 12 个恢复/回滚场景。执行 observation 必须全覆盖并保存 environment/commit、exercise/evidence SHA-256、时间/耗时、恢复命令与终态 hash、适用的 Run/Workspace，以及重复副作用、数据损失和越权写计数；缺文件、hash 漂移、覆盖不全或伪造未执行身份均 fail closed。当前 checked input 明确为 `not_executed`，报告为 0/12，四个指标均 `not_measured`、告警均 `unknown`、恢复门为 false。Runbook 只提供 disposable/staging 演练顺序，不执行破坏性 volume 清理，也不把命令或合成单测冒充 actual exercise。

Step 4 当时的 readiness 登记 63 个 hash artifact，状态为 45 complete、33 implemented pending verification、9 thin slice、1 planned；43 个未完成目标、16 个 open blocker、5 个 pending external gate 与 `NO_GO` 不变。聚焦 recovery/readiness/Evidence/Research 为 `101 passed`；Ruff format/check、strict mypy（517 个源文件）、wheel/sdist、fresh migration、OpenAPI 确定性、Prettier、ESLint、TypeScript 和生产 build 均通过。Web 为 `94 passed`，关键状态覆盖率四项均为 `100%`，现有 Chromium 套件为 `8 passed`；该浏览器套件包含 API replay/interception，不能代替 D10-02 所需的无拦截 SEC 完整产品旅程。Semgrep 严格扫描 356 个 target/0 finding，Python/Node license allowlist、Python/Node dependency audit 通过；Gitleaks 对 101 个可达提交、Git diff 和全部非忽略未跟踪文件未发现 Secret，本机被 Git 忽略的 `.env` 含运行凭据且未计入提交扫描。D10-03 当时为 `implemented_pending_verification`、D10-06 为 `thin_slice`，远端 branch/main CI、12 场景实际观测与上一镜像 artifact 尚缺。

## 9. Step 5 实现与最终审计

Step 5 只消费前四步 artifact 并继续使用唯一 `sec-release-readiness-v1` 判定，没有新增 release-only scorer、状态机或手工结论。README、产品范围、ADR 0007、架构、SEC 评测、恢复/回滚、第三方 NOTICE 与候选说明草案当时同步到主计划 2.2.13；README、产品范围、ADR、评测和候选草案新增进入 readiness hash 台账。候选说明固定为 `NO_GO`，明确不得创建 tag/镜像或对外宣称 live/model/公开 benchmark/正式恢复能力。

文档门使用 Markdown AST 解析受审计文档的本地 link/image destination，并拒绝越出仓库或不存在的目标；readiness 仍拒绝 artifact 缺失、hash 漂移、矩阵状态冲突、taxonomy blocker 伪关闭和无证据 verified gate。D10-08 从 `planned` 推进到 `thin_slice` 后，Step 5 当时状态为 45 complete、33 implemented pending verification、10 thin slice、0 planned，未完成目标为 43。新增文档 artifact 后台账为 68 项；16 个 blocker、5 个 external gate 和 `release_decision=no_go` 不变。

本步聚焦 readiness/recovery 为 `17 passed`；全量无服务后端为 `1259 passed, 88 skipped`。Ruff format/check、strict mypy 517 个源文件、wheel/sdist、Prettier、ESLint、TypeScript、OpenAPI、Web `94 passed`、关键状态覆盖率 `100%` 和生产 build 通过；Semgrep 为 356 个 target/0 finding，Python/Node dependency audit 与 license/NOTICE 门通过。Gitleaks 对 102 个可达提交、Git diff 和新增候选说明均未发现 Secret。本机 Docker Desktop 在本步验证时未运行，因此没有重跑真实依赖和 Chromium；Step 4 提交前的 `1345 passed` 五依赖与 Chromium `8 passed` 是前一证据层，不冒充本步重跑或远端 CI。

本步当时没有执行外部 Provider 凭据处置、公开/live 模型运行、中文人工签字、12 场景 staging 恢复、上一镜像回滚或 Day 10 远端 CI，也没有项目所有者最终验收。因此 D10-07/D10-08 均为 `thin_slice`，`v0.2.0-sec-disclosure-verifier` 仍只是禁止提升的候选说明草案。实现代理可以提交本步代码与文档供 branch/PR/main CI 验证，但不能自动 commit、push、merge 或打标签。

### 9.1 合并、三层 CI 与覆盖率债务关闭

2026-09-01，[PR #16](https://github.com/hrw991009/industry-intelligence-platform/pull/16) 将 Day 10 合入 `main`。功能 head [`e1a6dcc`](https://github.com/hrw991009/industry-intelligence-platform/commit/e1a6dcc930dd1be7522330ec08b1e847e53ea82b) 的 [push CI 33459436380](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/33459436380) 与 [PR CI 33461560633](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/33461560633)，以及合并提交 [`778a196`](https://github.com/hrw991009/industry-intelligence-platform/commit/778a1966a5fd42df6b47d4a4002cb47e67435ac4) 的 [main CI 33463386752](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/33463386752) 均通过 Browser E2E、Python quality、PostgreSQL integration、Web quality、Python/Node dependency audit 和 Secret history 共 7 个适用 Job。早先失败的 Day 10 run 已由最终 head 修复并被上述成功证据取代，没有从历史中删除。

main PostgreSQL integration Job 在 PostgreSQL、Redis、MinIO、Milvus 与 Elasticsearch 五个真实依赖下完成 `1347 passed`；总体 branch coverage 为 `80.72%`，冻结核心模块集合为 `90%`。这满足 D10-03 的干净 feature head/PR/main CI、供应链与覆盖率验收，因此 D10-03 升为 `complete`，`day4-core-coverage-debt` 关闭。readiness 当前为 46 complete、32 implemented pending verification、10 thin slice、0 planned；42 个未完成目标、15 个 open blocker、5 个 pending external gate，仍为 `no_go`。

该 CI 证据不越层：Browser E2E 仍是现有 interception 套件，不能关闭 D10-02 要求的无拦截中文完整链；生产 Runtime observation 仍为 0/50，正式恢复仍为 0/12，外部凭据/权利/中文签字和最终 owner acceptance 仍待完成。因此本次只关闭工程质量与核心覆盖率债务，不提升候选发布状态。

### 9.2 无拦截中文浏览器闭环实现

2026-09-01 当前分支新增独立 `sec-real-journey` Playwright project，正式 spec 不调用 `page.route` 或 `route.fulfill`。受控来源 manifest 对两期 Apple 10-K 衍生摘录、submissions 和 companyfacts 逐文件固定 SHA-256，并按 `source_available_at` 选择 2023/2024 快照；它只允许在 `APP_ENVIRONMENT=test` 使用，不是 live SEC 原文或生产来源。受控 OpenAI-compatible Provider 通过真实 loopback HTTP 返回严格结构化 search、monitor subscription 和 final 三阶段决策，生产 HTTPS/受控 DNS 约束保持不变。

运行器先迁移正式 PostgreSQL，再启动 Provider、Outbox Dispatcher 和 Celery Worker；Playwright 启动正式 API/Web，仅通过 UI 和认证 API 完成 Knowledge Base → 2023 Filing import → 中文 Research → Monitor 人工审批与同 Run resume → Verification/Evidence → 2024 Filing import → Monitor 立即检查 → verified Case。Monitor 初始 watermark 绑定审批前实际审阅且在 `as_of` 可见的 accession，不再从空水位扫描全部历史 filing；“立即检查”复用 Schedule occurrence/Job/Outbox，不在 API 线程直接执行分析。CI Browser Job 新增 Milvus/Elasticsearch，与 PostgreSQL、Redis、MinIO 共五个真实依赖，并始终上传 Provider 计数、无拦截标记、API 请求清单和桌面/移动截图。

当前无外部服务聚焦回归为 `141 passed`，默认后端全量为 `1278 passed, 89 skipped`，全仓 mypy 为 530 个源文件通过，Web TypeScript 通过；本机 Docker Desktop 未运行，Redis/MinIO/Milvus/Elasticsearch 不可达，因此没有在本机执行五依赖旅程，PostgreSQL 强制夹具也因本机数据库连接不可用而中止。默认全量中的 skip 不能替代强制外部依赖验收，必须以本分支后续 Browser CI 的实际成功 artifact 才能关闭该证据层；当前只表示实现已接通，D10-02 仍为 `implemented_pending_verification`。该旅程也不计入生产 Run 0/50、正式恢复 0/12、live SEC/model 或公开 benchmark，`NO_GO` 不变。

### 9.3 表格 Citation、运行采集与验收执行器收口

后续技术债收口没有更改冻结 gold、checked observation 或既有分母。SEC HTML 入库新增稳定
table/cell 坐标、span 与内容 hash marker，`sec_filing_text_v1` Evidence locator 保存命中
chunk 的结构化坐标；Workspace-scoped Citation resolver 会反查当前 chunk 并核对坐标、span
和 hash，解决原来 HTML table 被纯文本扁平化后无法可靠定位单元格的问题。该范围不扩张到
PDF/OCR/跨页复杂表格。

新增 production `release_execution` 与 `release_observation_collector`。执行器以同一受控中文
10-case 分母通过正式 submission/Worker/Runtime 自动产生 A0～A4 的 50 Run，live 模式为每格
3 次、共 150 Run；collector 从最终 Message/Draft、Run/Trace/Event、每次 Tool Observation
原始 source 顺序、Evidence/Calculation、Scope/Workspace 和数据库终态确定性生成 answer/gold
映射、ranked candidates、Citation 与 runtime binding。Citation 只统计最终 Draft 引用且反查
当前 URL、source/content hash、accession、as_of、Workspace 与 table cell 坐标。checked report
仍保持 0/50，实际动态结果只写 Git 忽略目录。

新增 12 场景 `release_recovery_executor` 与固定 exercise registry。计划由实际 collection 的
Run/Workspace 自动生成，使用 `disposable:*`、唯一 state 目录、无 shell argv 和前后严格 JSON
探针；禁止通配删除、volume/prune、`git clean/reset`、Secret 参数及原始 DROP/TRUNCATE。恢复
探针比较业务内容 hash/计数，并从 PostgreSQL 查询重复副作用与未授权写；stdout/stderr 对
Secret、Bearer 与数据库密码脱敏。pytest 是相应隔离演练中的固定验证步骤，不能脱离故障注入
冒充 outage/rollback 成功；checked recovery report 仍保持 0/12。

`pnpm run acceptance:sec` 先自动启动并等待五个 Compose 依赖，再串联 migration/受控入库、
无 interception 中文浏览器链、50 个 production Run、自动 collector/scorer、12 场景 recovery
executor/scorer 与 live SEC one-shot；`pnpm run acceptance:sec:live` 将相同矩阵扩为 150 Run。
所有动态结果只写 Git 忽略目录。实际 Run、隔离演练、截图人工查看、中文/权利/CI/owner 签字
仍未在本段执行，因此 readiness 继续 `NO_GO`，不得因执行器存在而关闭运行证据 blocker。

## 10. 当日完成定义

Day 10 只有同时满足以下条件才可关闭：

- D10-01～D10-08 以及所有仍非 `complete` 的冻结目标均有适用 DoD 证据并达到 `complete`；
- `sec-release-failure-taxonomy-v1` 的 release blocker 为 0，且没有通过删除 case、降低阈值或改证据层实现；
- 完整中文真实用户路径、正式故障恢复和安全回归通过，Citation 可解析率 100%、Recall@5≥0.80、无答案正确拒答率≥0.90；
- 核心/后端/关键前端覆盖率分别达到 ≥90%/≥80%/≥75%，branch/PR/main CI 与供应链门禁全部通过；
- D1-09、数据/代码/文档权利、中文人工抽样、live 运行和项目所有者验收都有实际记录；
- README、ADR、架构、评测、Runbook、限制、rollback 和 release notes 与不可变 artifact 一致。

任一条件未满足时，本日可以结束实施批次，但版本状态仍为 `NO_GO`，不得把计划完成等同于产品发布完成。

## 11. 复盘题

最终证据能支持哪些明确的产品声明？A0～A4 中哪一级在同一分母上产生了值得成本的净收益？哪些失败属于产品缺陷、外部依赖或治理阻断？在不读取开发者解释的情况下，运维人员能否只凭 Run/Trace/Evidence/报告与 Runbook 完成定位和回滚？
