# 参考仓凭据暴露审计

> 审计日期：2026-08-03
>
> 状态复核：2026-08-12
>
> 状态：D1-09 `thin_slice`；不阻断 Day 2 Agent 学习，阻断 Day 7 发布标签
>
> 安全规则：本文只记录文件、规则、提交与处置状态，不记录任何 Secret 原文

## 1. 为什么必须做这项审计

删除当前文件中的密钥，不会删除 Git 历史里的旧副本；旧值只要仍然有效，就可能被重新利用。因此 D1-09 的完成条件不是“新仓没有密钥”这一项，而是：新仓当前树和完整历史无泄漏，两个参考仓的候选全部完成真假判断，所有真实或无法确认的旧凭据均已在 Provider 侧吊销/轮换，并留下不含密钥的证据。

D1-09 是参考仓历史凭据的外部治理尾项，不否定已经通过的新仓 Day 1 工程与 Secret 门禁，也不阻断 Day 2 Agent 学习。在 6 组候选全部处置并复扫前，它仍阻断 Day 7 发布标签，且禁止复制、启用或以任何方式试用参考仓中的候选凭据和相关配置。

上述限制不禁止按干净接口独立实现 Adapter：可以基于新项目的 Provider-neutral Port、供应商公开文档和全新服务端配置自行编写实现，但不得复制参考仓实现或配置，不得把旧候选当作开发/测试凭据，并且仍须通过来源/许可证复核、当前树与完整历史 Secret 扫描和 Adapter 合同测试。

本次扫描全部使用 `--redact`，没有把候选值写入本项目文档、日志或回复。扫描是只读的，没有修改两个参考仓。

## 2. 扫描结果

| 扫描对象 | 范围 | 结果 | 结论 |
|---|---|---|---|
| 新项目 | 提交 `2c4e6e9` 的受控源码、配置与前端构建链路 | 分路径脱敏扫描无有效凭据；全量本地门禁通过 | 通过 |
| 新项目 | 截至提交 `2c4e6e9` 的完整 Git 历史、`--all` | 脱敏历史扫描无有效凭据 | 通过 |
| 新项目 | [CI 31578083339](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/31578083339) | Gitleaks、Python/Node 依赖审计及其余 Day 1 干净 CI 门禁通过 | 通过；不替代参考仓 Provider 侧处置 |
| R1 `D:\my_work_project` | 33 个 Git commits、`--all` | 2 个候选 | 未通过，必须处置 |
| R1 `D:\my_work_project` | 当前目录，约 683.74 MB | 87 个候选 | 未通过；包含真实项目候选和 `.venv` 第三方测试夹具噪声，必须分类 |
| R2 `D:\industry_information_assistant\industry_information_assistant` | 原生 Git 扫描 | 被历史中的 `__MACOSX/.../._test_doc.pdf` 中断 | 不能写“通过” |
| R2 同上 | 排除异常 AppleDouble 文件后的完整 patch stream | 2 个候选 | 未通过，必须处置 |
| R2 同上 | 当前目录，约 6.65 MB | 2 个候选 | 未通过，必须处置 |

## 3. 需要人工确认和轮换的候选

| ID | 参考仓位置 | 规则/现象 | 当前判断 | 必须完成的动作 | 状态 |
|---|---|---|---|---|---|
| SEC-R1-01 | `backend/service/core/retrieval2.py:9`，历史 commit `57919cc6fab0d44f69ba782d9f943b031500d49b` | `generic-api-key`，源码硬编码 | 高风险真实候选 | 确认 Provider；立即吊销/轮换；新项目不得复制；记录 Provider 侧处置证据编号 | open |
| SEC-R1-02 | `backend/.env` | KAFU、LLM、Embedding、DocMind 等 Access Key/Secret 候选 | 高风险本地凭据集合 | 逐项核对 Provider；真实或不确定值全部吊销/轮换；安全迁移到未提交的 Secret 配置 | open |
| SEC-R1-03 | `frontend/.env:2` | `VITE_*` Token 候选 | 高风险；前端变量会进入浏览器构建 | 立即吊销/轮换；以后只由服务端 Adapter 使用 Secret | open |
| SEC-R1-04 | `docs/chat_api.md:121`，历史 commit `cb621cb0e117d406a30c71bcf674ba94e19527fe` | curl Bearer 示例 | 真伪未知 | 在签发系统核对；无法证明是无效示例时按泄漏吊销 | open |
| SEC-R2-01 | `backend/app/service/config.py:22` | SERPER key 的源码默认值 | 高风险真实候选 | 吊销/轮换；删除源码 fallback，只允许服务端环境/Secret 注入 | open |
| SEC-R2-02 | `backend/app/service/dr_g.py:30` | DASHSCOPE key 的源码默认值 | 高风险真实候选 | 吊销/轮换；删除源码 fallback，只允许服务端环境/Secret 注入 | open |

