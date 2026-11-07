# Central Accounts, Plans and Shared Wallet Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [x]`) syntax. 本批已获用户明确实施授权；持续完成全部任务和验证，无需再次询问是否开始。

**Goal:** 交付独立的中央账号、版本套餐和跨产品共享点数钱包，保留 Easel 自定义接口与既有开发平台数据。

**Architecture:** Logto OIDC 负责身份，中央 broker 负责浏览器登录事务、本机一次性交接及产品会话。独立 PostgreSQL 服务维护 Scope、套餐、测试会员与整数点数平衡账本；Easel 本地桥只保存私有产品会话。真实支付和媒体执行属于后续批次。

**Tech Stack:** Python 3.12、FastAPI、HTTPX、Psycopg 3 async pool、PostgreSQL 16、PyJWT/cryptography、React/TypeScript。独立依赖锁，不加入桌面依赖。

## Global Constraints

- 当前 `codex/ui-redesign` 工作区直接开发，不切分支、不合并主分支、不自动提交／推送，不回退现有 99 个改动文件。
- 真实 `.env`、旧 SQLite、原有私有会话与当前 7860 配置不改；测试使用拥有的临时 HOME、工作区、数据库、端口和假身份。
- 旧自定义来源匿名可用，中央故障不自动切换到自定义，原供应商配置不上传。
- 本批套餐发放只允许标记为 test_only 的受限运营操作；真实订单／微信／支付宝收款不在完成定义中，不伪造已付款。
- 内部整数 microcredits，1点=1,000,000微点，API数量使用十进制字符串；过期余额、预留、已用与未知读取分别表示。
- PostgreSQL 真数据库验收；必需 DSN 缺失时测试失败，不跳过、不用 SQLite 替身；测试仅 loopback、明确 test 名数据库。
- 中央服务不导入 Easel/web.app，不读取用户工作区，不进入 Easel 安装依赖；旧平台保持可运行。
- OIDC 身份按 issuer/sub 绑定，不按 email 合并；access/refresh/handoff code 不回显到普通浏览器 API、日志或公开产物。
- 每个收费／发放操作幂等，钱包不负数，历史分录不可覆盖；同钱包跨产品并发原子准入。

## Frozen module contracts

`services/platform/src/platform_service/contracts.py`：

```python
@dataclass(frozen=True)
class Scope:
    user_id: str
    product_id: str
    tenant_id: str
    billing_account_id: str
    session_id: str
    authorization_epoch: int

@dataclass(frozen=True)
class VerifiedIdentity:
    issuer: str
    subject: str
    email: str | None
    email_verified: bool
    authenticated_at: int

@dataclass(frozen=True)
class OperatorIdentity:
    id: str
    capabilities: frozenset[str]
```

所有 ID 必须为非空有界字符串，epoch 非负非 bool。`PlatformError(status, code, retryable=False)` 只提供稳定错误码。`Database(dsn, *, min_size=1, max_size=5, schema=None)` 提供 `await open()`、`await close()`、`async with connection()`（dict rows）、`await migrate()`；连接和事务使用 Psycopg async 协议。`schema` 仅受控测试／运营配置，禁止用户输入；migrate 保留文件 SHA256、拒绝已应用文件变更，使用迁移锁。

基础迁移 `001_foundation.sql` 建 `users(id, authorization_epoch, active, created_at)`、`external_identities(issuer, subject, user_id, email, email_verified)`、`tenants(id, owner_user_id)`、`tenant_members(tenant_id,user_id,role,active)`、`billing_accounts(id,tenant_id)`、`products(id,enabled)` 和 `product_clients(product_id,client_id,enabled)`。使用 text IDs、timestamptz；复合归属约束防止混租户 FK。身份新增表放 `003_identity.sql`，钱包表放 `002_credits.sql`，互不并发编辑基础迁移。

中央鉴权后的公共路由：`GET /v2/capabilities?product_id=easel`、`GET /v2/me`、`GET /v2/plans`、`GET /v2/products/{product}/entitlements`、`GET /v2/wallet`、`GET /v2/usage`、`GET /v2/sessions`、`DELETE /v2/sessions/{id}`。`capabilities` 明确 `auth_mode=logto`、`ledger_mode=central_credit`、`billing_stage=test`、`payments_enabled=false`、`subscription_execution_enabled=false`，避免尚无S3链路时误用旧v1 token网关。

本批没有用户自助发点／改余额／自行结算的公共 API。Operator 通过独立 CLI 执行权限明确的套餐发布和测试发放，记录 operator 和幂等来源。

