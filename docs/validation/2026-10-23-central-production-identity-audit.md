# 中央平台生产准备：身份迁移只读审计

日期：2026-10-23。

范围：依据当前源码，审计从本地 Logto issuer 迁往服务器 HTTPS issuer 对产品账号、Console 管理员、中央会话、共享钱包和客户端入口的影响。本报告不包含真实 subject、凭证或未经核证的当前余额。用户已批准生产准备方向；本批不执行敏感身份映射迁移、不改配置、不访问远端、不调用模型。本文中的回归检查是后续执行前置条件，不代表已经运行或通过。

## 结论与当前能力缺口

当前实现不能仅替换 issuer 和中央 URL 就完成无损迁移。产品用户和 Console 管理员都按 `(issuer, subject)` 精确绑定。没有事先建立映射时，新 issuer 下的同一个人会得到新产品账户和钱包，或新的 Console 待批准申请；旧余额和权限仍在数据库中，但新身份无法直接访问。

产品侧已有允许多个外部身份指向同一用户的表结构，可支持受控新增别名；当前并没有经本次审计确认可直接执行的身份迁移命令。Console 当前没有保留原 admin ID 的身份 alias 迁移能力，需要另行设计、实现、测试及审查。普通 owner 审批不等于管理员迁移。

不得通过直接替换 issuer 配置、按 email/用户名合并身份、重建钱包或补发显示余额来绕过迁移。不得放宽 JWT issuer 校验。客户端应先在旧中央地址正常退出，再切换地址并重新登录。

## 源码行为与影响

### 产品身份和钱包归属

产品 OIDC 回调仅按 `(issuer, subject)` 查询 `external_identities`。命中后复用原 user ID，更新 email 等资料并查询该用户的 owner tenant 和钱包；未命中则创建新的 user、tenant 和 billing account。Email 不参与账户匹配。因此即使 subject 和 email 字符串相同，issuer 改变后也不是同一已绑定身份。

`external_identities` 以 `(issuer, subject)` 为主键，但允许多个记录指向同一 `user_id`。安全的产品迁移方向是：依据可信身份迁移证据建立 `(new_issuer, verified_new_subject) → existing_user_id`，保留旧记录，而不是覆盖旧记录或按 email 猜测对应关系。

钱包归属由原 user、tenant/member、billing account 关系决定。完整保留这些 ID 及账本关系，才能维持共享钱包。只迁移显示余额会遗漏批次、有效期、预留、分录、会员资格及待核账状态。回调当前通过排序后 `LIMIT 1` 选取 owner 钱包，迁移清单还应核对每个目标用户的 owner-wallet 基数，不应假定所有用户都只有一个钱包。

源码：

- [产品身份回调与原钱包选择](../../services/platform/src/platform_service/identity/__init__.py)
- [外部身份、用户、租户、成员和钱包关系](../../services/platform/src/platform_service/migrations/001_foundation.sql)

### 旧产品会话仍依赖旧身份

`identity_sessions` 和 handoffs 对旧 `(issuer, subject)` 有外键。`_account()` 也要求该身份仍然指向会话中的原 user ID，不能直接删除或覆盖旧身份行。

中央 access/refresh 会话校验依赖会话有效期、撤销状态、授权 epoch、用户及成员状态、产品和客户端状态，并不会因当前 issuer 配置改变而自动失效。因此切换时必须明确旧会话的保留或撤销策略，不能把改 issuer 当作全体注销。

建议先停止新的登录事务、处理在途任务，保留旧入口使客户端能够正常退出，再在新地址完成新登录。旧身份记录继续保留供历史核验及回滚；会话处理不应顺带改变账本状态。

源码：

- [会话和 handoff 外键](../../services/platform/src/platform_service/migrations/003_identity.sql)
- [身份服务 `_valid_session`、`refresh`、`_account`](../../services/platform/src/platform_service/identity/__init__.py)

### Console 管理员迁移需要新增能力

Console 回调同样仅按 `(issuer, subject)` 查询管理员。未找到或管理员未激活时返回 pending，不签发管理员会话。现有 `console-approve` 会对新二元组创建新的 admin ID，不会自动继承原管理员 ID 或历史审计归属。

当前 `console_admins` 直接保存单个 issuer/subject，没有独立的多身份 alias 表。若要求新旧身份指向同一管理员并保留历史归属，应另行实现 owner 执行、可审计、幂等且冲突拒绝的管理员身份迁移能力，例如独立身份映射表指向原 admin ID。此能力当前尚未具备，不能声称已经可以安全执行。

迁移必须保留原 admin ID、角色、active 状态、authorization epoch 和历史审计引用；被禁用的管理员不得借迁移恢复权限。本报告不建议直接修改现有管理员 issuer 来临时绕过映射设计，也不将新身份的普通审批视为等价迁移。

Console 会话通过 admin ID 和授权 epoch 校验，不会因 issuer 配置改变自动失效。浏览器 cookie 未配置跨站 Domain，迁往新的站点主机后应重新登录，而不是复制旧 cookie。

源码：

- [Console 回调、会话校验与 owner 审批](../../services/platform/src/platform_service/console_auth.py)
- [管理员、会话和不可变 ACL 事件](../../services/platform/src/platform_service/migrations/008_console_auth.sql)
- [Console cookie 和退出路由](../../services/platform/src/platform_service/console_app.py)

### 客户端地址切换顺序

