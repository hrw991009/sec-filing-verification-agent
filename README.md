# Industry Intelligence Platform

面向行业研究与企业知识工作的多模态行业智能工作台。

当前状态：Day 1 工程地基建设中。身份、工作空间、基础设施和健康检查尚未实现，不能视为可用产品。

七天交付口径：高质量完成能力矩阵从两个参考项目映射出的每一项目标，并在 Day 7 全部标为 `complete`；七天版本不宣称生产级，本文档不展开七天以后的打磨计划。

## 执行基线

- [七天主计划](docs/master-plan.md)
- [产品范围说明](docs/product-scope.md)
- [七天目标能力矩阵](docs/feature-matrix.md)
- [系统架构说明与 ADR 索引](docs/architecture.md)
- [参考仓凭据暴露审计与待处置清单](docs/security/credential-exposure-audit.md)
- Python 3.13.14
- Node.js 24.16.0
- uv 0.11.32
- pnpm 10.10.0

## 当前可运行范围

目前可以运行 React 工程展示页、Python 包测试、前端组件测试和 Playwright Chromium 生产构建冒烟测试。

目前还不能启动正式 API、数据库或身份功能，因为 PostgreSQL/Redis/MinIO Compose、FastAPI、Alembic、Settings、健康检查、认证和 Workspace 尚未实现。README 会随着 Day 1 纵向切片继续更新；不能把下面的工程展示页当成业务产品已经完成。

安全前置门禁同样尚未完成：参考仓脱敏扫描发现待处置凭据候选。在[审计清单](docs/security/credential-exposure-audit.md)中的 Provider 侧吊销/轮换完成前，不得复制或启用参考仓 Provider 配置。

## 第一次安装

在项目根目录 `D:\industry_intelligence_platform` 执行：

```powershell
uv --version
node --version
pnpm --version
docker --version
docker compose version
```

版本应与“执行基线”一致。然后按照锁文件安装依赖：

```powershell
uv sync --locked --all-packages
pnpm install --frozen-lockfile
pnpm exec playwright install chromium
uv run python --version
```

`uv run python --version` 应显示 `Python 3.13.14`。这里不使用全局 `python --version` 判断项目解释器，因为 Conda 或系统 PATH 可能指向另一套 Python；`uv run` 会使用项目 `.venv` 与 `.python-version`。`--locked` 和 `--frozen-lockfile` 的作用是验证并使用仓库已经评审过的依赖版本，而不是在安装时悄悄更新锁文件。

## 运行前端

开发服务器支持热更新：

```powershell
pnpm run dev:web
```

终端会显示实际访问地址，默认通常是 `http://127.0.0.1:5173/`。保持该终端运行；按 `Ctrl+C` 停止。

验证生产构建：

```powershell
pnpm run build
pnpm run preview:web
```

默认预览地址通常是 `http://127.0.0.1:4173/`。不要双击 `apps/web/dist/index.html` 直接用 `file://` 打开：Vite 构建产物需要 HTTP Server 正确提供模块和资源路径。

## 质量检查

### Python

```powershell
uv sync --locked --all-packages
uv run --locked --all-packages ruff format --check --config pyproject.toml apps/backend
uv run --locked --all-packages ruff check --config pyproject.toml apps/backend
uv run --locked --all-packages mypy --config-file pyproject.toml
uv run --locked --all-packages pytest
uv build --package industry-platform-backend
uv audit --locked
```

### Web

```powershell
pnpm install --frozen-lockfile
pnpm run format:check
pnpm run lint
pnpm run typecheck
pnpm run test
pnpm run build
pnpm audit --audit-level high
pnpm run test:e2e
```

`pnpm run test:e2e` 会构建 Web、启动 Vite preview、让 Chromium 访问生产构建并在结束后关闭测试服务器。失败产物位于被 Git 忽略的 `playwright-report/` 和 `test-results/`。

### 密钥扫描

```powershell
gitleaks dir --redact --verbose .
gitleaks git --redact --verbose --log-opts='--all' .
```

第一条检查当前目录，第二条检查完整 Git 历史；两者不能互相替代。

## 常见问题

### `uv` 指向了临时 OpenLoomi 目录

先检查：

```powershell
Get-Command uv -All | Select-Object Name, Source
where.exe uv
uv --version
```

本项目预期使用当前用户稳定安装目录 `$env:USERPROFILE\.local\bin\uv.exe` 中的 0.11.32。若第一项不是它，应先修正 PowerShell 启动后的 PATH，再运行项目命令；不要在项目脚本中硬编码个人绝对路径。

### PowerShell 同时显示 Conda 和项目虚拟环境

uv 不要求激活环境。可以先执行 `deactivate` 和 `conda deactivate`，然后直接使用正常的 `uv ...` 命令。`.venv` 仍由 uv 管理。

### Playwright 提示没有浏览器

执行：

```powershell
pnpm exec playwright install chromium
```

### 如何判断当前工作区是否干净

```powershell
git status --short
git diff --check
```

不要在不了解目标文件的情况下使用 `git reset --hard` 或删除整个目录。当前生成的 `dist/`、测试报告、缓存和虚拟环境均由 `.gitignore` 排除。
