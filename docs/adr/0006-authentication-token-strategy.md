# ADR 0006：采用 Access/Refresh Token 鉴权方案

> 状态：已接受
>
> 日期：2026-08-03
>
> 修订日期：2026-08-12
>
> 依据：`docs/master-plan.md` v1.7.0 第 3.3、5.2～5.3、6.4～6.5、8～10、14、16～17 节

## 背景

系统需要支持注册、登录、当前用户查询、Refresh 轮换、Logout 和严格 Workspace 隔离。

浏览器中的长期凭据容易受到脚本读取、泄漏和重放风险。如果把 Refresh Token 放入 LocalStorage，任何成功执行的恶意脚本都可能读取它，并长期冒充用户。

同时，角色和 Workspace membership 可能变化，系统不能只信任前端或长期 Token 中携带的权限信息。

## 决定

采用短期 Access Token 与轮换 opaque Refresh Token 的鉴权方案。

### 密码

1. 用户密码使用 Argon2id 哈希，七天版本的初始参数为 memory cost 64 MiB、time cost 3、parallelism 1、salt 16 bytes、hash 32 bytes；参数必须能通过配置提高，但不能在生产配置中低于该基线。
2. 密码允许常见密码管理器生成的长密码与 Unicode，长度为 12～128 个字符，不增加会降低密码熵的强制字符组合规则。
3. 数据库不保存明文密码或可逆密码。
4. 登录成功时检查哈希参数；参数落后则在验证成功后重新哈希，不能要求用户修改原密码。
5. 日志、错误响应和审计元数据不能包含密码。
6. 登录按“来源 IP + 规范化账号摘要”限流；错误密码和不存在账号返回相同的状态、错误码和近似处理路径，降低账号枚举风险。

### 修改密码

1. 修改密码必须携带有效 Access Token，并再次验证当前密码；浏览器请求同时校验精确 Origin。错误当前密码返回稳定通用错误，不透露内部哈希状态。
2. 新密码必须满足同一 12～128 字符策略且不能等于当前密码。七天范围不保存可恢复的密码历史，也不记录明文或密码派生提示。
3. 更新密码哈希、写入 `password_changed_at`、撤销该用户全部 Refresh rotation family/Session 和写脱敏审计事件必须在同一 PostgreSQL 事务中完成。
4. 因服务端每次验证 Access Token 都检查 `sid`，Session 撤销后所有现有 Access Token 立即失效；响应清除 Refresh/CSRF Cookie，前端清除内存 Access Token，并要求用户用新密码重新登录。
5. 修改密码按用户与来源 IP 限流。当前密码错误、策略失败、数据库事务失败时不得部分撤销 Session 或部分更新哈希；重复提交具有明确冲突/重新认证语义。

### Access Token

1. Access Token 使用 Ed25519 签名的 JWS/JWT，算法固定为 `EdDSA`，有效期 10 分钟，允许的时钟偏差不超过 30 秒。
2. Header 只接受预期的 `typ=at+jwt`、受信任 `kid` 和固定算法；拒绝 `alg=none`、算法混淆，以及 Token 自带的 `jku`、`x5u` 或其他远程密钥地址。
3. Claims 至少包含固定的 `iss=industry-intelligence-platform`、`aud=industry-platform-api`、用户 UUID `sub`、Refresh Session UUID `sid`、唯一 `jti`、`iat`、`nbf` 和 `exp`。
4. Token 不携带供应商密钥，也不把 Workspace 角色当作最终授权事实。服务端验证签名与全部时间/issuer/audience claims 后，仍检查用户和 `sid` 未撤销，并根据当前 membership 授权 Workspace 资源。
5. 前端只在内存中保存 Access Token，不写入 LocalStorage、SessionStorage、IndexedDB、URL、日志或持久状态库。
6. 前端 API Client 对同时出现的 401 只允许一个 refresh 请求进行；同一页面内其他请求等待该结果，避免刷新风暴。
7. 供应商 Token 或共享 Bearer Secret 不能进入 Access Token、前端 `VITE_*` 或浏览器存储。

### Refresh Token

