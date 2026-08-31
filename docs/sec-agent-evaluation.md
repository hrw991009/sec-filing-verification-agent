# SEC 披露与财务事实核验 Agent 评测计划

> 计划编号：`IIP-EVAL-SEC-001`
>
> 版本：`1.5.7`
>
> 日期：2026-08-26
>
> 修订日期：2026-08-31
>
> 权威范围：`docs/master-plan.md` v2.2.10 Day 5 Step 4～Day 10
>
> 状态：Day 6 `sec-source-v1` 报告仍为 22/24；Day 7 `sec-tool-v1` 与 Day 8 `sec-verification-v1` deterministic contract 已合入 `main`。Day 9 Step 1～Step 5 已由 PR #15 合入 `main`，push/PR/main 三层 CI 均通过；`release-suite-v1` 的统一 A0～A4、公开集 prediction、live≥3 次、真实 Agent/Trace/Evidence、许可/中文/owner 复核仍缺。Day 10 Step 1～2 已建立 readiness 台账并连接 SEC→Research→Verification→Evidence→Monitor/Case 产品路径；88 个目标仍有 43 个未完成，16 个 blocker open、5 个 external gate pending，结论为 `no_go`。D10-01/D10-02 为 `implemented_pending_verification`，D10-04/D10-07 为 `thin_slice`

Day 7 的五步执行顺序、`hybrid-v1`、SEC locator、Financial Context、Calculation/reconciliation 和 A0/A1/A2 具体边界见 [Day 7 执行计划](learning-log/day-7.md) 与 [SEC Filing Retrieval 与财务计算设计](sec-retrieval-design.md)。

Day 8 的 Claim Verifier、四种业务状态、one-revise、Monitor/HITL/恢复及 A2/A3/A4 边界见 [Day 8 执行计划](learning-log/day-8.md) 与 [SEC Verifier、Monitor 与恢复设计](sec-verification-monitor-design.md)。

Day 9 的公开集、SEC temporal、中英配对、Agent/security 与分层 release suite 见 [Day 9 执行计划](learning-log/day-9.md)。Day 10 的 common-case、Runtime binding、真实用户链和发布判定见 [Day 10 执行计划](learning-log/day-10.md) 与 [发布就绪合同](release-readiness.md)。

## 1. 目标与不能证明的能力

本计划回答六个独立问题：

1. 系统是否锁定了正确公司、CIK、form、报告期间、`as_of` 和 accession。
2. Retriever 是否找到正确 XBRL fact、filing section、table 或 text span。
3. Calculator 是否使用正确输入、公式、unit/scale、rounding 和 period 得到正确结果。
4. 最终 Claim 是否由可解析 Evidence 支持，冲突和无答案是否被正确处理。
5. Agent 是否选择了允许的 Tool、满足必要里程碑、遵守预算并正确停止。
6. Worker/Provider/SEC/索引故障、审批和重复 resume 后，状态与副作用是否仍正确。

本计划可以支持“SEC 公开披露事实核验 Agent”的受限能力声明。它不能证明：

- 投资建议、股票预测、估值、目标价或交易能力；
- 审计、法律或税务结论；
- 所有 SEC form、所有 taxonomy/custom tag 和所有 filing 附件均已覆盖；
- 中文开放式金融分析已经达到金融专家水平；
- 一次 live 演示、一个 benchmark 高分或一个 LLM judge 分数代表生产可靠性。

## 2. 三类运行证据必须分报

| 证据层 | 运行方式 | 能证明什么 | 不能证明什么 |
|---|---|---|---|
| Deterministic contract | Fake/frozen Provider、冻结 SEC responses、固定数据库与同一 Runtime | typed contract、trajectory milestones、公式、错误语义、恢复和安全不变量 | 当前 live SEC、当前模型质量 |
| Offline capability | 固定公开 benchmark 和 `sec-temporal-v1` | 数值/表格推理、检索、证据、point-in-time 和领域错误分类 | 实时数据新鲜度、线上随机性 |
| Live/model | 固定 provider/model/tool 版本，真实 SEC read tools，至少重复 3 次 | 当前模型与实时工具组合的能力、成本、延迟和波动 | 可确定重放、未来版本持续有效 |

