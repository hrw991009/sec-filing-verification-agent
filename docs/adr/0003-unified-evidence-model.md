# ADR 0003：采用统一 Evidence 与 Citation 模型

> 状态：已接受
>
> 日期：2026-08-03
>
> 依据：`docs/master-plan.md` 第 2、5.2、5.3、5.4、6.4、11、12、13 节

## 背景

系统需要同时回答来自 PDF、Chunk、图片、表格、网页、SQL、新闻、政策、招投标和行业数据的问题。

如果每种来源都定义一套独立引用格式，聊天、知识库、行业资讯、Text2SQL 和 Research 将无法共享证据展示、权限检查、评测与审计逻辑，也难以证明一个结论究竟依据了什么。

项目需要一个既能表达多模态定位信息，又能连接回答、Research Claim 和来源快照的统一模型。

## 决定

采用统一 Evidence 与 Citation 模型。

### Evidence

Evidence 表达一项可引用来源，至少包括：

- `id`；
- `workspace_id`；
- `kind` 和 `schema_version`；
- `title`；
- `canonical_url`；
- `snapshot_file_id`；
- `locator_type` 和版本化 `locator`；
- `excerpt`；
- `content_hash`；
- `source_published_at`、`retrieved_at`；
- `license_or_terms`；
- `status`、`invalidated_at` 和 `invalidation_reason`；
- 经过 Schema 校验的 `metadata`。

`kind` 是受控枚举，至少覆盖 `document_text`、`document_image`、`document_table`、`web_snapshot`、`sql_result`、`news`、`policy`、`bidding` 和 `stock`。公共字段不得因为某个 Provider 缺失而改变语义，Provider 私有字段只能保留在 Adapter 原始快照或受控 metadata 中。

`locator` 使用带 `schema_version` 和 `locator_type` 的判别联合 Schema，不接受任意无约束 JSON。不同类型的必填定位如下：

- 文档文本：`document_version_id`、`chunk_id`、页码范围、标题路径，可选 bbox；
- 图片或表格：`document_version_id`、`asset_id`、页码和 bbox；
- bbox：明确坐标原点、页面宽高和 `[x0, y0, x1, y1]`，禁止混用像素与归一化坐标；
- 网页：`source_item_id`、`snapshot_file_id`、段落或字符范围；
- SQL：`query_run_id`、数据连接、允许表、列名、返回行范围和 schema snapshot hash；
- 新闻、政策、招投标或股票：`source_item_id`、领域明细 ID 和来源版本。

每种 `kind` 的 Pydantic/OpenAPI Schema 必须冻结必填、可空和禁止字段。例如文档 Evidence 必须具有有效 `document_version_id`；网页 Evidence 必须具有来源快照；SQL Evidence 不得只保存模型生成的 SQL 文本；图片 Evidence 必须定位真实私有资产。

Evidence 可以引用 MinIO 中的私有来源快照，但只能保存 object key 关联，不能保存长期公开 URL。Canonical URL 用于标识来源，快照与 content hash 用于证明回答时实际看到的版本；来源更新必须创建新版本，不能静默改写旧 Evidence。

Evidence 状态至少包括 `active`、`superseded`、`tombstoned` 和 `unavailable`。来源被删除、权限收回或许可不再允许展示时，历史 Citation 保留最小审计关系和失效原因，但不得继续返回敏感 excerpt、私有对象或可访问签名 URL。

### Message Citation

`message_citations` 连接 Message 与 Evidence，并保存：

- Citation 顺序；
- 对应的 Evidence；
- Citation 支持的 Claim。

### Research Claim

Research 使用 `research_claims` 和 `claim_evidence` 表达结论、置信度、核验状态，以及 Evidence 对 Claim 的支持或反驳关系。

`claim_evidence.relation` 只接受 `supports`、`refutes` 或 `context`，并保存核验状态和排序依据。报告中的每个关键 Claim 至少关联一项当前用户有权访问的 Evidence；证据不足或冲突未解决时必须明确标记，而不是生成伪确定结论。

### 证据图与图表

`graph_nodes` 和 `graph_edges` 是 Research 结果的可重建派生视图：