1. Refresh Token 使用 CSPRNG 生成至少 256 bits 熵的 opaque token。数据库保存使用独立服务端 pepper 计算的 HMAC-SHA-256 摘要，不保存明文 Token。
2. Refresh Token 通过名为 `__Host-iip_refresh` 的 Cookie 传递，属性固定为 `HttpOnly; Secure; SameSite=Strict; Path=/`，不得设置 `Domain`。设备绑定使用另一枚服务端随机生成的 `__Host-iip_device` Cookie，同样固定 `HttpOnly; Secure; SameSite=Strict; Path=/` 且不设置 `Domain`，数据库只保存其摘要；不得用 IP、User-Agent 或侵入式浏览器指纹充当稳定设备标识。
3. Refresh Session 具有 7 天空闲过期和 30 天 rotation family 绝对过期；每次成功 refresh 都签发新 Refresh Token，新的过期时间不能超过 family 绝对上限。
4. `refresh_sessions` 至少保存 token hash、user、创建/过期/使用/撤销时间、设备摘要、`rotation_family`、上一 Session、替代 Session、CSRF token hash，以及仅用于 5 秒响应丢失恢复的 successor recovery envelope/expiry。
5. Refresh 和 Logout 必须使用 POST，验证精确 Origin allowlist，并要求 `X-CSRF-Token` 与绑定当前 Session 的 `__Host-iip_csrf` Cookie 同值且哈希匹配；CSRF Cookie 使用 `Secure; SameSite=Strict; Path=/`、不设置 `Domain`，但不是 HttpOnly，以便前端读取后写入请求头。
6. Refresh 事务按 token hash 查询并对 Session 行加锁。一个旧 Token 只能创建一个 successor；创建新 Session、标记旧 Session 已使用、写替代关系，以及同时轮换 Refresh/CSRF Token 必须在同一事务完成，响应同时设置两枚新 Cookie。
7. 为处理“数据库已提交但 HTTP 响应丢失”，首次轮换把**同一个** successor Refresh Token 与 CSRF Token 放入 AEAD 加密的 recovery envelope，AAD 绑定 predecessor/successor/family/user/设备摘要，独立恢复密钥只从服务端 Secret 加载；数据库仍不保存明文，envelope 最多保留 5 秒并由清理任务置空。
8. 5 秒内旧 Token 再次到达时，服务端必须同时锁定 predecessor、successor 和 family current pointer；只有该旧 Token 仍是**当前且尚未使用 successor 的直接 predecessor**，并且旧 CSRF Cookie/Header、精确 Origin 和设备摘要都匹配，才可解密并重发同一个 successor Cookie及新的短期 Access Token，不得创建第二个 successor。若 successor 已经轮换/撤销、family current 已前移、请求 Token 是早于这个直接 predecessor 的 ancestor、绑定不匹配、超过 5 秒或 envelope 缺失/解密失败，均按重放撤销 family 并要求重新登录，绝不能把浏览器 Cookie 回滚到旧 successor。
9. 前端仍使用 refresh single-flight；收到恢复响应后重新读取最新 CSRF Cookie。并发响应无论先后都只写入相同 successor，不能用 `409` 假设第一个响应一定已经到达浏览器。
10. Logout 撤销对应 rotation family、使关联 `sid` 立即失效，并清除 Refresh、CSRF 与 device Cookie。重复 Logout 保持幂等。
11. 浏览器开发与 E2E 必须运行在支持 Secure Cookie 的可信本地 HTTPS 或浏览器认可的安全 localhost 环境；禁止为了方便在普通配置中关闭 `Secure`。纯单元测试可以使用隔离的测试 Client，但不能改变正式 Cookie 策略。

### Workspace 授权

1. 注册用户时，在同一受控业务流程中创建默认 Workspace 和 owner membership。
2. 角色至少包括 owner、admin、member 和 viewer。
3. 每次访问 Workspace 资源都必须根据当前用户和 membership 执行服务端授权。
4. 不能只相信前端传入的 Workspace ID。
5. 不能只依赖 Token 中可能过期的角色信息决定资源权限。
6. 所有租户 Repository 查询显式接收 WorkspaceScope。

权限不是四个角色名称，而是服务端动作矩阵。七天冻结规则如下：

| 动作 | owner | admin | member | viewer |
|---|---|---|---|---|
| 查看 Workspace 内业务资源 | 允许 | 允许 | 允许 | 允许 |
| 创建/修改普通业务资源 | 允许 | 允许 | 允许 | 拒绝 |
| 删除普通业务资源、运行 Tool/Research | 允许 | 允许 | 允许（仍受具体资源权限与预算约束） | 拒绝 |
| 邀请/移除 member、viewer | 允许 | 允许 | 拒绝 | 拒绝 |
| 在 member 与 viewer 间改角色 | 允许 | 允许 | 拒绝 | 拒绝 |
| 任命/移除 admin 或 owner | 允许 | 拒绝 | 拒绝 | 拒绝 |
| 修改 Workspace 安全设置或删除 Workspace | 允许 | 拒绝 | 拒绝 | 拒绝 |

