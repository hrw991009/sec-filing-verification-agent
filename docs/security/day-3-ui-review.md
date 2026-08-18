# Day 3 Tool Inspector、行业页与图表前端安全复核

> 复核日期：2026-08-17
>
> 适用范围：Day 3 第 5 步的 Agent Web 模式、Tool Inspector、行业情报页、数据库页和 ECharts Artifact 渲染。

## 1. 新依赖与许可证

本步锁定 `echarts==6.1.0`，仅在浏览器中把服务端已经校验的图表 Artifact 渲染为 SVG。安装包 metadata 与随包 `LICENSE` 均标明 Apache-2.0，随包 `NOTICE` 标明 Apache Software Foundation 版权；上游入口为 [Apache ECharts 官方站点](https://echarts.apache.org/)与[官方仓库](https://github.com/apache/echarts)。发布物必须保留依赖自身的许可证/NOTICE，不得移除构建产物中的法律声明。

ECharts 6.1 的声明文件与本项目 TypeScript 6 的 `exactOptionalPropertyTypes` 存在第三方类型冲突，因此 Web 应用只对依赖声明启用 `skipLibCheck`；应用源码仍由 strict TypeScript、ESLint、Vitest 和 production build 校验。这个兼容开关不能扩大到关闭 `strict` 或把未知 Artifact 强制断言成 ECharts option。

## 2. Artifact 与脚本边界

- 浏览器不接受模型生成或 API 任意返回的原生 ECharts option。`parseSafeChartOption` 对根字段、轴、series、标量类型、行列/series 数量和总 JSON 字节执行精确 allowlist；未知字段一律拒绝。
- 禁止 `formatter`、函数、`custom` series、图片、HTML、外部 URL、dataset transform 和任何可执行表达式。渲染器固定为 SVG，输入只能是服务端 typed Artifact 支持的 `table/line/bar/pie/scatter`。
- 表格单元格再次按 JSON scalar 处理，未知对象不会通过 React 隐式字符串化后显示；超量数据必须在进入 ECharts 前失败。
- 数据库页面展示 generated SQL、validated SQL、计划预算和稳定错误码，但不展示 DSN、驱动异常或数据库 Secret。

## 3. Tool Inspector 与来源边界

- Inspector 只消费正式 Trace API，不使用前端 Mock trajectory；刷新后由 PostgreSQL Event/Context manifest 重建。
- 可显示字段固定为 call/tool/version、capability、策略、审批、timeout、结果/费用上限、耗时、稳定错误码、Observation ID 和 model-visible envelope digest。raw arguments、幂等键、参数 digest、Provider body、Observation 正文、Secret 和 chain-of-thought 不在 allowlist。
- `tool_observation` manifest 必须同时有来源 ID 和 envelope hash；后端 Trace 会把它与本 Run 的 `agent.tool.completed` 事实关联，不一致时 fail-closed。
- 行业 Source locator 只接受无 userinfo/query/fragment 的 allowlisted HTTPS URL；页面通过 `rel="noreferrer noopener"` 打开新窗口。模型、数据库字段或未知 Provider 不能直接提供可点击 URL。

## 4. 权限、失败与隐私

- 当前行业偏好和所有行业/数据库请求都绑定已认证 Workspace；切换 Workspace 会重新加载，不复用旧 Workspace 数据。
- Web 模式只有当前行业存在时才可提交；服务端仍重新验证 `search_mode=web`、industry、capability、Tool allowlist 和 Runtime 预算，前端禁用按钮不是授权。
- Provider 未配置或条款未批准时页面展示明确 readiness，不回退 Fake。浏览器 E2E 的冻结 Provider 只位于测试驱动，不进入正式资源组合。
- 会话删除是可恢复策略中的逻辑删除：用户列表立即隐藏 Conversation，但 Run、Event、ToolCall/ToolRun 与安全 Trace 仍保留，真实 PostgreSQL 集成已证明 Tool 审计不会被普通删除级联清空。物理隐私擦除必须走未来显式授权 purge，并在执行前处理最小安全审计留存；Day 7 对外发布前仍需完成隔离备份恢复和 purge 演练。

## 5. 自动验证

组件测试覆盖恶意 chart option、超量数据、非法 locator、Trace 原文不泄漏、Workspace 切换、加载/未配置/失败 UI 和安全表格/图表。Playwright 真实旅程从页面提交 Web Turn，经 PostgreSQL Job/Outbox、正式 Loader、`UnifiedAgentRuntime` 和 `industry.web_search:v1` Adapter，再从 Trace API 显示 Inspector，并验证刷新恢复与数据库页面入口。

依赖漏洞由根目录 `pnpm audit --audit-level high` 门禁检查；许可证和 NOTICE 由本文件记录并在发布审查中复核。该结论不把冻结响应冒充真实 Provider 在线质量，也不把 Observation 提升为 Day 4 Evidence。