## Task 1 — Independent service and PostgreSQL foundation

**Own files:** `services/platform/pyproject.toml`、`uv.lock`、`src/platform_service/{__init__,contracts,errors,db}.py`、`src/platform_service/migrations/001_foundation.sql`、`tests/conftest.py`、`tests/test_foundation.py`。

- [x] RED：测试 Scope 拒绝空产品／负epoch，迁移首次应用／再次空结果／哈希变更拒绝、复合租户约束和包隔离。先观察缺模块或未实现失败。

```python
async def test_migrations_do_not_apply_twice(db):
    assert await db.migrate() == []  # fixture 已迁移
    async with db.connection() as conn:
        rows = await (await conn.execute("SELECT version, checksum FROM schema_migrations")).fetchall()
    assert rows and all(len(row["checksum"]) == 64 for row in rows)
```

- [x] 实现上述合同、async pool 和真实 PostgreSQL 测试 fixture；每个测试仅创建／清理自己命名的 schema，测试 DSN 要求 loopback 和 `easel_s1_test` 前缀，缺失 DSN 明确失败。
- [x] 独立包锁定依赖，测试命令：`rtk proxy uv run --project services/platform --group test pytest services/platform/tests/test_foundation.py -q`。GREEN 后报告实际结果，不自动 commit。

## Task 2 — Versioned plans, membership and shared credit ledger

**Own files:** `src/platform_service/migrations/002_credits.sql`、`membership.py`、`credits.py`（超长则拆同名包）、`tests/test_membership.py`、`test_credits.py`。Consumes Task1 Database/Scope/OperatorIdentity。

**Produces:** `MembershipService(db)` 的 async `publish_plan(operator, plan:dict)`、`issue_test(operator, billing_account_id, plan_id, issuance_key)`、`plans(product_id)`、`entitlements(scope)`；`CreditLedger(db)` 的 async `summary(scope)`、`usage(scope)`、`reserve(scope, *, operation, key, body_hash, amount, rate_version)`、`dispatch(scope, reservation_id)`、`settle(scope, reservation_id, *, actual, key)`、`release(scope, reservation_id, *, key)`、`pending(scope, reservation_id)`。

套餐字典固定字段 `id/name/version/included_products/features/credits/days/concurrency`；credits 是十进制整数微点，所有测试套餐 `test_only=true`，金额和支付不开放。套餐不可原地改版本。Test issuance 将会员、credit_lot、journal 和来源唯一键原子写入，不能跨租户误授予。

钱包响应 `unit=microcredit, microcredits_per_point='1000000', available/reserved/spent/expired/issued` 为十进制字符串；保留 `pending_reconciliation` 计数及 `test_only=true`。reserve 的 amount 仅可信内部合同，非客户端自报价格；本批不开放其公共写路由。

- [x] RED：重复发放返回同结果、同key不同金额409；套餐不含第二产品拒绝；无会员拒绝；2连接同时各预留80但钱包仅100，只能成功一个。

```python
async def test_shared_wallet_cannot_overspend(two_scopes, ledger):
    results = await asyncio.gather(*[
        ledger.reserve(scope, operation="fixture", key="one", body_hash="a", amount=80, rate_version="fixture-v1")
        for scope in two_scopes
    ], return_exceptions=True)
    assert sum(isinstance(result, dict) for result in results) == 1
```

- [x] 实现 PostgreSQL 钱包锁／稳定 lot分配、available/held/spent/expired批次计数、数据库平衡约束、历史分录禁止 UPDATE/DELETE、整数边界与一次向上取整定价 helper。过期先结转，hold按原批次结算；settle超预算拒绝并保留待核算，重复结算不双扣，不明费用不按零释放。
- [x] 测试实际数据库约束、发行/预留/结算/释放/待核算/到期、跨产品共享与跨租户拒绝，命令 `... pytest services/platform/tests/test_membership.py services/platform/tests/test_credits.py -q`。GREEN 后报告。

## Task 3 — Logto OIDC broker and central product sessions

**Own files:** `src/platform_service/migrations/003_identity.sql`、`identity/`、`tests/test_identity.py`、`tests/test_oidc.py`；Consumes Task1 contracts/db，独立依赖由Task1维护，需额外依赖先协调。