用户不能修改自己的角色来提权。任何操作都不能移除或降级最后一个 owner；所有权转移必须在一个行锁事务中先确认另一名有效 owner，再完成角色变化。两个并发“移除最后 owner”请求最多一个可以成功，且成功后仍必须至少保留一名 owner。admin 不能修改 owner/admin，也不能通过邀请流程指定高权限角色。成员增删、角色变化、所有权转移和 Workspace 删除都写入脱敏审计日志。

### Agent Runtime 与 LLM Context 边界

1. 身份层完成 Token、Session、用户状态和当前 membership 校验后，只向可信 Runtime Context 提供服务端构造的 `principal`、`WorkspaceScope` 和 `capability`；它们是授权与执行对象，不是 Prompt 内容。
2. `principal`、`WorkspaceScope` 和 `capability` 不得由客户端、模型输出或 Tool 参数自行声明、替换或扩大。Runtime 和 ToolExecutor 必须使用可信 Runtime Context 重新校验实际作用域，不能把模型选择的 workspace、role 或 capability 当作授权事实。
3. Access/Refresh Token、CSRF/device Cookie、密码、Session recovery envelope、签名私钥、HMAC/AEAD 密钥、Provider Secret 以及数据库 Session、Repository、连接池、客户端实例等内部对象均不得进入 LLM Context。
4. Context Compiler 只能把完成授权后的任务内容、允许的 Memory、Observation、Evidence、Artifact 引用和必要的非敏感显示信息写入 LLM Context；不得直接序列化 Runtime Context，也不得通过 Prompt、Tool Observation、Trace snapshot 或 Artifact 泄漏认证材料和内部对象。
5. Trace 与 context manifest 可以记录脱敏的 principal/workspace 引用、capability 名称、策略版本和授权结果，以解释一次执行为何允许或拒绝；不得记录 Token、Cookie、Secret、完整认证 Header 或可重放凭据。
6. Background Job、Celery Worker、LangGraph resume 和 Harness replay 必须重新装载或验证可信 Runtime Context。Checkpoint、Event、Scenario 和模型输出都不能代替当前身份、membership 与 capability 授权。

### 配置与日志

1. 必需安全变量通过 Pydantic Settings 从服务端环境加载。
2. 开发、测试和生产配置边界明确。
3. 必需变量缺失时应用 fail fast。
4. 日志遮蔽 Token、密码、Cookie 和 Secret。
5. 带凭据的 CORS 不能使用通配来源。
6. Ed25519 私钥、受信任公钥 keyring、当前 `kid`、Refresh HMAC pepper、CSRF 密钥和 successor recovery AEAD 密钥只能从相互独立的服务端 Secret 加载，不进入仓库、镜像层或前端变量。
7. 签名密钥轮换采用“先加入新公钥 → 切换当前签名 `kid` → 等待旧 Access Token 最大 TTL 加时钟偏差 → 移除旧公钥”的顺序；紧急泄漏时立即移除受影响 key、撤销全部 Refresh Session 并要求重新登录。
8. CORS allowlist、可信 Origin 和对外基准 URL 必须是精确配置，禁止通过字符串后缀匹配来源。

`SameSite=Strict` 要求七天 Web 与 API 采用 same-site 部署，首选同源反向代理；精确 CORS 本身不能让跨站 Cookie 被发送。开发与 E2E 必须始终使用同一个 hostname（例如全部使用 `localhost`），不能混用 `localhost` 与 `127.0.0.1`。若部署必须跨站，必须先更新本 ADR 并重新评估 `SameSite=None; Secure`、CSRF 和第三方 Cookie 限制，不能只改 CORS。

安全设置在开发环境中可以替换密钥和缩短数据保留，但不能关闭签名验证、Workspace 授权、CSRF、Cookie Secure 或日志脱敏。

## 结果

### 收益