`EASEL_PLATFORM_URL` 或构造参数中的 URL 优先于持久配置。已有 auth 与有效 URL 不一致时，客户端拒绝操作并要求恢复原地址后退出。`configure()` 拒绝已登录时修改地址；由环境指定地址时，也拒绝网页配置覆盖。

本地配置入口为 `POST /api/platform/central/configure`，请求体仅包含 `url`。配置及退出受任务忙碌 guard 约束。普通账号界面提供登录、退出及状态，并没有面向用户的通用服务地址编辑表单。新地址需要是 HTTPS origin；非 loopback HTTP 及任意 API 路径前缀不受支持。

应执行的顺序是：停止任务 → 保留旧地址可达 → 在旧地址退出中央账号 → 更新运营者启动配置或调用本地 configure → 在新地址重新登录。旧服务故障时 logout 会保留可恢复的认证状态，因此不能先拆除旧入口，再期待现有界面自动清理。不得通过手工删除凭证或改写私有状态来隐式跨越这一保护。

源码：

- [中央地址优先级、状态保护、配置与退出](../../easel/platform/central.py)
- [本地变更 guard 和 configure 路由](../../easel/platform/central_web.py)
- [URL 校验](../../easel/platform/client.py)
- [账号界面](../../web/frontend/src/components/CentralAccountSettings.tsx)

### Pi 历史和待核账操作不能自动跨地址续用

Pi 私有会话绑定包括 account、platform URL、完整 central scope、模型及服务来源。新登录会改变 session scope，切换中央 origin 也会改变绑定。任务凭证续取会核对当前地址和 scope，不能靠相同余额推断旧任务仍可执行。

本地 operation recovery 的 owner digest 包含中央 origin 及业务 user/product/tenant/wallet。因此换地址后，旧记录不会自动归入新 origin 的恢复列表。应保留历史文件和待核账 operation 清单，由受控流程核证；不得修改绑定或向新地址重放旧请求来制造恢复成功。原 unknown/待核账账本不因迁移而释放或结算。

源码：

- [Pi 会话绑定](../../easel/harnesses/pi_session.py)
- [operation owner 摘要和恢复记录](../../easel/platform/execution_records.py)
- [中央任务凭证地址及 scope 校验](../../easel/platform/central.py)

### 新 HTTPS issuer 的信任边界

OIDC discovery 要求响应 issuer 与配置完全一致；JWT 按配置 issuer 验签。产品和 Console 分别使用独立的 client/callback 配置。新环境中 subject 是否保持不变，应由可信身份数据迁移及真实签名登录证明，不能仅凭相同 email、用户名或数据库复制声明推断。

不得为兼容迁移而接受任意 issuer alias、弱化签名校验、重放旧 authorization code、跳过 PKCE/nonce 或绕过 Console 的新鲜认证要求。

源码：

- [OIDC discovery、端点及 JWT 校验](../../services/platform/src/platform_service/identity/oidc.py)
- [产品配置](../../services/platform/src/platform_service/config.py)
- [Console 独立配置](../../services/platform/src/platform_service/console_config.py)

## 后续迁移前置检查与安全步骤

1. **备份并演练恢复。** 分别保管 Logto 身份库、中央业务库、Console ACL/审计及必要配置的私有完整备份。公开证据仅包含摘要、计数和比较结果。保留原归档 unknown 账本，不将其状态重置。
2. **建立明确身份映射清单。** 清单关联旧 issuer/subject、新 issuer/subject 与原 user/admin ID，仅在私有受控环境使用。映射必须来自可信迁移证据或验证过的身份控制权，不依据 email/用户名自动配对。新身份已绑定其他主体时整体拒绝。
3. **实现及审查缺失能力。** 产品使用原身份表新增别名；Console 先完成保留原 admin ID 的迁移设计和实现。迁移应具备事务、幂等、冲突拒绝及审计，不通过普通批准另建管理员来声称 ID 已保留。
4. **先处理映射和数据，再开放新登录。** 保留原 user/tenant/wallet/admin ID 及完整账本。不得额外授权、重发赠送或把 internal/test 资金转换成 live。未验证映射前不要让新 issuer 的产品回调自动创建重复用户。
5. **运行隔离回归。** 验证同 email 不同 subject 不合并；新 issuer 无映射会走新用户路径；存在映射时 wallet ID 不变；重复迁移幂等；冲突拒绝；禁用管理员保持禁用；旧身份和审计可查；会话撤销或保留策略符合明确预期。
6. **切换前后核对不变量。** 对比用户、租户、钱包、管理员数量与 ID 映射，以及资金桶、批次有效期、held/pending、账本分录和 ACL 事件摘要。身份映射的预期新增项单列，不将其误认为新增业务账户。随后验证新 HTTPS OIDC 登录能访问原用户钱包和原管理员角色。
7. **迁移客户端并保留恢复窗口。** 先在旧地址正常退出，再修改地址并重新登录。分别验收新登录、旧凭证不转发、旧任务不能跨地址续用和历史记录仍被保留。客户端迁移完成前保留旧入口的短期恢复窗口；失败时恢复配置和入口，不重建余额或临时提升权限。

## 完成边界

本报告完成的是源码层只读审计。未声称 Logto 生产迁移、管理员 alias 支持、身份映射执行、正式 TLS 入口、旧会话撤销、钱包恢复演练或新地址登录验收已完成。后续敏感迁移必须以具体实现、私有映射核证和上述前置检查为依据。