**Produces:** `IdentityService(db, settings, *, http_client=None)` 的 async `begin_login(product_id,client_id,callback_uri,handoff_challenge,local_state)`、`authorize(transaction_id)`、`callback(product_id,code,state,cookie)`、`redeem(payload)`、`authenticate(access_token,product_id=None)`、`refresh(refresh_token)`、`logout(access_token)`、`me(scope)`、`sessions(scope)`、`revoke(scope,session_id)`。确切HTTP挂载由root完成，身份服务不得自己创建公共业务发点入口。

`authorize` 返回 Logto URL 与相关性cookie；callback返回只含 code/state 的本机redirect。采用独立 OIDC PKCE/state/nonce 与本机handoff PKCE，不复用；固定服务端 OIDC 回调 `/oidc/{product_id}/callback`。产品client在DB预注册，对应OIDCclient/secret来自服务端 settings（不从用户body）。精确 loopback URI仅 `127.0.0.1`／`[::1]`，路径 `/platform-auth/callback`，随机端口可用，拒绝userinfo/query/fragment/localhost别名。

- [x] RED：错state/cookie/nonce/aud/issuer/signature/algorithm、过期、重复回调、错产品/PKCE/code、并发兑换拒绝；同issuer/sub映射同中央user及钱包，同email不同sub不自动合并。
- [x] 实现受限HTTP超时／大小／禁止redirect的OIDC discovery、JWKS与固定 RS256／ES384 白名单验证；固定issuer和clientaud，email_verified规则明确；明文clientsecret与OIDC token不写DB。事务只存证明hash，access/refresh只存hash；refresh轮换重放撤销设备，跨产品bearer不能复用；产品／成员／用户撤销每次认证生效。
- [x] session返回私有 `access_token/refresh_token/session_id/expires_at/refresh_expires_at/scope/account`，账号含id/email/email_verified；公共 me不含token。开发HTTP只明确loopback settings允许；生产HTTPS和Securecookie。
- [x] 用签名JWKS假IdP验证完整真实HTTP合同和拒绝分支，不将fixture视为真实Logto部署验收；命令 `... pytest services/platform/tests/test_identity.py services/platform/tests/test_oidc.py -q`。GREEN报告。

## Task 4 — Central API/operator and Easel account integration

**Root owns:** 中央 `config.py/app.py/__main__.py`、`tests/test_api.py`；Easel 新增独立 `easel/platform/central.py` 与本地桥适配、私有v2 session store；`PlatformAccountSettings.tsx` 的central分支及 `lib/api.ts` 合同；不重写旧v1客户端、旧自定义或主题。

- [x] 配置只从服务端环境读DSN/OIDCclient；公开服务缺身份配置时明确未就绪，不提供绕过的开发登录按钮。OperatorCLI预注册产品/client、发布test套餐和幂等发放，不提供公开operator bearer默认密钥。
- [x] API按Task1路由返回已认证Scope的钱包/套餐/明细；浏览器OIDC只放行精确协议路由，业务API要求设备bearer且不对任意Origin开放；小body限额、安全错误、不透传远端响应。
- [x] 新本地桥 `/api/platform/central/*`：私有登录监听器、PKCE交接、取消/超时/完成guard；默认平台入口由 `EASEL_PLATFORM_URL` 产品配置提供，自托管仍明确配置。普通响应不含token；已有v1会话不覆盖，v2另存受保护文件。
- [x] 账号页识别central capabilities，显示统一登录、测试套餐、点数（可用/预留/已用/过期/待核算）、明细和设备；保留v1测试页；明确支付尚未开放、执行S3尚未开放，不能把central会话送旧Responses网关。
- [x] 先写API拒绝和token不回显、客户端私有文件及来源隔离、前端未知/错误与真实0区分测试，再实现。使用隔离服务进行浏览器登录及钱包回读验收。

## Task 5 — Independent review and acceptance

- [x] 独立task/spec review和最终安全／并发review，修复所有重要问题并重跑覆盖测试。
- [x] 真实临时PostgreSQL全服务回归＋签名HTTP假IdP测试及真实Logto浏览器登录＋两产品同钱包并发＋重放/撤销＋平衡分录核对；实际SQL约束拒绝证明。
- [x] 旧账号/订阅/自定义/媒体相关Python回归、全部前端测试、build/lint、浏览器主题与窄屏。真实Logto若未部署，准确记录fixture与真实服务差别，不伪称SSO生产验收。
- [x] 文档、服务启动/迁移/测试运营命令、部署配置模板和验证报告；保存本批基线比较（真实.env、旧私有数据、已有99文件哈希、HEAD/main），清理仅本次拥有的临时服务；不合并主分支。