- Refresh Token 不能被普通前端 JavaScript 直接读取；
- Refresh Token 泄漏后可以通过数据库 Session 撤销；
- 轮换和重放检测可以识别旧 Token 再利用；
- Access Token 暴露窗口受到短有效期限制；
- 固定算法、issuer、audience、`kid` 和远程密钥拒绝规则降低 Token 混淆风险；
- Workspace 权限变化可以通过当前 membership 及时生效；
- Logout、设备 Session 和审计拥有持久数据依据。

### 代价与风险

- Refresh Session、轮换和重放检测增加数据库与事务复杂度；
- Cookie、CORS 和不同环境的安全配置需要浏览器 E2E 验证；
- Access Token 只在内存中意味着页面刷新后需要通过 Refresh 恢复会话；
- Ed25519 keyring、Session 状态和 CSRF 绑定增加实现与测试成本；
- 多个并发 refresh 请求需要前端单飞、数据库行锁和明确的冲突语义；
- 5 秒加密 recovery envelope 改善响应丢失恢复，但增加独立 AEAD 密钥、清理任务和短暂重发窗口，必须依赖 Origin、旧 CSRF、随机 device Cookie 与严格 TTL 绑定；
- `SameSite=Strict` 冻结了 Web/API same-site 和统一 hostname 的部署约束；
- Session 撤销和 rotation family 必须具有明确事务边界；
- Workspace 授权必须贯穿 API、Repository、Worker、索引和签名 URL。

## 否决方案

### 将 Refresh Token 放入 LocalStorage

否决原因：恶意脚本能够直接读取长期凭据，扩大 XSS 后的账户接管范围。

### 在数据库中保存 Refresh Token 明文

否决原因：数据库泄漏会直接暴露仍然有效的用户凭据。

### 使用永不过期或长期 Access Token

否决原因：泄漏后的有效窗口过长，也不利于权限变化和 Session 撤销。

### 只在前端执行 Workspace 权限判断

否决原因：客户端输入不可信，攻击者可以直接构造 API 请求。

### 将供应商密钥发送给浏览器

否决原因：前端代码、网络请求和构建产物无法安全保存服务端密钥。

## 验证

- 注册同时创建默认 Workspace 和 owner membership；
- 重复邮箱注册失败；
- 正确密码登录成功，错误密码失败；
- 不存在账号与错误密码返回相同公开错误，登录限流真实阻断暴力尝试；
- 过期、过早、错误 issuer/audience、未知 `kid`、错误算法和伪造 Access Token 全部被拒绝；
- Refresh 成功后旧 Token 失效；
- 两个并发 Refresh 以及首次响应丢失后的 5 秒重试只能创建一个后继 Session，并重发完全相同的 successor Refresh/CSRF Cookie；
- grace 内设备/Origin/旧 CSRF 绑定不匹配、grace 外旧 Token 重放和 recovery envelope 解密失败都会撤销 family；envelope 到期后被清除且日志/快照中不可见；
- 重放旧 Refresh Token 会撤销对应 rotation family；
- Logout 后 Refresh 失败且 Cookie 被清除；
- 修改密码要求正确当前密码；成功后旧密码、所有旧 Access/Refresh Session 立即失效，新密码可以重新登录，错误/并发/事务失败不产生部分状态；
- 缺少或伪造 Origin/CSRF 的 Refresh 与 Logout 被拒绝；
- Cookie 的名称、Secure、HttpOnly、SameSite、Path 和 Domain 属性通过 API/浏览器测试；
- same-site 反向代理、统一 hostname 和 `localhost`/`127.0.0.1` 混用失败行为通过浏览器测试；
- 页面刷新能够通过 Refresh 恢复合法会话；
- 未登录用户无法访问受保护资源；
- 用户 A 无法访问用户 B Workspace；
- owner/admin/member/viewer 每个动作的允许与拒绝矩阵全部通过；自提权、admin 修改高权限角色、移除/降级最后 owner 和并发最后 owner 操作被拒绝；
- 日志、测试快照、前端源码、网络构建产物和 `VITE_*` 中不存在服务端 Secret；
- PostgreSQL 或 Redis 故障时，健康检查和错误行为符合契约。

## 变更与回滚

Token 格式、算法、TTL、Cookie 策略、Session Schema、CSRF 策略或密码哈希参数变化必须更新 ADR，并说明旧 Token/Session 兼容、强制重新登录、数据迁移和回滚方式。

如果安全问题需要立即失效全部凭据，应轮换服务端密钥、撤销全部 Refresh Session，并要求用户重新登录，不能只修改前端代码。
