# Day 2 第三方依赖与使用边界复核

> 复核日期：2026-08-15
>
> 范围：Day 2 新增的 Model Provider HTTP、私有附件和图片校验依赖，以及外部模型使用边界。

## 1. 版本与许可证

下表依据当前 `uv.lock` 和已安装 wheel 的发行元数据核对；仓库没有复制这些项目的源代码，也没有修改后再分发其源码。

| 依赖 | 锁定版本 | 用途 | 发行元数据许可证 | 处置 |
|---|---:|---|---|---|
| `httpx2` | 2.9.1 | OpenAI-compatible HTTP/SSE 与测试 transport | BSD-3-Clause | 允许作为独立库使用；保留锁文件中的来源与 hash |
| `minio` Python SDK | 7.2.20 | 私有对象的预签名上传、读取、下载和删除 | Apache-2.0 | 允许作为 Adapter 依赖；不得把 Access Key、Secret 或签名 URL 写入日志 |
| Pillow | 12.3.0 | JPEG/PNG/WebP 的真实解码、尺寸/帧数检查与安全重编码 | MIT-CMU | 允许作为 Parser Adapter 依赖；不复制测试图片以外的第三方素材 |

Compose 中的 MinIO Server 与 `mc` 使用固定镜像并作为独立基础设施进程运行。它们不是链接进应用的库；在对外分发镜像、修改镜像或提供托管服务前，项目所有者仍须按对应镜像版本复核 AGPL-3.0 的源代码提供、NOTICE 和网络服务义务。本轮没有修改或重新发布 MinIO 镜像。

## 2. 外部模型与数据

- 项目只实现 Provider-neutral Port 和全新编写的 OpenAI-compatible wire Adapter，没有引入供应商 SDK，也没有复制参考仓 Provider 实现或配置；
- `.env` 默认可以不配置真实模型。未配置时正式链路稳定失败，不会调用外网，也不会改用 Fake；
- 启用任何真实 Provider 前，项目所有者必须复核该 Provider 的 API/模型许可、价格、输入保留、训练使用、区域传输、图片输入和删除条款；
- 用户问题、附件和模型输出均可能包含企业数据。没有完成上述条款和隐私复核时，不得把生产数据发送给 Provider；
- Harness 的冻结 fixture 和 Trace snapshot 是本项目自建的合成数据，不含真实用户问题、答案、Secret、附件原文或原始 chain-of-thought。

## 3. 前端素材

Day 2 工作台的布局、CSS、图标和状态组件均在本仓独立实现，没有新增来源不明的图片、字体或模板。用户上传的文件保持用户所有，不转化为项目可再分发素材。

## 4. 威胁与隐私复核

| 威胁 | 当前控制 | 自动化证据 | 剩余边界 |
|---|---|---|---|
| 跨 Workspace 读取 Run、Trace、消息或附件 | HTTP 先构造可信 `WorkspaceScope`，SQL 同时按资源 ID 和 Workspace 过滤，组合外键阻止跨租户关联 | Conversation/File/Trace API 负向测试、PostgreSQL 组合外键与跨 Workspace 集成测试 | Day 3 以后新增 Tool/数据源时必须重新做同样授权 |
| Runtime Context、Token、Provider Secret 或其他 Workspace 数据进入模型/Trace | Context Compiler 只接受显式安全投影；manifest/Trace 只存版本、引用、预算和脱敏摘要 | `test_context.py`、`test_trace_api.py`、Harness snapshot 脱敏测试和 Gitleaks | 启用真实 Provider 前仍需复核供应商保留与训练条款 |
| 附件伪类型、图片炸弹、路径文件名或 metadata 泄漏 | 服务端随机 object key；有界读取；核对大小/hash/MIME/magic；Pillow 真解码、像素/帧限制并重编码去 metadata | `test_parser.py`、真实 MinIO 生命周期和匿名访问拒绝测试 | Day 5 新增 PDF/OCR 时必须单独做 parser sandbox/资源预算复核 |
| 附件或摘要中的 Prompt injection 获得更高指令级别 | system instructions 独立；附件和摘要始终作为带“不可信数据”边界的 USER 内容，不能修改 capability、预算或 Workspace | Context Compiler 的角色、顺序、注入文本和跨 Workspace 负向测试 | L0 不包含 Tool；Day 3 Tool 输入仍需独立 schema/allowlist |
| 模型 Markdown 执行脚本或危险链接 | 前端使用结构 allowlist 和协议检查，不使用 `dangerouslySetInnerHTML`；危险 URL 退化成普通文本 | `SafeMarkdown.test.tsx` 与工作台 XSS/恶意链接测试 | 未来 Citation/富媒体组件需继续走 typed renderer，不能拼 HTML |
| Provider URL 访问内网、metadata 或受环境代理影响 | 服务端固定 HTTPS DNS hostname；统一 public-egress transport 校验 IDN、全部 A/AAAA、全局地址和实际 peer，禁代理、跳转与 IP literal | `test_public_egress.py` 和 OpenAI-compatible Adapter URL/transport 测试 | 正式部署仍需网络层 egress deny；这是部署纵深防线，不由本地代码测试冒充 |
| 取消、断线或 Worker 中断让 Run 永久运行 | 取消是持久事实；Runtime 可在首个 delta 前关闭 stream；Terminalizer/Reconciler 用行锁和唯一终态约束收敛；SSE 只读已提交 Event | Runtime 挂起取消测试、真实 PostgreSQL reliability/cancel 测试、浏览器停止与刷新旅程 | Day 5 才增加从 Checkpoint 恢复同一次 graph；Day 2 不伪装 resume |

日志只记录 Run/Job/Trace ID、状态、stop reason、耗时、Token 与费用等可聚合字段，不记录 prompt、回答、附件原文、签名 URL 或 Secret。当前前端 Access Token 仍只保存在内存；下载使用短期私有 URL。以上复核覆盖 Day 2 新增面，不能替代后续 Day 3～6 对 Tool、Memory、Knowledge/RAG、Evidence/Citation 的专项威胁建模。

## 5. 后续复核

依赖升级、替换 Provider、增加真实数据源、引入外部评测集或修改/分发基础设施镜像时，必须重新核对版本、许可证、使用条款和必要 NOTICE。Day 7 的供应链门禁还会统一执行许可证清单、NOTICE、依赖/镜像漏洞与来源归属检查；本文件只证明 Day 2 新增范围已经有明确的人工复核记录。
