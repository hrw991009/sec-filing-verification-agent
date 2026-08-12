# API Contract

该包保存后端 HTTP API 的版本化 OpenAPI 文档、生成的 TypeScript 类型和统一客户端。

数据流只有一个方向：

`FastAPI 路由与 Pydantic 模型 → openapi.json → src/schema.d.ts → typed client`

`openapi.json` 和 `src/schema.d.ts` 都是生成物，不能手工维护。修改后端 HTTP
契约后，在仓库根目录运行 `pnpm run api:generate`。`pnpm run api:check` 会重新生成
两份文件并检查 Git 差异，CI 也执行相同的漂移检查。

业务代码通过 `createIndustryPlatformApiClient()` 创建客户端。它默认发送浏览器凭据，
调用路径、请求体和响应体类型全部来自生成的 OpenAPI 类型，不需要另写 DTO。
