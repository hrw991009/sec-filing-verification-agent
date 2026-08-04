# Day 1 学习日志

## Python 质量门

### 1. Ruff、mypy、pytest 分别解决什么问题，为什么不能互相替代？

答：Ruff 检查代码格式与静态代码规则，mypy 检查参数、返回值、空值以及对象之间的类型关系，pytest 发现并运行项目配置选中的自动化测试（当前永久测试属于单元测试），三个分工不同，不可替代。

### 2. `print("debug")` 为什么被 T201 阻断？

答：后端代码规范中不能单独打印没有结构化字段的字符串，因为它可能泄露 Token 或密钥，也没有 info、warning、error 等日志级别。

### 3. `assert False` 如何证明 pytest 能阻断失败？

答：断言条件为 false 时，Python 会抛出 `AssertionError`；pytest 捕获它并把测试标记为失败，同时以非零退出码阻断质量门。

### 4. `uv.lock`、`uv sync --locked` 与 `uv sync --frozen` 有什么区别？

答：`uv.lock` 记录已经解析出的精确依赖版本、来源、哈希和平台条件。`uv sync --locked` 会先确认项目配置与锁文件一致，不一致就失败，然后按照锁文件同步环境。`uv sync --frozen` 直接使用已有锁文件而不更新它，但不会确认锁文件是否仍与项目配置一致。因此 CI 使用 `--locked` 作为一致性门禁。

### 5. 当前永久测试保障了什么，又没有保障什么？

答：当前永久测试主要防止源码版本号与发行包元数据发生漂移，同时证明包可以导入、发行元数据可以读取以及 pytest 测试链路能够运行。但它只验证包元数据一致性，不代表 API、数据库、权限、安全和其他业务逻辑已经得到测试，也不能替代构建、干净环境安装和跨平台 CI 测试。

## 已完成的失败探针

- Ruff 成功阻断了 `print("debug")`；
- mypy 成功阻断了声明返回 `int`、实际返回 `str` 的代码；
- pytest 成功阻断了 `assert False`；
- ESLint 成功阻断了不允许的 `console`；
- Vitest 成功发现了标题层级从 `h1` 变为 `h2`；
- Playwright 在浏览器测试失败时生成了截图、Trace 和报告。

## CI 验收记录

- GitHub Actions Run ID：`30797166192`；
- 分支：`day1/engineering-foundation`；
- Python quality：通过；
- Web quality：通过；
- Browser E2E：通过；
- Python dependency audit：通过；
- Node dependency audit：通过；
- Secret history：通过。

该结果证明现有工程门禁可以在 GitHub 的干净 Ubuntu 环境中复现，但不代表 Day 1 已经完成。数据库、迁移、身份、Workspace、真实健康检查和身份 E2E 仍未实现。

## 文档与安全基线验收记录

- 主计划已升为 `1.3.0`，只定义 Day 1～Day 7；不展开七天后的生产级打磨路线；
- 能力矩阵冻结 59 个目标行，每一行的 Day 7 目标状态均为 `complete`；当前状态仍按事实记录，不能把计划目标误写成已经实现；
- 产品范围、架构、功能矩阵、6 份 ADR、README 和参考仓凭据审计已完成本地链接、Markdown 表格、ADR 结构、一致性、安全与 `git diff --check` 检查；
- Python 的锁定安装、Ruff、mypy、pytest、build、依赖审计全部通过；Web 的锁定安装、Prettier、ESLint、TypeScript、Vitest、build、依赖审计和 Playwright 全部通过；
- 新项目当前目录和 6 个提交的完整历史再次通过 Gitleaks 脱敏扫描，发现数为 0；
- 两个参考仓的只读脱敏扫描发现需要项目所有者处置的候选，具体位置和不敏感处置表见 `docs/security/credential-exposure-audit.md`；
- 因 Provider 侧吊销/轮换证据尚未完成，D1-09 必须保持 `thin_slice`。这也说明“扫描执行完”不等于“安全问题处理完”。

本轮只修改文档，没有实现 FastAPI、数据库、身份或其他业务能力。因此，D1-10 文档基线可以完成，但 Day 1 整体仍未完成。

## 待本人用自己的话复盘

1. 为什么本地测试通过仍然需要 CI？
2. 为什么 GitHub Action 使用完整 commit SHA，而不是只写 `@main` 或 `@latest`？
3. 为什么每张租户业务表都需要 `workspace_id`？
4. 为什么 Refresh Token 不应放入 LocalStorage？
5. 为什么 Milvus 不是业务事实源？
6. 今天有没有为了赶进度加入特殊分支或第二套正式链路？
