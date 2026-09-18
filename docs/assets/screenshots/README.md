# 实际运行截图

采集日期：2026-09-18。由 Playwright 直接访问本机 `https://localhost:8443`，浏览器操作经过 Nginx → FastAPI → 正式 Runtime / Worker，无业务 API 拦截、无前端注入结果、无数据库手工造数。

- 环境：Windows + Docker Desktop Linux containers，独立 `sec-filing-local` Compose project。
- 数据：实时访问 SEC 官方 submissions、2022–2024 Apple 10-K 和 XBRL；不是受控 fixture。
- 模型：沿已配置的真实 OpenAI-compatible Provider 执行；不是 Fake / Replay Provider。
- 研究：`3504212e-73a9-568e-abc1-5a356a5eaf03`，三年杜邦分析，真实结果页显示已核验。
- “财务核验方法说明”是测试上传的研究方法笔记，不是财务来源；年报与数值来自 SEC。
- 截图只包含本次生成的演示账户和公开财务数据，不包含 API Key、私钥或登录凭据。

| 图片 | 内容 |
| --- | --- |
| `sec-workspace.png` | SEC 工作台、真实申报范围与年报导入 |
| `dupont-results.png` | 直接截取结果区域：指标卡、四组柱状图、杜邦分解与输入/结果表 |
| `research-workbench.png` | Research 页面与正式结果 |
| `knowledge-workspace.png` | 真实私有文档与 SEC 年报索引 |
| `agent-workspace.png` | 真实模型回答与附件 |
| `evidence-detail.png` | 真实计算 Evidence 的公式、来源定位和反向 Trace |

结果区域截图较长是因为保留完整计算表格，没有拼接或修改显示值。`../hero.svg` 为装饰性矢量横幅，不是软件运行截图或性能证明。

## 重新采集

先启动完整应用并配置真实模型与 SEC 身份，再安装开发依赖和 Playwright Chromium：

```powershell
pnpm install --frozen-lockfile
pnpm exec playwright install chromium
node infra/local/smoke.mjs setup
node infra/local/smoke.mjs research
node infra/local/smoke.mjs chat
node infra/local/smoke.mjs capture
```

查看已经创建的演示账户和研究结果，可运行 `node infra/local/open-demo.mjs` 打开可见浏览器；关闭窗口即结束，不会创建新研究。

脚本创建独立演示账户，登录凭据和浏览器会话只保存在 Git 忽略的 `.data/local/` 中。它会真实调用 SEC 和模型，可能产生费用；截图会覆盖同名展示图片。`capture` / `restore` 读取已有结果，不重新发起 Research。

SEC 来源有获取时点约束；旧截止时间无法读取今天才获取的来源版本时，等待来源快照可见后，用新的截止时间重试查询。截图只证明这次具体运行，不替代完整质量、稳定性、恢复或发布验收。
