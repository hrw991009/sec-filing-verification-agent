# 本机 Docker 部署

这是完整应用的源码构建部署，不是只启动数据库的开发 Compose，也不是已经推送到镜像仓库的安装包。

## 准备

- 安装 Git 和 Docker Desktop，启动 Docker 并使用 Linux containers。
- 使用较新的 Docker Compose v2 或 v5；配置使用 `!reset`。本次实测版本为 v5.1.4。
- 建议给 Docker 预留 4 核、12 GB 内存和至少 20 GB 可用磁盘；这是包含双检索引擎的开发资源建议，不是经过压测的最低配置。
- 首次构建需要访问镜像仓库、Debian、PyPI 与 npm。正常分析还需要访问模型服务和 SEC。

## 一条命令启动

仓库根目录，Windows PowerShell：

```powershell
.\start-local.ps1
```

若 PowerShell 的脚本策略阻止运行，可仅为这次进程指定策略，不修改系统长期设置：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-local.ps1
```

Linux / macOS：

```bash
bash start-local.sh
```

打开 **https://localhost:8443**。首次会提示自签名证书，请核对是本机 localhost 后继续。不要把它作为公网证书使用，也不必把本项目证书安装为系统根证书。

脚本顺序是：构建后端 → 初始化配置和 TLS → 构建 Web → 启动依赖 → 数据库迁移和私有桶初始化 → 启动应用进程 → 等待就绪。一次性任务 `migrate`、`minio-init` 显示 `Exited (0)` 属于正常状态。

首次运行不导入旧 `.env`，也不共用开发数据库。新用户需要注册；不存在默认管理员密码。

## 模型与 SEC 配置

启动后，编辑 `.data/local/provider.env`。模型三项必须同时配置，URL 使用你的 OpenAI-compatible 服务地址；下列名称是占位示例，不能直接执行研究：

```dotenv
AGENT_MODEL_PROVIDER_BASE_URL='https://your-provider.example/v1'
AGENT_MODEL_PROVIDER_API_KEY='replace-with-your-key'
AGENT_MODEL_ROUTE_JSON='{"model":"openai-compatible/your-model","upstream_model":"your-model","response_models":["your-model"],"pricing_version":"local-unpriced-v1","input_micro_usd_per_million":0,"cached_input_micro_usd_per_million":0,"output_micro_usd_per_million":0,"supports_image_input":false,"reasoning_enabled":false,"temperature":0.0}'
SEC_USER_AGENT_APP='SecFilingVerificationAgent/0.1'
SEC_USER_AGENT_EMAIL='your-real-contact-address@example.com'
```

`response_models` 应包含 Provider 实际返回的模型 ID。示例价格为“未计价”的 0，并不表示服务免费；按 token 计费时填写真实价格，按次计费时不要将 token 估算展示为实际账单。模型仍受步骤、Token 和超时预算约束。

保存后重新运行，应用容器会按新配置重建：

```powershell
.\start-local.ps1 -NoBuild
```

已有根目录 `.env` 时可显式导入白名单：

```powershell
.\start-local.ps1 -ImportEnv .env
```

只导入模型 URL / Key / Route / 请求超时、SEC 应用名和邮箱，不导入认证密钥、存储密码或旧服务地址。原 `.env` 不被修改。重复指定 `-ImportEnv` 会重新覆盖本地 provider 配置，因此日常改 `.data/local/provider.env` 后无需再导入。

没有模型配置时可打开 UI、注册和使用不依赖模型的功能；Agent 会明确返回 provider 未配置，不会回退到 Fake Provider。

## 第一次分析

1. 注册账户，进入“知识库”，创建一个研究档案库。
2. 进入 SEC 工作台。Apple 的 CIK 是 `0000320193`；选择 `10-K`、报告期范围和截止时间，查询后选择目标 accession。
3. “锁定并导入”后等待可检索。文本与 XBRL、原始快照和索引均沿正式入库链写入。
4. 在 Research 选择“SEC Filing” → “多年度杜邦分析工作流”，选择知识库、CIK、最新分析年报的报告期和 3–5 个分析年度。
5. 点击“补齐杜邦分析数据”，确认齐备，再填写问题与已确认范围，启动 Research。
6. 在结果中查看指标卡、柱状图、杜邦分解和表格，点击来源反查 Evidence。

实时下载到的来源版本不能用于证明其获取之前的历史可见性。首次使用请以当前时间附近的截止时间查询；若报告“来源版本在截止时点不可见”，保留这项限制，使用较新的截止时间或已有历史快照，不要把不可见数据强行算入。

## 服务与持久化

| 组成 | 用途 | 宿主机端口 |
| --- | --- | --- |
| Web / Nginx | 静态页面、HTTPS、API / SSE / 签名对象代理 | 仅 `127.0.0.1:8443` |
| API | 身份、Workspace、业务接口 | 不发布 |
| Worker / Dispatcher / Reconciler / Beat | 执行、投递、恢复对账和调度 | 不发布 |
| PostgreSQL / Redis | 业务事实、会话与任务基础设施 | 不发布 |
| MinIO | 私有文件、SEC 快照 | 不发布，签名请求经 HTTPS 网关 |
| Milvus / etcd / Elasticsearch | 双通道检索 | 不发布 |

项目名固定为 `sec-filing-local`，不会复用 `industry-intelligence-platform` 的开发 volumes。

`.data/local/secrets.env` 保存随机数据库密码、相互独立的 HMAC/AEAD 密钥和 Ed25519 私钥；`provider.env` 保存外部配置；`runtime.env` 是启动时生成的组合配置；`certs/` 保存本地证书。目录已被 Git 忽略，Docker 构建上下文不包含这些内容。不要公开它们，也不要在有数据的情况下重新生成密钥来排错。

数据库和对象内容保存在 Docker volumes，备份必须同时考虑这些 volumes 与私有配置目录。仅复制镜像不能复制账户、文档和结果。

## 停止、重启与更新

```powershell
.\start-local.ps1 -Action status
.\start-local.ps1 -Action logs
.\start-local.ps1 -Action stop
.\start-local.ps1 -NoBuild
```

Bash 对应：`bash start-local.sh status`、`logs`、`stop`、`--no-build`。

代码更新后运行不带 `-NoBuild` 的启动命令，重新构建前后端并迁移数据库。更新前备份数据；这不是自动安全回滚方案，正式回滚遵循[恢复 Runbook](runbooks/sec-release-acceptance.md)。

**停止不会删除数据。不要为了重启使用 `down --volumes`；不要删除 `.data/local`。** 本次没有向 Docker Hub / GHCR 推送镜像；其他用户需要 clone 源码构建，不能直接凭你的本地 image ID 下载。

## 排错

- **8443 被占用**：先确认占用者。不要只改映射端口；浏览器可信 Origin、公开 MinIO 签名地址和证书必须保持一致。
- **下载镜像/依赖失败**：检查 Docker Desktop 代理与网络后重跑；构建缓存会复用，不需要清空 volumes。
- **服务不健康**：使用 `-Action status` 和 `-Action logs`。数据库迁移、Milvus 或 Elasticsearch 未就绪时，不应跳过依赖检查强行启动。
- **SEC 查不到数据**：检查 Form、报告期和截止时间；实时源受请求身份、网络、限流和 point-in-time 约束，不代表该公司没有年报。
- **模型报错**：检查 URL、Key、路由、实际响应模型名和 Provider 可用性。配置更改后需要重新启动应用容器。
- **证书到期**：自动生成证书有效期一年。停止应用后备份并更新 `certs/localhost.crt` 和匹配的 key；不要删除 `secrets.env`。

受控来源演示可显式运行 `.\start-local.ps1 -Demo`（Bash：`--demo`），使用仓库的固定 SEC 样本，模型仍然是真实 Provider。该样本不是完整实时历史库，不能据此承诺 3–5 年数据齐全，也不等于 live SEC 验证。下次不加 `-Demo` 即恢复实时来源。

本配置面向单机开发和项目展示：没有公网入口、可信公网证书、生产备份调度或集群高可用。不要直接把监听地址改为 `0.0.0.0` 当作生产部署。