- 节点类型至少包括 Claim、Evidence 和 Entity，并保存对应正式资源 ID；
- 边只从已经持久化并授权的 Claim/Evidence/Entity 关系生成，关系类型受控；
- 每个图都绑定 `workspace_id` 和 `research_run_id`，查询时重新执行 Workspace 授权；
- 图中的标签或属性来自受消毒摘要，不直接嵌入全文、Secret 或长期对象 URL；
- 删除或失效 Evidence 后，相关节点/边进入失效或重建流程，不能继续显示为有效支持。

`chart_specs` 保存经过版本化 JSON Schema 校验的 ECharts 配置和其数据来源 ID。允许的图表类型、系列、轴、标题和数据量采用 allowlist；禁止函数、脚本、任意 HTML、外部 JavaScript、外部图片 URL 和事件处理器。模型输出只能作为待校验候选，校验失败必须返回稳定错误，前端不能直接执行模型生成的 JavaScript。

### 权限与可信边界

所有租户 Evidence 都必须具有 Workspace 边界。

读取 Evidence、来源快照或签名 URL 前必须重新检查当前用户的 Workspace 权限。

外部文档、网页、SQL 结果和模型生成内容仍然是不可信输入。成为 Evidence 不会使其获得系统权限。

SQL 结果、来源快照和 excerpt 必须按数据连接与 Workspace 的敏感等级最小化保存。读取 Citation 时重新授权 Evidence 及其底层资源；只检查 Citation 自身的 `workspace_id` 不足以授权对象、文档或 Query Run。

## 结果

### 收益

- 聊天、RAG、Text2SQL、行业数据和 Research 共享一套引用模型；
- 前端可以使用统一引用组件展示文本、页码、图片、表格、网页和 SQL 来源；
- Citation precision、Citation recall 和可解析率可以统一评测；
- Research Report 的关键 Claim 可以追溯到具体 Evidence；
- 报告、证据图和图表共享同一组已授权来源关系；
- 来源快照和 content hash 便于检查来源变化；
- 权限、删除和审计规则可以集中实现。

### 代价与风险

- `locator` 和 `metadata` 必须有正式 Schema 和版本策略，不能演变成无约束 JSON；
- 不同来源的定位能力不同，Adapter 必须转换为统一语义；
- 来源内容变化时需要区分 canonical URL、快照和内容哈希；
- Evidence 删除或失效时，历史 Citation 需要明确状态，不能静默指向错误内容；
- 多模态资产需要额外关联文件、页码和 bbox。
- 严格判别联合 Schema 增加 migration、Adapter 转换和前后端契约维护成本；
- Evidence 失效与隐私删除需要在“保留审计关系”和“删除敏感内容”之间建立明确生命周期。

## 否决方案

### 每个来源建立独立 Citation 系统

否决原因：会复制权限、展示、审计和评测逻辑，并让跨来源 Research 无法形成统一证据图。

### 只在回答文本中输出来源编号

否决原因：编号没有稳定资源关系，无法验证、重新授权、删除或反查来源。

### 只保存 URL

否决原因：URL 内容可能变化、失效或需要授权，也不能表达 PDF 页码、bbox、表格和 SQL 行范围。

### 让模型自由生成引用

否决原因：模型可能产生不存在或无法解析的来源。所有 Citation 必须关联系统中真实存在且已授权的 Evidence。

## 验证

- 每个 Citation 都能反查到真实 Evidence；
- Evidence 能定位到文档版本、页码、bbox、Chunk、资产、网页段落或 SQL 范围；
- 每种 `kind` 缺少必填 locator 字段或出现禁止字段时，Schema 校验失败；
- 生成后验证每个引用存在，禁止伪造来源；
- 用户 A 无法读取用户 B Workspace 的 Evidence 或签名 URL；
- 删除、重建索引和来源变化后，Citation 状态仍可解释；
- 失效 Evidence 不再返回 excerpt 或签名 URL，但历史 Citation 能说明失效原因；
- 每个关键 Research Claim 都有关联 Evidence，证据图中的节点和边能反查正式资源；
- 含函数、脚本、外链或超预算数据的 ECharts spec 被拒绝；
- 小型评测集的 Citation 可解析率达到 100%；
- 无答案问题证据不足时必须拒答。

## 变更与回滚

Evidence Schema 或 locator 语义变化必须通过版本化迁移和契约测试完成。

在新版 locator 完成数据迁移和前端兼容前，不能删除旧版解析逻辑。迁移完成后必须移除旧正式链路，禁止长期保留两套 Citation 系统。
