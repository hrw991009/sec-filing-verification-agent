# Day 9 执行计划：公开 Benchmark、SEC Temporal Eval 与中文验证链

> 制定日期：2026-08-29
>
> 计划基线：[Day 1～Day 10 主计划](../master-plan.md) 2.2.3 Day 9
>
> 能力边界：[Day 1～Day 10 目标能力矩阵](../feature-matrix.md) D9-01～D9-08
>
> 权威评测合同：[SEC 披露与财务事实核验 Agent 评测计划](../sec-agent-evaluation.md)
>
> 当前状态：Step 1 已在 `feat/day-9` 工作树实现，D9-01 为 `implemented_pending_verification`；D9-02～D9-08 保持 `planned`

## 1. 进入基线与本日边界

Day 8 已由 [PR #14](https://github.com/hrw991009/industry-intelligence-platform/pull/14) 合入 `main`，功能 head 为 `227eeb4`，合并提交为 `ae1f50b`。push/PR CI `33247902191`、`33247948314` 和合并提交 CI `33248397107` 的 7 个适用 Job 均通过。该证据证明 Day 8 代码已合并并通过仓库工程门禁，但 `sec-verification-v1` 仍是 frozen replay，不是 live SEC/model 质量结果；Day 4～8 的发布债务继续留在 Day 10。

Day 9 只建设可审计评测链：固定公开 benchmark、构造 SEC point-in-time release set、建立中英事实链对照，并在相同数据/Scope/预算下比较 A0～A4。它不训练模型，不把 benchmark 数据接入生产 RAG，不把公开 gold 放入产品 Context，也不将动态 LLM judge 变成 PR 硬门。

## 2. 复用边界与不变量

| 现有正式能力 | Day 9 复用方式 | 禁止做法 |
|---|---|---|
| `UnifiedAgentRuntime`、Harness、Trace、Evidence、Calculation、Verifier | Eval 只替换冻结输入/外部 Adapter，并通过正式 Run/Trace/Evidence identity 评分 | 新建 benchmark 专用 Agent loop，或直接评分模型自报标签 |
| Day 6～8 的 `sec-source-v1`、`sec-tool-v1`、`sec-verification-v1` | 作为 deterministic contract 层保留原分母；release manifest 只引用，不改写旧 observation | 用 Day 9 新题覆盖旧失败、改分母或回填高分 |
| `sec-l4-v1`/`sec-l5-v1`、`financial-context-v1`、A0～A4 | 每个 run 固定 runtime/harness/model/prompt/tool/context/retrieval/scorer 与预算 | 给不同策略不同 gold、额外 Evidence 或更宽 Scope |
| Git 管理的小型 manifest/report、对象存储的大数据产物 | Git 保存 registry、schema、checksum、dataset card 和小型派生报告；大 payload 本地下载并校验 | 未复核许可就提交原始 benchmark/PDF，或从浮动 `main` 下载 |

所有证据必须分为 `deterministic_contract`、`offline_capability`、`live_model`。三层不能平均成一个总准确率；没有 artifact checksum、source revision、split、许可记录或可复算 scorer 的运行不得进入 release claim。

## 3. 五步实施顺序

| Step | 能力与矩阵 | 实现范围 | 本步完成证据 |
|---|---|---|---|
| 1. Release Eval 治理合同 | D9-01 | 在正式 `evaluation` bounded context 定义严格 Dataset Registry、DatasetCard、Release `EvalCase/Manifest`、Runtime/Strategy/Budget/Trajectory/SEC gold；固定四个外部数据源 revision 与 artifact SHA-256，生成版本化 JSON Schema | 重复 key/NaN、浮动 revision、hash/byte size、许可/商业/再分发、registered-only、split/文档泄漏、future source、配置缺失和 case→run→trace→Evidence 引用的负向测试；不下载数据、不产生成绩 |
| 2. FinQA 与 TAT-QA 固定上下文 Adapter | D9-02、D9-03 | 按 registry 下载并校验固定 artifacts；转换为统一 case，但分别实现官方 supporting facts/program/execution 与 answer EM/F1/scale/derivation scorer | 官方 split 数量与 checksum、转换确定性、官方 scorer 对照、错误 program/scale/source 负例；两个 benchmark 分报 |
| 3. `sec-temporal-v1` 与中英事实链 | D9-05、D9-06 | 至少 60 个 point-in-time release cases，按 accession/filing 隔离 split；至少 30 组中英问题共享 gold Scope/Evidence/program/result/status | cutoff/future leakage、amendment/custom tag/footnote/conflict/no-answer、pair identity 100% 和人工语言抽样清单 |
| 4. 受限外部补充与 Agent/security | D9-04、D9-07 | FinanceBench 仅非商用内部补充且不提交受限 payload/PDF；FinSearchComp historical/AkShare 与 dynamic live 分报；按 BFCL/ToolSandbox/tau-bench/AgentDojo 方法补 trajectory/final-state/injection | 许可阻断、动态/专业数据库依赖、judge 漂移、未授权写、跨 Workspace、重复副作用与 `pass^k` 报告 |
| 5. A0～A4 release 消融与收口 | D9-08 | 在同 manifest/data/Scope/budget 上运行分层 scorer，输出 retrieval、answer/program、Evidence/Citation、trajectory、recovery、point-in-time、security、Token、成本和延迟 | deterministic/offline/live 三套 JSON+Markdown；A2/A3 保留或回退决定；branch/PR/main CI 与 owner review 台账 |

步骤按治理合同、固定数据 Adapter、产品专项数据、受限/动态数据、综合决策的依赖顺序执行。任何后续 Adapter 都必须消费 Step 1 registry 与 schema；不得各自解析许可、版本和 hash。

## 4. Step 1 冻结口径

Step 1 的 `DatasetRecord` 必须区分数据许可与代码许可，并记录 source revision、artifact path/URL、byte size、SHA-256、split、gold 可见性、允许证据层、商业使用、再分发和独立文档权利。`registered_only` 只表示元数据与 checksum 已固定；在 Adapter、转换测试和本地 artifact 校验完成前必须 `release_eligible=false`。

Release `EvalCase` 必须同时固定 dataset/split/document group、输入语言与问题、运行/模型/Prompt/Tool/Context/Graph/Verifier/Scorer 版本、Budget、required/allowed/forbidden action、partial order、预期 stop reason 和可选 SEC gold。SEC case 额外要求 CIK、forms、report period、`as_of`、accession、source availability、Evidence locator、Calculation program/result/unit/tolerance/rounding 和业务核验状态；任一 source 在 `as_of` 后才可见时拒绝加载。

本步只提交 registry、schema 和合同测试，不提交 FinQA/TAT-QA/FinanceBench/FinSearchComp 原始数据，不声称任何 offline/live 分数，也不把 schema 存在写成 D9-01 `complete`。在分支、PR、合并提交 CI、case→真实 Run/Trace/Evidence 反查和 owner review 完成前，D9-01 最多为 `implemented_pending_verification`。

### Step 1 实现记录

当前工作树在正式 `industry_platform.modules.evaluation` 中新增冻结 Pydantic 合同及唯一 CLI 生成入口；`evals/registry/sec-agent-datasets-v1.json` 登记 FinQA、TAT-QA、FinanceBench 和 FinSearchComp 的精确 upstream revision、11 个 artifact 的 byte size/SHA-256、数据/代码许可和允许用途，`evals/manifests/sec-agent-release-v1.json` 通过 registry canonical hash 绑定该快照。两个版本化 JSON Schema 由同一模型确定生成，后续 Adapter 必须消费这些合同。

加载器拒绝重复 JSON key、NaN/Infinity、浮动 revision、未固定 URL、gold 进入模型 Context、许可权利升级、不安全路径和非法 release-ready 状态；Release manifest 进一步约束 document group 不跨 split、case/artifact split 一致、完整 runtime/tool/context/scorer version、Budget、trajectory partial order、point-in-time source、case→run/trace/Evidence/Calculation identity 与 registry 引用。聚焦测试为 19 passed，模块 branch coverage 为 86.18%；Ruff、mypy、Web quality、现有 Chromium `8 passed`、依赖审计、完整历史 Gitleaks 和真实 PostgreSQL/Redis/MinIO/Milvus/Elasticsearch `1226 passed` 均通过，总体/既有核心 coverage 为 80.29%/86%。四个数据集仍全部是 `registered_only`/`release_eligible=false`，没有下载 payload、执行 Adapter、真实 Run 反查或 benchmark 得分；远端 branch/PR/main CI 与 owner review 也尚未发生。

## 5. 完成定义

Day 9 只有同时满足以下条件才可关闭：

- D9-01～D9-08 均达到矩阵 `complete`，四个外部 benchmark 有固定 revision/hash、dataset card、转换/scorer 测试和许可边界；
- `sec-temporal-v1` 至少 60 case，中英配对至少 30 组，按 accession/document group 隔离 split 且 future leakage 为 0；
- 公开 benchmark 使用各自官方指标单独报告，产品门禁与 A0～A4 报告不伪装官方 leaderboard；
- deterministic/offline/live 分报，live 固定 provider/model/tool/prompt 且每 case 至少重复 3 次；
- branch、PR、合并提交 CI 和 owner review 通过，Day 4～8 遗留债务仍在 Day 10 台账中可见。