R1 当前目录其余大量发现位于 `backend/.venv/Lib/site-packages/...` 的密码学测试向量、示例 key 或缓存文件，通常不是项目凭据，但必须在上述真实项目路径处置后再分类。只有逐条证明为第三方测试夹具，才可以用路径规则排除；不能先全局忽略 `generic-api-key` 或 `private-key` 规则来制造绿色结果。

## 4. 项目所有者的处置记录

项目所有者应在 Provider 控制台完成动作后，只填写不敏感证据。不要粘贴旧值、新值、Cookie、完整 Access Key ID 或控制台含密钥截图。

| 候选 ID | Provider/账号范围 | 处置动作 | 完成时间（UTC） | 不敏感证据编号或工单 | 复核人 | 状态 |
|---|---|---|---|---|---|---|
| SEC-R1-01 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | open |
| SEC-R1-02 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | open |
| SEC-R1-03 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | open |
| SEC-R1-04 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | open |
| SEC-R2-01 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | open |
| SEC-R2-02 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | open |

## 5. D1-09 门禁语义与解除条件

D1-09 保持 `thin_slice` 期间允许进入 Day 2 Agent 主线，也允许按第 1 节所述干净接口独立实现 Adapter；但不得复制或启用参考仓 Provider 配置，并且不得创建 Day 7 发布标签。

D1-09 只能从 `thin_slice` 改为 `complete`，当且仅当：

1. 上述 6 个候选全部标为 `rotated`、`revoked` 或有证据证明为从未有效的测试值；
2. 所有真实/不确定值已在 Provider 侧失效，而不只是从文件中删除；
3. 参考仓的项目文件与 Git 历史重新执行脱敏扫描，剩余发现均有逐项 allowlist 理由；
4. R2 使用能绕过异常 AppleDouble 文件但覆盖其余完整历史的扫描再次执行；
5. 新项目当前树、完整历史、CI、前端构建产物和日志仍为 0 个有效凭据；
6. 复核人确认文档没有保存任何 Secret 原文。

在这些条件完成前，D1-09 仍未完成：不得复制、启用或改写含候选凭据、默认值或历史配置的参考仓 Provider 代码；可以依据不含凭据的公开契约和新仓干净 Port 独立实现 Provider Adapter，但该实现不能作为关闭 D1-09 的证据。

## 6. 2026-08-12 状态复核

本轮 Day 1 代码已经增加本地随机密钥配置、Ed25519 Access Token、Gitleaks 与 Python/Node 依赖审计。提交 `2c4e6e9` 已通过全量本地门禁和 [CI 31578083339](https://github.com/hrw991009/industry-intelligence-platform/actions/runs/31578083339) 的干净环境验证，包括新仓当前受控源码、完整历史和前端构建链路的 Secret 检查；这些证据确认 D1-01～D1-08、D1-10～D1-12 的新仓工程门禁，不会自动吊销参考仓中的旧凭据。

上表 6 组候选仍全部为 `open`，项目所有者处置表没有 Provider 侧证据，因此 D1-09 必须继续保持 `thin_slice`。它不阻断 Day 2 Agent 学习，但在候选全部完成吊销/轮换、非敏感证据登记和复扫前继续阻断 Day 7 发布标签。完成外部处置后，应严格按第 5 节重新扫描并由复核人更新每一行状态；任何文档都不得保存旧值、新值或完整凭据截图。
