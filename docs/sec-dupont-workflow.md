# 多年杜邦分析工作流

`sec.dupont-analysis@v2` 是内置 Research 任务，支持 3～5 年（前端默认 5 年），执行固定的 `FinancialResearchWorkflow` LangGraph。
历史 `@v1` 与 schema v1 Scope 保留原两年范围；新 v2 任务要求 schema v2 Scope 和明确的 `analysis_years`，不扩大旧任务的数据权限。
它不是新的 LLM 数学工具，也不是独立运行时：复用 planner、researcher、evidence_curator、writer、verifier 五种角色，及 Job/Outbox、预算、事件、检查点和恢复机制。

## 使用

1. Research → SEC Filing → 研究任务 → 杜邦分析，选择 3、4 或 5 年。
2. 选择 Knowledge Base，填写 CIK、目标财年的报告期与截止时点。
3. 点击“补齐杜邦分析数据”。系统选择指定数量的连续正式 10-K，复用 SEC 下载、不可变快照与入库任务。页面等待入库后同步 XBRL。
4. 输入齐备后确认 Brief 并开始。报告包含各年输入、四项比率、方向比较、期间、计算引用及口径限制。

沿用现有 ResearchDraft 与独立 VerificationReport 的存储模型：草稿正文保留 L3 草稿声明；是否通过核验以页面的 Verification 状态及问题列表为准，不从草稿标题推断。

数据准备是用户明确触发的写操作；研究任务的分析工具均只读，不创建监控。
修改公司、报告期、知识库或截止时点后，应重新准备数据。
准备中刷新页面不会撤销正式入库任务，再次点击会复用已有导入。

API：`POST /api/v1/workspaces/{workspace_id}/disclosures/dupont/prepare`。
请求字段：`cik`、`fiscal_year`、`knowledge_base_id`、`as_of`、`years`。API 为历史客户端保留默认 2 年；新前端显式传入 3～5 年。
响应状态：`ready`、`awaiting_ingestion`、`insufficient_data`，包含锁定的 `financial_scope`、导入任务和问题列表。

## 数据与公式

每年六个有顺序的输入：收入、归母净利润、期初资产、期末资产、期初归母权益、期末归母权益。
连续 N 年需要 4N+2 个唯一事实：N 年收入与利润、N+1 期资产与权益；五年为 22 个。

| 项目 | 公式 |
| --- | --- |
| 平均资产 | (期初资产 + 期末资产) / 2 |
| 平均归母权益 | (期初归母权益 + 期末归母权益) / 2 |
| 净利率 | 归母净利润 / 收入 × 100% |
| 总资产周转率 | 收入 / 平均资产 |
| 权益乘数 | 平均资产 / 平均归母权益 |
| ROE | 归母净利润 / 平均归母权益 × 100% |

`sec.get_xbrl_facts@v1` 的 `purpose=dupont` 选择完整输入并返回每年可直接传入计算器的 operands。
`finance.calculate@v1` 的 `operator=dupont` 使用 Decimal、单位及 scale 归一化，返回 ROE 和全部 components。
报告显示四位小数；ROE 不使用已舍入的因子相乘。收入、资产和权益必须为正，净利润可以为负。

## 防止错误成功

- 仅标准 US-GAAP、USD、合并无维度事实；净利润 `NetIncomeLoss`、权益 `StockholdersEquity`，不混用含少数股东权益的概念。
- 年度长度允许 350–380 天以覆盖 52/53 周财年，各年必须连续，期初余额必须等于期间开始前一天。
- 仅锚定 10-K 与 Scope 授权的前序年度 10-K；不混入季度、未来申报、其他公司或未授权知识库。
- 重复披露数值一致才可去重；重述或收入别名数值冲突不择优忽略。10-K/A 在本版返回不支持。
- 缺数据、非正分母、未知数字转换、冲突或源证据失效均不允许输出“已验证”的完整比较。
- 计算器从正式 source_fact_id 重新加载数值。Evidence 正规化与 verifier 再次检查期间、单位、源值并复算全部因子；任何一年少一份有效计算也不能 VERIFIED。
- 定量表格由规范化计算结果确定性生成，避免模型改写数字；方向变化是会计分解，不自动推断经营原因。
- 模型上下文采用带版本的 `dupont-model-context-v2` 精简投影，保留各年操作数、源标识、期间和计算结果，移除计算回执中的重复输入元数据。完整 Observation 与原始 hash 仍持久化，精简内容由实际 ModelRequest 哈希绑定。

