# 本机完整 Docker 部署验证

验证日期：2026-09-18。范围是单机项目展示部署，不是公网生产验收。

## 环境与隔离

- Windows / PowerShell，Docker Desktop Linux containers，Compose v5.1.4。
- 全新 `sec-filing-local` project 和六个独立数据 volumes；未复用开发数据库，也未删除已有开发服务。
- 后端、前端均从本次工作树构建；不是已上传公共仓库的发行镜像。
- 仅 `127.0.0.1:8443` 映射到宿主机；API、数据库、Redis、MinIO、Milvus 和 Elasticsearch 无宿主机端口。
- 根 `.env` 仅作为显式导入的读取来源，密钥未写入镜像、README 或截图。

## 实际执行

| 检查 | 已取得的证据 |
| --- | --- |
| 镜像构建与初始化 | Python / Web 镜像构建；随机凭据与本地证书；Alembic 到 head；私有桶初始化 |
| 完整应用启动 | API、Worker、Dispatcher、Reconciler、Beat、HTTPS 网关与五项存储/检索依赖实际运行 |
| 浏览器身份 | 注册 → 默认 Workspace → HTTPS Cookie → 刷新 / 再登录 |
| 私有上传 | 浏览器签名 POST 经网关到 MinIO，正式 complete API 后进入 Knowledge ingestion |
| 真实后台处理 | Worker 执行 Job，写入 Milvus 和 Elasticsearch，文档变为可用 |
| 实时 SEC | 官方 submissions 查询；Apple 2022 / 2023 / 2024 年报导入与 XBRL 输入补齐 |
| 真实模型研究 | Research `3504212e-73a9-568e-abc1-5a356a5eaf03` 完成，结果显示已核验 |
| 结果层 | 3 个财年，4 个指标卡 / 柱状图、杜邦分解、12 行输入与计算表；来源可反查 |
| 普通 Agent / SSE | 真实模型回答通过网关流式返回，不关闭预算或绕过 Runtime |
| 私有下载 | 签名 GET 字节与原上传文件一致；移除签名后 HTTP 403 |
| 整套停止 / 启动 | `-Action stop` 后 `-NoBuild` 启动成功；`restore` 不重新登录即读取原会话、报告、图表、Evidence 和文档 |

本次三年研究的 Agent Run：`aaf9b8ad-28c0-5c9b-bcda-8b14a76d99b7`。后台日志记录 `completed / final`，输入 37,987 Token、输出 8,726 Token。Provider 路由价格为 0 的日志不代表调用免费；本记录不把它解释为真实费用。

## 在验证中修复的问题

1. 容器内部 MinIO 地址不能直接给浏览器：存储访问与公开签名客户端分离，保持签名 Host 和 URI。
2. Nginx 对桶根路径自动 301 会把 POST 改为 GET：完整匹配桶根路径，签名上传不再重定向。
3. Web 容器构建遗漏根 TypeScript 配置：补齐 `tsconfig.base.json`，使用同一 frozen lockfile 构建。
4. 未来的 SEC 截止时间原本返回 500：HTTP schema 提前校验并返回 422；不可见的历史来源不能被 UI 显示为普通空结果。
5. 界面中的开发阶段标签移除，使用 Research / Evidence / Memory 功能名称。

截图见[真实运行图片](assets/screenshots/README.md)。自动化验证入口为 `infra/local/smoke.mjs`；它不拦截业务接口或手工修改数据库。

## 边界

本地质量检查：Python `1696 passed, 109 skipped`（未启用的独立集成测试明确跳过，实际部署链路由上述浏览器另行验证）；前端 `100 passed`；Ruff、mypy（595 个源文件）、ESLint、TypeScript、Prettier 通过；OpenAPI 重新生成无差异；Semgrep 0 findings；本次变更及新文本的脱敏 Gitleaks 扫描无发现。尚未提交或运行本次远端 CI。

这次执行不是重新跑全部 50 个稳定性案例或 12 个恢复场景。原有[稳定性报告](../evals/reports/product-stability-v1.md)、[工程验收](runbooks/sec-release-acceptance.md)和[完整发布判定](../evals/reports/sec-release-readiness-v1.md)各自保留证据边界。Bash 启动脚本与 Linux 容器镜像不等同于已经在 macOS / Linux 宿主机做过完整实测。