三类报告不得合并成一个“总准确率”。PR CI 只运行 deterministic quick suite；offline release suite 在受控任务运行；live suite 定时或发布前运行，不因外部网络波动阻塞普通代码 PR。

## 3. Dataset registry

### 3.1 FinQA

- 官方规模：8,281 个 QA、2,789 份财务报告。
- Gold：supporting facts、可执行 reasoning program、answer。
- 官方指标：execution accuracy 与 program accuracy。
- 许可：数据站声明 CC BY 4.0；官方代码仓库 MIT。接入时分别保存 dataset 与 code 的许可记录。
- 用途：固定上下文下的财务数值推理、公式与 supporting facts。
- 缺口：不测开放 EDGAR 检索、当前/修订 filing、Citation 可解析、真实 Tool trajectory 或 point-in-time。
- 来源：[数据站](https://finqasite.github.io/)、[论文](https://aclanthology.org/2021.emnlp-main.300/)、[仓库](https://github.com/czyssrs/FinQA)。

### 3.2 TAT-QA

- 官方规模：16,552 个问题、2,757 个 table-text hybrid contexts。
- Gold：answer、scale、derivation/evidence mapping。
- 官方指标：exact match 与 numeracy-aware F1。
- 许可：数据说明 CC BY 4.0；仓库代码 MIT。
- 用途：表格+文本、unit/scale、抽取与算术。
- 缺口：上下文已经选定，不测开放检索、EDGAR API、accession 选择或 Agent 状态。
- 来源：[论文](https://aclanthology.org/2021.acl-long.254/)、[仓库](https://github.com/NExTplusplus/TAT-QA)。

### 3.3 FinanceBench

- 论文数据规模为 10,231 题，但公开数据只有 150 题；文档和报告不得把 10,231 写成公开可运行规模。
- 公开 Gold：human answer、justification、evidence text、document/page。
- 用途：小型 filing RAG、证据页和拒答补充。
- 许可：官方 Hugging Face card 标 CC BY-NC 4.0；商业使用、再分发和 source PDF 权利必须单独复核。
- 缺口：公开集小、gold 全公开、无正式 train/dev/test，且不测 EDGAR API、amendment、point-in-time 或 Tool trajectory。
- 来源：[论文](https://arxiv.org/abs/2311.11944)、[仓库](https://github.com/patronus-ai/financebench)、[数据卡](https://huggingface.co/datasets/PatronusAI/financebench)。

### 3.4 FinSearchComp

- 官方规模：635 题，由 70 位金融专业人士参与构建，覆盖 Global 与 Greater China。
- 任务：Time-Sensitive Data Fetching、Simple Historical Lookup、Complex Historical Investigation。
- 许可：公开数据声明 CC BY 4.0；接入时 pin dataset commit/hash。
- 用途：最接近 live 多步金融搜索和调查的补充评测。
- 缺口：动态题会随日期/收盘状态漂移，部分 gold 依赖专业数据库，评判包含 LLM judge；不能成为固定 CI 或唯一发布硬门。
- 策略：可重放 historical/公开工具子集进入 offline suite；动态 T1 进入 live suite并记录日期、时区、source/tool/model version。
- 来源：[ICLR 2026](https://proceedings.iclr.cc/paper_files/paper/2026/hash/4d42358702dff82e1436550a05ade260-Abstract-Conference.html)、[仓库](https://github.com/randomtutu/FinSearchComp)。

### 3.5 通用 Agent 与安全参考

| Benchmark | 使用范围 | 不得声称 |
|---|---|---|
| [BFCL](https://gorilla.cs.berkeley.edu/leaderboard) | function schema、参数、non-call/relevance、多轮状态的固定非 live 类别 | 财务事实正确 |
| [ToolSandbox](https://machinelearning.apple.com/research/toolsandbox-stateful-conversational-llm-benchmark) | milestone dependency、world state、澄清与状态前置条件方法 | SEC/财务领域能力 |
| [tau-bench](https://github.com/sierra-research/tau-bench) | policy、最终数据库状态、重复运行 `pass^k` 方法 | 财务检索或计算 |
| [AgentDojo](https://github.com/ethz-spylab/agentdojo) | 间接 Prompt Injection、benign utility、attack success 方法 | 财务答案正确 |

接入任何代码前必须 pin 版本并复核许可证：BFCL/tau-bench/AgentDojo 的仓库许可与 ToolSandbox 的 Apple sample-code license 不能混写成同一许可。

## 4. 自建固定数据集

### 4.1 `sec-fixture-v1`

Day 5 使用少量已审核 SEC filing 快照验证 Knowledge、locator、calculator 和 L4 合同。它不访问 live SEC，也不作为模型能力报告。

最低覆盖：

- 直接事实；
- 一步和两步计算；
- 错误 unit/scale；
- no result/not ready；
- filing snapshot/version；
- hard stop、resume 和重复 decision。

### 4.2 `sec-source-v1`

Day 6 验证官方数据和 point-in-time source contract。最低规模为 24 个确定性 case：18 个 `contract` cases 用于开发期回归，6 个 `closeout_regression` cases 在步骤收口时复跑。两组 case/gold 均对开发可见，只保证 gold 不进入模型 Context，不支持“未见数据泛化”声明。它不是公开 benchmark，也不评价开放式投资判断。

每个 case manifest 至少固定：

```text
case_id / dataset_version / split / execution_kind=tool|sync
optional sync_kind=canonical_source|workspace_import
license_or_use_record / checksum / eligible_metrics
expected_snapshot_presence / expected_import_presence
optional frozen_source_refs / source_snapshot_hashes / source_version_available_at
optional bulk_published_at / coverage_through / incremental_coverage_refs
visibility_basis / source_version_valid_from / source_version_valid_to
adapter_version
question_or_operation / cik_candidates / expected_cik
allowed_forms / report_period / as_of / timezone
visibility_policy_version / amendment_policy / expected_accession
expected_fact_or_section_locators / expected_result_or_error
required_milestones / allowed_tools / forbidden_tools
argument_constraints / budget / scorer_version
```

最低覆盖：

- company/ticker/name/CIK 歧义；
- 启用 `10-K`、`10-Q`、`10-K/A`，并验证 `10-Q/A` amendment 合同兼容与 base 关系；
- report/filed/accepted/public-available time 与 visibility policy；
- submissions current list、`filings.files` supplemental history、coverage manifest、重复 accession 和 supplemental 缺失/损坏；
- bulk `coverage_through` 前/等于/后、post-watermark 增量补齐，以及未补齐 gap 禁止 `no_result`；
- append-only document/response source version、correction/deletion、hash 幂等和内容变化异常；
- standard/custom XBRL fact；
- instant/duration、dimensions、unit/period；
- cutoff 后 filing；
- 429/5xx/timeout、99/100 CIK bulk threshold、bulk partial/failure、重复同步、损坏快照和跨 Workspace。

规则 Scorer 必须分别输出 identity/selection、source/locator、point-in-time、XBRL context、trajectory/readiness、failure classification、idempotency 和 Workspace/security 指标，并为每项指标从 manifest 计算固定 eligible denominator。source identity/locator 可解析率 100% 只统计预期返回来源的 eligible cases；ambiguous/no-result/依赖/权限 case 不进入该分母，但必须进入各自错误分类分母。future leakage、错误 company/form/accession、跨 Workspace、未授权写 Tool、重复 filing/snapshot/fact/index 和依赖错误误报 `no_result` 均为 0。

成功且 `expected_snapshot_presence=true` 的 `execution_kind=tool` case 必须由 case → AgentRun/ToolCall/Trace → source snapshot 反查。`sync_kind=canonical_source` 由 case → Job/Outbox → canonical sync/source snapshot 反查，合法成功时可固定 `expected_import_presence=false`；`sync_kind=workspace_import` 才必须由 case → Job/Outbox/`workspace_sec_imports` → source snapshot 反查，并分别验证 manifest 的 snapshot/import presence。429、连接失败、bulk 下载前失败或快照提交前 hard stop 可固定 `expected_snapshot_presence=false`/`expected_import_presence=false`，此时 source refs/hash/visibility 字段可空，证据链在 request attempt 或 Job/Event + typed error 收口，并断言零已提交 snapshot/import；这些 case 仍进入适用错误指标的固定 denominator。

冻结 response/replay 进入普通 PR CI；live smoke 单独记录日期、官方 URL、retrieved_at、content hash、Adapter/version、请求速率、失败和重试，不进入普通 PR 硬门，也不得与 deterministic 指标平均。

### 4.3 `sec-tool-v1`

Day 7 使用同一 10-case manifest、`sec-tool-contract-data-v1` 和共享预算运行 30 个 A0/A1/A2 策略观察。主类别简单事实、计算、跨章节、base/amendment 和无答案各 2 条；A0 是 full-context/no-tools oracle，A1 只允许 filing Hybrid search/read，A2 必须使用正式 `sec-l4-v1` 六 Tool surface。三者不能获得不同 gold、Scope、预算或数据版本。

输入与产物分层保存：

- `evals/scenarios/sec-tool-v1.json`：不可变 case、gold identity/Evidence/program、共享预算和策略 surface；
- `evals/observations/sec-tool-v1.json`：冻结策略观察与 execution boundary；
- `evals/reports/sec-tool-v1.json` / `.md`：只能由 `sec-tool-scorer-v1` 重算的派生报告。

Scorer 要求 10×3 组合精确覆盖，缺失或重复即拒绝评分；错误 company/period/accession 从实际选择直接计算，不读取自报布尔值。答案、Evidence、Citation、calculation program/lineage、无答案拒答、Tool allowlist、预算、步骤、Token、成本和延迟分别报告。当前 deterministic report 的 A2 复杂题相对 A1 净增益为 `0.833333`，简单题退化为 `0`，错误 identity 为 `0`，A2 拒答/Citation/calculation lineage 均为 `1.0`。

这些 observation 绑定 production component pytest，但仍是 frozen contract，不是当前模型实际运行。报告必须保留 `live_sec_executed=false`、`live_model_executed=false`、`browser_e2e_executed=false` 等边界，并在真实依赖、中英 paired、分支/PR/main CI 和 owner review 未齐时输出 `day7_closeout_ready=false`。A0/A1/A2 的 public/offline 与 live repeated run 属于 Day 9/10，不能回填或平均到该 deterministic 报告。

### 4.4 `sec-verification-v1`（Day 8）

Day 8 在不改写 `sec-tool-v1` 的前提下新增独立 manifest/observation/report，覆盖五类能力：

- Claim support/refute/conflict/insufficient、Citation resolvability、scope/period/unit 和 Calculation 重算；
- one-revise success/no-progress/max-revision、budget/deadline/cancel 和 dependency failure；
- filing/table/web 间接 prompt injection、伪造 system/tool 指令和未授权写请求；
- monitor approval allow/deny/timeout、watermark、amendment Case、重复 tick/decision/notification；
- L4/L5/Monitor 的 Worker hard stop、Checkpoint CAS、迟到结果和 side-effect recovery。

每个 case 固定相同的 source/data hash、`FinancialScope`、allowed/forbidden actions、Evidence/Calculation gold、expected verification status、Runtime stop reason、最终数据库状态和预算。A2/A3/A4 必须复用同一适用 case；不允许给 A3 额外 gold Evidence，或把 A4 的 Monitor 恢复结果平均成问答准确率。

Day 8 deterministic 硬门为：verified false support=0、Citation/source identity resolvability=100%、fabricated source/accession/number/formula=0、future leakage=0、跨 Workspace=0、未授权写=0、重复副作用=0、恢复成功率=100%。A3 只有在复杂检索/计算/冲突类相对 A2 有净收益、简单题退化不超过 2pp 且成本/延迟未越界时保留 mandatory verifier/one-revise；A4 主要以审批、最终数据库状态和恢复正确性验收。

Day 8 已冻结 14-case/42-run `sec-verification-v1`，独立 scorer 重算 deterministic/security/fault 三层合同；A3 复杂题相对 A2 净增益为 `0.714286`、简单题退化为 `0`，A4 operational/recovery 为 `1.0/1.0`。PR #14 的 push/PR/main CI 均通过。该结果仍是 frozen replay + executable contract refs；专用 Monitor 浏览器旅程、真实 hard-stop 故障注入、live SEC/model 和 owner closeout 仍留在 Day 10，不能平均进 Day 9 offline score。

### 4.5 Day 9 pinned registry baseline

Day 9 Step 1 只固定来源元数据，不提交外部数据 payload。以下 revision 与原始 artifact SHA-256 于 2026-08-29 从官方 GitHub/Hugging Face 来源核验；后续 Adapter 必须下载精确 revision 后按 byte size 与 hash 校验，禁止使用浮动 `main`：

| Dataset | Source revision | Frozen artifacts | 许可与接入边界 |
|---|---|---|---|
| FinQA | GitHub `0f16e2867befa6840783e58be38c9efb9229d742` | `train.json` `49f237…28db6` / 78,216,616 B；`dev.json` `a847fb…eee51` / 10,954,658 B；`test.json` `831dbf…30dc` / 14,395,143 B | 数据站声明 CC BY 4.0；仓库代码 LICENSE 为 MIT。只测固定上下文 supporting facts/program/execution，不测开放 SEC 检索 |
| TAT-QA | GitHub `870accc41953dcde885aabeb963d94aabdc0fbc3` | train `2df6e7…7c69` / 12,845,647 B；dev `8da095…16af5` / 1,637,431 B；test `6efcf0…a96c` / 1,146,306 B；test gold `c4d084…4b597` / 2,167,546 B | README 声明数据 CC BY 4.0，仓库代码 LICENSE 为 MIT。test input/gold 分开登记，防止 gold 进入模型 Context |
| FinanceBench | GitHub `cc39aeb4afdf33909ee1412188bf89035950c2eb`；HF card `e04404e3a97f69f79c14d42f24981a1c9c3bcd18` | open source `a5a2aa…71877` / 929,848 B；document info `1c6912…89575` / 88,781 B | HF 官方 dataset card 标 CC BY-NC 4.0；仅内部非商用补充评测。源 PDF 权利独立，payload/PDF 默认不进入本仓库或商业发布物 |
| FinSearchComp | GitHub `55b6393fcf3c8f749ba5a69a70b20d4ef6f67caf`；HF card `1fd1beea75482e2dd5e2be8f618195d9c6aff176` | full `6437a6…08c4` / 5,948,881 B；AkShare `9a0cf3…40d6` / 5,592,958 B | HF 官方 dataset card 标 CC BY 4.0。historical/AkShare 与动态 live 分报；专业数据库依赖与 LLM judge 不进入普通 PR 硬门 |

表中短 hash 仅用于文档阅读，机器 registry 必须保存完整 64 位 SHA-256。Step 1 建立该 `registered_only` 基线；Step 2 与 Step 4 已完成四项 Adapter、转换测试、许可约束和本地 artifact 校验，因此当前四项均为 `adapter_ready`，但仍因 owner/license、源文档、模型 run 或 live 依赖 blocker 保持 `release_eligible=false`。

机器合同位于 `evals/registry/sec-agent-datasets-v1.json` 与 `evals/manifests/sec-agent-release-v1.json`，并由正式 `evaluation` 模块生成 `evals/schemas/` 下两个 JSON Schema。manifest 保存 registry canonical SHA-256；严格加载器拒绝重复 key、NaN/Infinity、浮动 revision、许可权利升级、gold Context 暴露、跨 split document group、future SEC source 和不完整执行引用。该实现只有本地合同测试证据，不代表外部 payload 已下载、Adapter 已执行、case 已绑定真实 Run/Trace/Evidence 或任何 benchmark 分数成立。

### 4.6 `sec-temporal-v1`

Day 9 冻结至少 60 个 release cases。所有 case 必须来自 `as_of` 前可见的官方 filing snapshot，并锁定：

```text
case_id
dataset_version
question_zh / optional question_en
cik
allowed_forms
as_of
selected_accessions
expected_evidence_locators
expected_fact_inputs
expected_program
expected_result / tolerance / unit / rounding
expected_business_status
required_milestones
allowed_tools
forbidden_tools
argument_constraints
partial_order
budget
expected_runtime_stop_reason
```

最低分层：

| 类别 | 最少数量 |
|---|---:|
| 单 filing 直接事实 | 10 |
| 表格/文本 Evidence | 8 |
| 可执行计算 | 10 |
| 跨期可比与变化 | 8 |
| amendment/base 选择 | 6 |
| custom tag/footnote/冲突 | 6 |
| 无答案与 cutoff 负例 | 6 |
| 注入、权限、审批与恢复 | 6 |

同一 case 可以覆盖多个标签，但主类别计数只记一次。case 构造、dev 和 release holdout 必须按 accession/filing 分离，不能把同一表述轻微改写后跨 split。

### 4.7 中英配对集

至少 30 个 `sec-temporal-v1` case 提供中文/英文配对问题，共享同一 gold scope、accession、Evidence、program、result 和 business status。

自动硬指标比较：

- CIK/accession/form/period 选择；
- Evidence IDs/locators；
- calculator program/input/result；
- business status；
- Citation resolvability。

中文自然度、术语和说明完整性使用人工抽样。翻译模型或 LLM judge 不能覆盖事实链不一致。

实现位置为 `evals/scenarios/sec-temporal-v1.json`、`evals/datasets/sec-temporal-v1.md` 和 `evals/reviews/sec-temporal-v1-language-sample.md`。30 个 pair 共享单一 gold 并展开为 60 个语言 case，11 个真实 accession 按 filing 隔离 split；验证报告已证明 22/22 source artifact 与 35/35 Evidence 可解析、future leakage 为 0。该报告没有执行模型或绑定 Runtime，且中文抽样清单尚未签字，所以不能据此填写本节后续 capability 指标或宣称 D9-06 完成。

### 4.8 受限外部补充与 `agent-security-v1`

FinanceBench Adapter 只消费固定 150 题 JSONL 与 metadata，输出不含 answer/justification/Evidence gold 的 sanitized input；源 PDF 不物化、不提交。当前 150 题引用 84 个文档并包含 189 条 Evidence，metadata 的一个未引用 document id 存在 period 冲突并被显式报告。数据仅限内部非商用，官方 answer correctness 需要人工审查，因此本地转换成功不产生官方成绩。

FinSearchComp 将 391 个 historical case（T2 219、T3 172）与 244 个 dynamic T1 分报。dynamic 中 203 个属于 AkShare-compatible，41 个依赖其他或专业数据源；两份 artifact 的 203 个共同动态 case 时间字段全部漂移，不能混用 snapshot。historical contract 不执行 LLM judge，dynamic contract 的 live/model/judge/repeated run 与 `pass^k` 仍为 null；这些结果不能进入普通 PR 单一硬门或与 fixed suite 平均。

`agent-security-v1` 从 temporal 的三组安全/恢复 pair 派生 6 个中英 case，不导入 BFCL/ToolSandbox/tau-bench/AgentDojo 代码或 payload，只复用其 action/argument、milestone/final-state、all-k 与 injection utility/attack 的评测方法。每 case 固定 3 次 observation，scorer 从实际 action、Workspace、partial order、stop reason 和 final state 推导指标。当前 18/18 contract trial、6/6 经验 `pass^3`、攻击/越权/重复副作用 0 只证明生成器与 scorer；`UnifiedAgentRuntime`、真实模型、生产数据库终态和远端 CI 均未执行。

### 4.9 `release-suite-v1`

Step 5 的 release suite 只消费受检派生报告和 Step 1 registry/release manifest，不重新解释原始数据或复制各 benchmark scorer。每个输入保存 report identity/version、case/run denominator、evidence layer 和文件 SHA-256。当前 manifest 仍是没有 strategy/case 的 `contract_only` 状态；若它开始执行，现有聚合器会 fail closed，要求同步新的 common-case 评测逻辑。

deterministic 报告保留三段同 manifest/data/Scope/budget 的局部决策：10-case `sec-tool-v1` A1→A2、14-case `sec-verification-v1` A2→A3，以及同 14-case 的 A3→A4 operational extension。A2/A3 可以进入下一证据层，A4 只保留于审批/恢复范围。因为两个 source suite 不同，不生成全局 A0～A4 score，也不选择 production default。没有 ranked retrieval candidate 时，`retrieval_recall_at_5` 必须为 `not_measured`，不能以 answer/complex accuracy 代替。

offline 报告仅声明四个 Adapter 的可用 denominator，model/prediction/official scores 全为空；live 报告冻结 FinSearchComp dynamic、SEC temporal、Agent security 三类目标的 case 数和最低 3 次要求，provider/model/version、均值、方差、`pass^k`、成本和延迟在未运行时均为空。failure taxonomy 将缺失证据与真实运行失败分开；当前 9 项均为 release blocker，但 observed runtime failure 为 0。该实现使缺口可机器审计，不代表 D9-08 已完成。

### 4.10 `sec-release-readiness-v1`

Day 10 在同一 `evaluation` bounded context 将能力矩阵、Day 1～10 日志、代码/测试、CI workflow 和既有评测报告投影为单一发布台账。manifest 固定 88 个正式 requirement 的规范化 digest、六种状态计数、Day owner/依赖/验证命令、当前 41 个 artifact、9 个 taxonomy blocker 的双向映射、7 个跨 Day blocker 和 8 个 external gate。生成报告为每个 artifact 计算 byte size/SHA-256，并要求全部非 `complete` requirement 被 open blocker 精确覆盖、全部 pending gate 被 open blocker 引用。

当前结果是 45 个 `complete`、32 个 `implemented_pending_verification`、7 个 `thin_slice`、4 个 `planned`；43 个未完成目标、16 个 open blocker、5 个 pending gate，故 `release_decision=no_go`、`rc_ready=false`。Day 9 push/PR/main CI 三项已有 URL/commit 证据并标记 verified；最终 owner acceptance 仍 pending。该层只审计证据完整性，不运行模型、Runtime、公开 benchmark 或 live SEC，也不把历史 `complete` 自动升级为当前发布能力。

本地验证为聚焦/evaluation `8/65 passed`、readiness branch coverage `84%`、无强制真实服务全量 `1184 passed, 88 skipped`；Python/Web/构建/OpenAPI、依赖审计和 98-commit Gitleaks 通过。真实依赖、Chromium、远端分支/PR/main CI 和 owner review 未在本步执行，继续保留为发布证据缺口。

### 4.11 Step 2 产品路径证据边界

Step 2 把已锁定 Filing 的 `FinancialScope` 输入交给现有 Research Runtime，并让 Workbench 读取正式 Verification Report 的四态、Claim、Citation、Calculation、issue、stop reason 和 Evidence snapshot。相关组件/API 回归证明页面不会自行推断状态、amendment 不会静默降级、Case Evidence 可反查，且 active/paused Run 会从正式 API 刷新。这些测试登记进 readiness artifact，但不进入 capability score，也不替代 `sec-verification-v1` scorer。

无拦截的真实依赖 Playwright、受控 SEC source、Worker resume、新 Filing Monitor/Case、forbidden/cancelled/retry 和刷新恢复尚未执行；现有 `tests/e2e/sec-workbench.spec.ts` 仍是浏览器接口回放，只能证明前端渲染与响应式布局。因此 Step 2 不关闭 Day 5/8 browser/recovery blocker，不改变 Day 9 deterministic/offline/live 报告，也不支持对外声称完整中文闭环。

## 5. Scorer 分层

### 5.1 Scope 与 source identity

- filer/CIK exact match；
- allowed form 与 selected accession；
- report period 与 `as_of`；
- amendment/base policy；
- future-information leakage；
- official source URL/snapshot hash resolvability。

### 5.2 Retrieval

- fact/section/table Recall@K；
- MRR@10/nDCG；
- Dense/BM25/RRF/rerank 分层 trace；
- wrong-accession candidate rate；
- Context included/excluded reason accuracy。

### 5.3 Numerical reasoning

- operand Evidence exact match；
- program/operator accuracy；
- execution/result accuracy；
- unit/scale/period/rounding accuracy；
- divide-by-zero/incompatible-unit refusal；
- derived-number lineage completeness。

### 5.4 Evidence、Claim 与 Citation

- Evidence attribution；
- Claim support/refute/conflict/insufficient；
- Citation precision/recall/resolvability；
- verified Claim precision 与 answer coverage；
- fabricated source/accession/number/formula count。

### 5.5 Trajectory

Gold 不要求唯一精确工具序列。每个 case 使用：

- required milestones/tools；
- allowed tools；
- forbidden tools/actions；
- argument constraints；
- partial-order dependencies；
- max calls/steps/cost/deadline；
- expected stop reason；
- expected final database state。

例如必须满足：

```text
resolve filer
< select filing/accession
< fetch fact or search locked filing
< calculate
< verify/finalize
```

结构化与叙述检索可以并行或交换顺序；无计算题不强制调用 calculator；信息不足时应澄清或拒答，而不是为了匹配轨迹调用无关 Tool。

### 5.6 Runtime、HITL 与安全

- hard-stop recovery；
- Checkpoint revision/CAS；
- repeated resume/decision；
- duplicate ToolCall/Calculation/Monitor/Case/notification；
- unauthorized write；
- cross-workspace access；
- indirect prompt injection utility/attack success；
- budget/cancel/deadline/no-progress stop。

## 6. A0～A4 消融

| 配置 | 能力 | 目的 |
|---|---|---|
| A0 | Oracle/full context 或 gold evidence | 推理上限；不能与真实检索公平混报 |
| A1 | 纯 Filing Hybrid RAG | RAG baseline |
| A2 | A1 + SEC/XBRL structured tools + typed calculator | 测 Tool 与确定性计算净收益 |
| A3 | A2 + mandatory verifier + 最多一次 revise | 测核验与修订净收益 |
| A4 | A3 + durable monitor/HITL | 测长任务、写操作和恢复，不期待简单问答分数提升 |

保留 A2/A3 的条件：

- 复杂检索、计算、amendment 或 conflict 类别有可测净收益；
- 简单事实题相对前一配置退化不超过 2 个百分点；
- 不突破冻结 latency/cost budget；
- fabricated facts、future leakage、未授权写和重复副作用仍为 0。

不满足时必须回退具体策略，不能因为“更像 Agent”而保留。

## 7. Release 硬门禁

| 指标 | 门禁 |
|---|---:|
| Citation/source identity resolvability | 100% |
| Fabricated source/accession/number/formula | 0 |
| Future-information leakage | 0 |
| Wrong company/period/accession（确定性集） | 0 |
| Cross-workspace retrieval/read/write | 0 |
| Unauthorized write Tool | 0 |
| Duplicate side effect | 0 |
| Verified Claim false support（确定性集） | 0 |
| SEC Retrieval Recall@5 | ≥ 0.80 |
| No-answer correct abstention | ≥ 0.90 |
| 中英 pair 事实链一致率 | 100% |
| Recovery scenarios success | 100% |
| Accepted baseline regression | ≤ 2 percentage points |

FinQA/TAT-QA/FinanceBench/FinSearchComp 使用各自官方指标并单独报告，不用本表阈值伪装官方 leaderboard。外部 benchmark 分数不是上述硬门的替代项。

## 8. 运行批次

### PR quick suite

- 无公网、无付费模型；
- 至少 40 个 deterministic cases；
- frozen SEC responses、calculator、trajectory、point-in-time、security 和 recovery；
- 目标是快速阻断合同/逻辑回归。

### Offline release suite

- `sec-temporal-v1` 全集；
- 公开 benchmark 固定 manifests；
- A0～A4；
- 固定模型输出或受控 provider job；
- 生成机器可比 JSON 和 Markdown。

### Live suite

- 最新 SEC filing smoke；
- FinSearchComp 动态/可用子集；
- 固定 provider/model/tool/prompt version；
- 每个 case 至少 3 次，报告均值、离散度、成功交集 `pass^k`、成本和延迟；
- 失败不得覆盖固定 suite 结果，需区分外部依赖、模型、数据漂移和产品缺陷。

## 9. 报告与发布声明

每份报告至少包含：

- evaluation manifest/hash；
- dataset card 与许可状态；
- model/runtime/harness/prompt/tool/context/retrieval versions；
- case 数、split、过滤和失败；
- 分层指标，不只一个总分；
- failure taxonomy；
- A0～A4 变化、成本和延迟；
- deterministic/offline/live 标签；
- 规则 scorer、人工抽样和 LLM judge 的各自结论；
- 已知缺口与回退决定。

发布说明只能引用实际运行且可反查的报告。Frozen replay 通过时写“Runtime/合同回归通过”；public benchmark 通过时写具体 benchmark 和 split；live suite 通过时写模型、日期、数据与重复次数。三者均不得简写为“完整金融 Agent 已验证”。