## 结果展示

一次 Research 后展示四项：指标卡及较上年变化、年度数据表、独立坐标轴柱状图、可切换年份的杜邦分解图；不包含趋势图、饼图或桑基图。
`GET /api/v1/workspaces/{workspace_id}/research-runs/{run_id}/result-view` 从当前草稿引用的正式计算 Evidence 派生结果，不从 Markdown 提取数字，也不再次调用模型。
金额使用 Scope 的单位和 scale；净利率与 ROE 的变化为百分点，其余为已发布数值之差。首年没有上年可比值，不补零。
核验标记绑定草稿及证据版本，来源失效或年度不完整时不发布图表数值。表格、指标卡和图形都可打开原有 Evidence 详情。

部署需先迁移数据库至 `e6b8d0f2a357`：支持官方 inline XBRL 的长 context ID，并将 Tool/Evidence 来源序号上限统一为 32。降级遇到超旧上限的数据会拒绝，不能截断来源标识。

## SEC 解析

真实年报需要支持嵌套 inline facts、continuations、排除文本、千分位、符号与独立 scale。
原始 XBRL 解析版本升级到 `sec-xbrl-raw-v2`，来源版本带 `v2`，不覆盖旧证据。
大型 narrative TextBlock 保留于原始快照与正文索引，不作为标量事实入库；其内部数字仍提取。
不支持的转换保留为原始字段，但不能用于计算。

参考：[XBRL Inline 1.1](https://specifications.xbrl.org/work-product-index-inline-xbrl-inline-xbrl-1.1.html)、[Transformation Registry](https://specifications.xbrl.org/work-product-index-inline-xbrl-transformation-registry-4.html)。
SEC submissions 的 CIK 支持官方的数字或字符串表示；接受日期与 filing date 按美国东部日期比较，UTC 跨日不能误判为未来申报。
相同 submissions 内容在缓存过期后重新抓取时，复用原始快照并保留首次可用时间，不因新的抓取时间冲突，也不允许回填更早的可用时间。

## 边界与真实验收

这是普通非金融企业的三因素会计分析，不自动识别行业适用性，不做 DCF、CAGR、估值、投资建议或五因素杜邦归因。
准备入口按“报告期结束日期所在公历年”识别财年；过渡财年或一年多份年报会明确拒绝自动选择。

提供显式启用的真实验收脚本 `apps/backend/tests/dupont_live_runner.py`：

```powershell
uv run --locked --package sec-filing-verification-agent-backend python apps/backend/tests/dupont_live_runner.py --workspace-id <workspace UUID> --user-id <owner UUID> --output .data/evals/dupont-live.json
```

默认 Microsoft 2021～2025；`--years 3|4|5` 指定范围（`2` 用于历史兼容验收），`--prepare-only` 只验证数据准备。
脚本创建或复用独立知识库，执行本次 Job 的正式 delivery，并保存真实状态、来源 hash、报告和 verifier 问题。
它不会启动消费所有旧任务的全局 worker，也不使用假模型或认证绕过。
模型与 SEC 配置读取 `.env`；不要把 key 放入命令、报告或 Git。
单条真实验收与全项目 50 次稳定性评估、完整 release gate 是不同证据，不互相替代。

2026-09-15 本地真实验收已完成：Microsoft 2025/2024，两份 SEC 官方年报、10 个唯一事实，正式 Agent 状态 `completed`，Verifier 为 `verified`，问题数 0。
2024 ROE 为 37.1333%，2025 为 33.2808%；对应净利率为 35.9560% / 36.1460%，资产周转率为 0.5305 / 0.4981，权益乘数为 1.9468 / 1.8484。
本地完整来源、hash、报告与核验结果保存在 `.data/evals/dupont-research-gemini-v14.json`；最新提示协议的重复验收为 `.data/evals/dupont-research-gemini-v19.json`，同样 `verified`、问题数 0（不提交私人 workspace 标识）。
此结果不代表模型网关已达到稳定性门槛；开发验收中仍观察到供应商不可用或无效响应，均保留为失败记录。
