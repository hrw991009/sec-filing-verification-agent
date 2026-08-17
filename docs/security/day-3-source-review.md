# Day 3 真实来源、使用边界与安全复核

> 复核日期：2026-08-17
>
> 适用范围：Day 3 第 3 个可验收步骤中的新闻、政策、招投标与股票 Provider；这不是法律意见，部署方仍须按实际商业模式取得必要授权。

## 1. 来源与启用结论

| 领域 | 固定 Provider / 接口 | 官方边界 | 当前启用规则 |
|---|---|---|---|
| 新闻 | World Bank News `GET https://search.worldbank.org/api/v2/news` | [World Bank API/站点条款](https://www.worldbank.org/ext/en/legal/terms-conditions)说明：非数据集 Materials 的 API 使用受非商业用途、合理请求量和署名等条件限制；新闻端点也可在[官方 Alerts 页面](https://alerts.worldbank.org/zh-hans/taxonomy/term/782)反查 | 默认 `terms_approval_required`，只有部署方完成用途复核并显式设置 `WORLD_BANK_NEWS_TERMS_APPROVED=true` 后才允许发网；不以“公开可访问”推导“可商业再分发” |
| 政策 | FederalRegister.gov `GET https://www.federalregister.gov/api/v1/documents.json` | 官方 API 无需密钥并面向机器读取；FederalRegister.gov 同时明确其网页/XML 展示不是正式法律版本，法律依赖应回查链接的 govinfo 正式版本 | 可读取有界元数据；保留 document number、agency、原链接与发布时间，不宣称网页结果构成法律通知或法律意见 |
| 招投标 | TED `POST https://api.ted.europa.eu/v3/notices/search` | [Search API](https://docs.ted.europa.eu/api/latest/search.html)允许匿名检索已发布公告；[TED 法律声明](https://ted.europa.eu/en/legal-notice)说明公告通常可商业或非商业复用，编辑内容为 CC BY 4.0、metadata 为 CC0，并保留第三方权利与署名例外 | 当前真实无密钥来源；仅请求五个必要字段，保存 publication number、地区、类型、原链接和有界摘要；产品展示仍须提供适当来源标识 |
| 股票 | Alpha Vantage `GET https://www.alphavantage.co/query?function=GLOBAL_QUOTE` | [官方条款](https://www.alphavantage.co/terms_of_service/)的默认许可为个人、非商业使用，除非另有书面约定；取得 API key 本身不等于取得商业使用权 | 必须同时存在 `ALPHA_VANTAGE_API_KEY` 和 `ALPHA_VANTAGE_TERMS_APPROVED=true`；缺 key 返回 `provider_not_configured`，有 key 但未完成用途复核返回 `provider_terms_approval_required`，绝不回退 Demo/Mock 行情 |

四个 Adapter 都没有新增 Python 或 Node 运行时依赖，复用现有 `httpx2`、Pydantic、SQLAlchemy、Celery 与受控公网 egress，因此本步没有新增许可证包或供应链例外。

## 2. 真实合同验证

2026-08-17 只用无敏感信息的最小查询做过一次人工连通/形状核对：

- World Bank News 返回分页新闻 metadata；
- Federal Register 返回 `count/total_pages/results`，结果包含 document number、agency、publication date 与 HTML link；
- TED v3 在 `checkQuerySyntax=false` 下接受受限 Expert Search，并返回 notice title、publication number/date、buyer country、notice type 与 HTML link；
- Alpha Vantage 官方 `demo` key 的 IBM `GLOBAL_QUOTE` 返回公开示例形状；Demo 只用于合同核对，没有写入生产配置，也不是生产 fallback；
- GDELT 当时返回 HTTP 429，因此没有把它伪装成可用新闻来源，新闻合同改用能在官方页面反查的 World Bank endpoint，并继续受上述用途开关约束。

CI 不依赖公网稳定性。自动测试使用冻结的官方响应形状，通过真正的 Provider parser、Registry/Executor 与 Observation normalizer；“冻结响应可重复”与“线上 Provider 当前可用”分别记录，不能相互冒充。

## 3. 网络与数据安全边界

- Provider registry 是四项固定 allowlist，不能由模型传 URL、模块名、host、API key 或授权标志；
- 所有请求复用 `PublicEgressTransport`：解析并固定公网地址，拒绝 localhost、私网、metadata、异常地址与 DNS rebinding；Provider 层再禁止 redirect；
- JSON 响应执行精确 Content-Type、解压后字节上限、超时、重复键拒绝和严格字段/类型/长度校验；异常只映射稳定 error code，不保存或返回上游正文；
- API key 只从 Pydantic `SecretStr` 进入请求边界，不进入 `repr`、Event、数据库、Trace、Source Item 或 locator；
- source locator 只能来自固定 Provider 的受信解析器，归一为 allowlisted HTTPS host，并拒绝 userinfo、query、fragment、反斜杠和控制字符；
- Source Item 只保存必要 metadata、有界摘要、原链接、发布时间、采集时间、Provider/version、使用约束和内容 hash，不保存完整上游页面或原始响应；
- external ID 与规范化内容 hash 双重去重；每次重复仍写入 `collection_run_items` 的明确 disposition，避免静默丢失采集事实。

## 4. 尚未关闭的产品边界

- 当前只完成后端 Provider、Tool、采集、readiness 和授权 API；行业页面、专用政策/招投标/行情卡片、错误 UI 与 Tool Inspector 属第 5 步；
- World Bank News 和 Alpha Vantage 未取得相应部署用途批准时保持 fail-closed，不能为了演示把 readiness 改成 ready；
- 当前 Source Item 是可追溯 Observation/EvidenceCandidate，不是 Day 4 的 Evidence；后续 Claim/Citation 仍须经过授权、许可、去重和 locator 可解析性校验；
- 若 Provider 条款、endpoint 或字段发生变化，必须更新 Provider version、重新复核本文件并重跑合同与安全测试，不能静默兼容未知响应。
