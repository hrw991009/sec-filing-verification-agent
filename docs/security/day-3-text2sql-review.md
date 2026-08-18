# Day 3 安全 Text2SQL 与 SQLGlot 复核

> 复核日期：2026-08-17
>
> 适用切片：Day 3 第 4 步，`database.text2sql:v1`

## 1. 第三方依赖结论

本步新增并锁定 `sqlglot==28.5.0`，只用于 PostgreSQL 方言的 SQL 解析、AST 遍历、列限定和规范化输出。上游项目使用 MIT 许可证；本地 wheel metadata 的 `License-Expression` 也是 `MIT`。复核入口为 [SQLGlot 官方文档](https://sqlglot.com/sqlglot.html)、[官方 GitHub 仓库](https://github.com/tobymao/sqlglot)和 [PyPI 发布记录](https://pypi.org/project/sqlglot/28.5.0/)。

SQLGlot 是 parser/transpiler，不被当成安全判定本身。项目在完整 AST 上另行执行 fail-closed policy；未知或不在 allowlist 的节点、函数、表、列和输出形状都被拒绝。依赖不发网、不读取 Secret、不执行 SQL 或模型生成代码。`uv audit --locked` 已检查锁文件中的 73 个第三方包，当前没有已知漏洞或 adverse project status。

## 2. 信任与权限边界

- 自然语言问题和模型生成 SQL 都是不可信输入；模型返回的 `generated_sql` 不是授权，也不是 `validated_sql`。
- 连接密码只存在于 `TEXT2SQL_DATABASE_URL` Secret setting；`data_connections` 只保存固定引用 `settings:text2sql_database_url`，API、Event、Artifact 和日志均不返回 DSN。
- 查询使用与应用 owner 不同的 PostgreSQL 登录账号。启动组合会拒绝应用账号同名 DSN；每个事务再执行 `SET TRANSACTION READ ONLY`，并检查账号对 allowlisted 表只有 `SELECT`、没有写权限。
- 当前固定 allowlist 只有显式合成样例表 `public.sample_company_metrics`。模型不能提供连接 URL、schema allowlist、数据库角色、timeout 或预算。
- WorkspaceScope 和 `RUN_TOOL` capability 由已认证 principal/可信 Runtime Context 提供。Agent 路径的 QueryRun 通过复合外键绑定 ToolCall、AgentRun、Workspace 和 actor；表格/图表 Artifact 绑定真实 Tool execution Step。

## 3. SQL、预算与结果校验

校验器明确指定 PostgreSQL 方言并要求恰好一个根 `SELECT`。它递归拒绝 DML、DDL、COPY、CALL、命令、参数占位符、递归 CTE、锁、`SELECT INTO`、系统 schema、未限定或未允许的表/列、危险/未知函数、过多 JOIN、过深 AST、大 OFFSET 和不稳定输出列；`sqlglot.qualify` 再依据当次不可变 SchemaSnapshot 展开并限定列。

通过 AST 的 SQL 仍须经过数据库硬边界：只读事务、statement timeout、最大返回行数，以及 `EXPLAIN (FORMAT JSON)` 计划预算。扫描行预算递归累计所有计划节点，不能只看聚合根节点的少量输出。查询结果只接受有界 JSON scalar，拒绝二进制、非有限数、超长/控制字符字符串和未知类型。

每次尝试都形成 QueryRun：保存问题、generated/validated SQL、validator 版本、SchemaSnapshot、预算、计划成本/扫描行、状态、行数、结果 hash、稳定错误码和 trace。数据库错误只映射为稳定码，不把 SQLSTATE 详情、驱动错误或凭据返回客户端。

## 4. Artifact 边界

- 表格 Artifact 最多 200 行、64 列、512 KiB，使用规范 JSON 的 SHA-256 绑定内容；列表 API 只返回轻量 QueryRun 摘要，详情才返回 Artifact。
- 图表不接受模型提供的任意 ECharts option 或 JavaScript。服务端只从 typed `ChartRequest` 和已经校验的表格列构造 `table/line/bar/pie/scatter` 固定 option；数值轴、series 列、字段存在性、总字节和 content hash 都再次校验。
- Text2SQL Observation 只携带前三行有界结果和 Artifact ID；source locator 是服务端构造的 `sql://<connection-id>/.../query-runs/<id>`，不接受模型或数据库字段提供的 URL。
- Observation 仍是不可信 Context，不会在本步提升为 Evidence；Observation→Evidence 属 Day 4。

## 5. 已执行的负向验证

- AST 单测逐项拒绝 DELETE、UPDATE、INSERT、CREATE、DROP、ALTER、COPY、CALL、多语句、递归 CTE、`pg_sleep`、`pg_read_file`、`current_setting`、`nextval`、系统表、未限定表、未知列、参数、超量 OFFSET、`SELECT INTO` 和 `FOR UPDATE`。
- 计划单测证明聚合根输出 4 行但子扫描 250,000 行时，预算事实记录 250,004 行；负数、非有限值和畸形子计划 fail-closed。
- 真实 disposable PostgreSQL 完成 migration upgrade/check/downgrade/upgrade；独立账号执行安全聚合查询并生成持久表格/图表，危险 SQL 留下失败 QueryRun，账号绕过应用直接 `DELETE` 仍被 PostgreSQL `InsufficientPrivilege` 拒绝。
- Registry/Executor 单测证明 `database.text2sql:v1` 的输入 Schema 可组成 Provider structured `response_schema`，查询失败映射稳定 Tool error，成功 Observation 不回传 generated SQL。

## 6. 尚未关闭的范围

当前只实现后端数据库浏览、直接 API、Tool Adapter、审计与 Artifact 合同；第 5 步的数据库/图表页面、Tool Inspector、Playwright 用户旅程和生产 Conversation/Agent Job 的 L1/L2 command materialization 尚未完成。QueryRun 进程崩溃后的陈旧 `running` 对账、正式数据源连接管理、显式 Run purge、审计留存/恢复/备份也必须在生产入口前关闭。上述限制不影响本步对固定样例库的后端安全结论，但禁止把它表述成 Day 3 或生产聊天 Text2SQL 已完成。
