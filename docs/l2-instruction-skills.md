# L2 轻量技能与固定金融 Research

本系统分开三层：Tool 是一次受控能力；L2 Skill 是按需加载的 `SKILL.md` 指令资产；Research 是系统内置的固定 LangGraph 金融工作流，不作为 L2 的 Skill 或可调用子 Run。

| 入口 | 执行方式 | 输出与边界 |
| --- | --- | --- |
| 直接回答 | 无工具调用 | 普通回答，不加载技能 |
| 轻量助手 · Skills / Web · L2 | 同一个有界模型/工具循环；自主选择是否 `skill.read@v1`，读取后继续原循环 | Markdown 回答及 Tool Trace；不产生正式 SEC 核验结果 |
| Research | `FinancialResearchWorkflow` 固定图，内置研究任务约束工具、报告与校验 | Research 报告、Evidence、独立 VerificationReport、现有持久化中断恢复 |

## 三个内置技能

资源位于 `apps/backend/src/industry_platform/modules/skills/bundled/<name>/SKILL.md`：

- `industry-news-brief`：当前所选行业的公开动态简报。仅使用现有 allowlist 行业检索，不是通用浏览器。
- `financial-excerpt-explanation`：解释用户提供的财报片段，区分原文事实、术语与推断，不自行计算新财务结果。
- `financial-basis-comparison`：比较已提供材料的期间、单位、合并范围、期初/期末等口径；缺失信息明确列出，不静默替换口径。

前端选择轻量助手即可，仍复用 `search_mode=web` 和现有行业选择。用户不必手动选 Skill；模型先看到名称、描述、内容 hash，匹配后调用 `skill.read`。与任务不匹配时可直接回答或调用普通工具。调用轨迹可看到 `skill.read`；读取不是自动完成分析。

每个文件采用 Agent Skills 的 Markdown + YAML frontmatter 格式。目前仅加载三个随应用发布的受审文件；frontmatter 使用 JSON（合法 YAML 子集）的 `name`、`description` 两个字段。不是通用第三方技能安装器。添加资产需修改显式 allowlist、审查指令和预算并补测试；不动态扫描用户目录，不执行脚本，也不支持模型提供文件路径。

## 信任、预算与版本

- `skill.read` 也经过正式 ToolRegistry、权限、超时、Step/Event/Observation 和预算。每个技能不是一个新 Tool；只注册一个受控读取入口。
- 模型必须提供 catalog 中的精确名称和内容 SHA-256。文件在 worker 资源构建时快照加载；未知或过期 hash 拒绝，不回退到其他版本。
- 只有与当前受审资产完全相符的 `skill.read@v1` 成功结果才激活宿主提供的技能指令。其他工具结果、篡改正文与错误结果不能激活。
- 正文只在可信指令层发送一次；其 Observation 数据副本在 Context manifest 中标记 `excluded_duplicate`，原始 Observation 和 hash 仍完整持久化，避免双份正文挤占预算或混淆指令与数据。
- 指令不得扩大工具表、能力、预算或改写最终输出协议。技能读取不返回 source，因此不是财务 Evidence，不生成 VERIFIED 声明。
- 新 L2 Run 使用 `conversation-l2-skills-v1` profile；仍为最多 2 次工具调用、8 个 Step、300 秒，读取占一次调用，输入上限 8192、单次输出上限 1536、总 Token 32768。不是无限循环。
- 历史 L2 `harness-v1` 保持原工具策略，不自动注入技能。L2 沿用现有执行语义，本次不声称增加持久中断续跑。

## Research 边界与部署

### 模型网关的多分支协议

当前本地网关对 `json_schema` 内嵌 `anyOf` 的对照测试总选择第一分支：要求 beta，
alpha-first 返回 alpha，beta-first 才返回 beta；相同要求的 JSON 模式返回 beta。
这会破坏 L2 在工具、技能与最终回答之间的自主选择，不能通过重新排列工具规避。

`AGENT_MODEL_ROUTE_JSON` 可显式配置 `structured_output_mode="json_object"`。适配器把
完整逻辑 Schema 放入系统指令并请求 JSON，仍在模型输出进入 Runtime 前用同一 Schema
严格校验；未知工具、错参、额外字段等继续拒绝。默认仍为 `json_schema`，不自动重试
或静默降低验证。配置读取不改变 API key；当前 `.env` 仅更新此非密钥字段。

行业新闻还依赖原有新闻 Provider 的配置和使用条款审批；技能本身不构成审批授权。
未配置或未批准时保留原有 typed failure，不伪造新闻或自动同意第三方条款。

任务定义移到 `modules/research/tasks.py`，策略在 `task_policy.py`，杜邦报告资产在 `dupont_report.py`。Research 实例不注入轻量技能，工具策略也不包含 `skill.read`；误配时拒绝启动。

新 API 使用 `task_name` / `task_version`；认证目录为 `GET /api/v1/research/tasks`。旧请求字段 `skill_name` / `skill_version` 不再接受，避免把两种概念混在一起；前后端和 worker 必须一起发布。

新的持久标识为 `research-task:<name>:<version>`；历史 `skill:<name>:<version>` 只在任务解析边界读取，保留原标识、策略版本和幂等指纹，不重写记录、图或 Checkpoint。未知历史任务版本继续 fail-closed。通用 Research 请求仍可不指定任务，沿用原有 WEB/LOCAL 路径。

节点依旧为范围澄清、Brief、计划、受控检索循环、Evidence、Claims、提纲、草稿、验证、最多一次修复、最终报告。节点内复用同一工具循环；Scope guard 等只是图内职责，不是五个独立 Agent。

停止新提交并排空旧 worker 后整体更新；回滚前停止新标识的任务提交并处理在途 Run。旧 worker 不认识新任务标识，不得用它恢复新 Run。已经存在的记录、Evidence 与 Checkpoint 均保留。

独立入口文档：[事实核验](sec-verification-workflow.md)、[两年杜邦分析](sec-dupont-workflow.md)。本次未新增图表组件；现有报告使用 Markdown 表格，图表展示应与执行架构分开验收。

## 验证入口

`apps/backend/tests/l2_skills_live_runner.py` 通过显式指定的现有 owner 和 workspace 提交
真实会话，并只执行该次正式 Job delivery；`--case excerpt|comparison|news` 选择验收问题，
`--output` 保存本地审计结果。问题不会强制指定 Skill，检查实际模型选择及工具轨迹。
脚本仅使用用户提供的虚构示例片段，不把示例数字当作真实公司事实。

完整后端、浏览器、固定协议测试与这些单条真实验收是不同证据；单次通过不代表
50 次稳定性矩阵或整个项目 release gate 已完成。失败轨迹同样保留，不自动重试或改判成功。

本地真实验收：财报片段解释与口径对比均完成 `skill.read → final`，未调用外部工具；
新任务标识的两年杜邦工作流在当前 JSON 模式下 `completed / verified`，问题数 0。
新闻案例实际完成技能读取，然后被原有新闻源的 `provider_terms_approval_required` 拦截；
这项只证明权限阻断生效，不宣称新闻检索端到端成功。
