# Logto Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 用户当前只要求计划；本文件不是启动实施的指令。

**Goal:** 建立可供多个产品使用的统一身份与中央产品设备会话，以 Easel 为首个产品。

**Architecture:** Logto 处理托管登录；独立 `platform_service` broker 处理固定 OIDC 回调与本机一次性交接。业务 Scope、成员权限与产品会话由中央数据库维护，桌面客户端没有 Logto 管理机密。

**Tech Stack:** Python 3.12、FastAPI、HTTPX、cryptography、PostgreSQL 16、Logto 1.45.0；中央服务单独锁依赖。实施使用库接口前按仓库规则查询当前 Context7 文档。

## Global Constraints

- 当前分支 `codex/ui-redesign`；不合并主分支，不擅自推送、部署或操作真实用户。
- 不修改 7860 服务，不使用真实 HOME、工作区、数据库或邮箱完成测试。
- 自定义模式不要求平台账号；平台故障不能触发自动改用自定义密钥。
- 不把中央服务、Logto、数据库或管理机密放入 Easel 桌面包。
- 只有明确的 OIDC 回调和一次性交接路由允许受限浏览器协议入口；其余 peer／Origin 限制保持。
- 本地只存平台会话；浏览器不接收 access、refresh、task 或 Logto token。
- 架构合同见 [设计](../specs/2026-10-16-logto-central-membership-design.md)；I1 是另两份子计划的依赖。

## 文件与接口边界

全部新增服务端文件位于 `services/platform/`。核心模块分为 `config.py`、`contracts.py`、`db.py`、`identity/`、`membership/`、`credits/`、`migration/`、`gateway/`；不从中央服务导入 `web.app`，不读取产品工作区。

I1 先定义以下合同，后续任务不能另造同义字段：

```python
from dataclasses import dataclass

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
class ProductSession:
    scope: Scope
    access_token: str
    refresh_token: str
    expires_at: int
    refresh_expires_at: int
```

`ProductSession` 为中央到本机私有通道类型，禁止直接成为浏览器响应。浏览器仅获得 `PublicStatus`（authenticated、脱敏 user、product/tenant、session_id、expires_at）。旧 account_id 与新 user_id 通过有唯一约束的 legacy mapping 连接，绝不按 email 查询自动绑定。

测试命令统一从独立服务目录运行；每个任务先 RED，完成后同命令 GREEN。下文 `rtk proxy uv run --project services/platform --group test pytest ...` 的测试环境在 I1 创建；尚不是现有仓库可执行命令。所有 PostgreSQL fixture 必须验证临时库归属，拒绝非 loopback／非测试库名；遇到未设置的测试 DSN 明确失败，不跳过必要数据库验收。

## I1：隔离服务、数据库迁移和 Scope 合同

**Files:**

- Create: `services/platform/pyproject.toml`、`uv.lock`、`src/platform_service/{__init__,config,contracts,db,app,errors}.py`
- Create: `services/platform/migrations/001_identity.sql`
- Create: `services/platform/dev/compose.yaml`、`.env.example`、`README.md`
- Create: `services/platform/tests/{conftest,test_scope,test_migrations,test_isolation}.py`
- Modify: `.gitignore`（只增加中央私有 env／临时数据排除，不覆盖已有规则）

**Interfaces:** Consumes 环境配置；Produces `Scope`、`VerifiedIdentity`、`ProductSession`、`db` pytest fixture、`apply_migrations(connection) -> list[str]`、`create_app(settings) -> FastAPI`。`db` fixture 在 conftest 使用 `open_test_db()` 创建资源，测试结束关闭连接／删除自己创建的数据库，不能指向原平台 SQLite。

- [ ] RED：先添加不可变 Scope、两次迁移无副作用、产品隔离及发行包隔离测试。核心断言：

```python
def test_scope_requires_product_and_tenant():
    from platform_service.contracts import Scope
    import pytest
    with pytest.raises(ValueError):
        Scope("u1", "", "t1", "b1", "s1", 1)

def test_migration_is_idempotent(db):
    from platform_service.db import apply_migrations
    assert apply_migrations(db) == ["001_identity"]
    assert apply_migrations(db) == []
```

- [ ] 执行 `rtk proxy uv run --project services/platform --group test pytest services/platform/tests/test_scope.py services/platform/tests/test_migrations.py services/platform/tests/test_isolation.py -q`；预期 RED（包、合同或迁移未实现）。首次环境准备失败不算行为 RED，先修正环境再观察上述断言。
- [ ] 实现独立包和迁移版本校验，迁移文件内容哈希存 `schema_migrations`，已应用文件被改必须拒绝；Scope 的所有 ID 非空、epoch 非负。配置禁止裸露管理服务；dev Compose 使用临时命名卷及随机发布端口、关闭公网监听。I1 的 migration 建 users、external_identities、tenants、tenant_members、billing_accounts、tenant_billing、products、product_clients、device_sessions、session_tokens、legacy_accounts 与审计／inbox／outbox基础表。billing_accounts 此时余额为 0，积分迁移由 C1 追加。

  `errors.py`定义`PlatformError(status:int, code:str, *, retryable:bool=False)`，只暴露稳定错误码和correlation_id。operator身份使用独立类型`OperatorIdentity(id:str, capabilities:frozenset[str])`，在`contracts.py`定义，来自受限服务认证，不得由产品请求JSON直接构造。

```sql
CREATE TABLE external_identities (
  issuer text NOT NULL, subject text NOT NULL,
  user_id text NOT NULL REFERENCES users(id),
  PRIMARY KEY (issuer, subject)
);
CREATE TABLE legacy_accounts (
  source_system text NOT NULL, legacy_account_id text NOT NULL,
  user_id text REFERENCES users(id),
  PRIMARY KEY (source_system, legacy_account_id),
  UNIQUE (source_system, user_id)
);
```

- [ ] 同命令 GREEN；锁定依赖和 Logto 镜像 digest，记录 PostgreSQL 16 组合兼容测试。检查 Easel 的安装依赖和打包 manifest 不新增这些服务端资产。没有 Docker／测试 DB 时报告环境阻塞，不伪称迁移通过。
- [ ] 只 stage 本任务新增文件及 `.gitignore` 对应 hunk；提交 `feat(platform): add isolated central identity foundation`。不得 `git add -A`。

## I2：中央 OIDC 登录事务和本机 PKCE 交接

**Files:**

- Create: `services/platform/src/platform_service/identity/{__init__,oidc,transactions,router}.py`
- Create: `services/platform/migrations/002_auth_transactions.sql`
- Create: `services/platform/tests/{test_oidc,test_handoff,test_auth_origin}.py`
- Create: `services/platform/tests/fixtures/oidc_provider.py`
- Modify: `services/platform/src/platform_service/{app,config}.py`、`services/platform/dev/README.md`

**Interfaces:** Consumes I1 `VerifiedIdentity`／数据库；Produces `begin_login(product_id, client_id, callback_uri, handoff_challenge, local_state) -> LoginStart`、`verify_callback(transaction_id, code, state, correlation_cookie) -> HandoffRedirect`、`redeem(code, state, handoff_verifier, redemption_id, product_id, client_id, callback_uri) -> ProductSession`。LoginStart 含 transaction_id/authorization_url/expires_at；HandoffRedirect 只含受约束 URI，不含 token。`oidc.py` 将 HTTP 与验证隔离为可注入 provider，生产只允许配置的 issuer。

- [ ] RED：覆盖 state/nonce/signature/aud、算法拒绝、错误 callback、一次消费、超时、错产品、错 PKCE、Cookie 绑定、callback 限定 Origin 例外。代表测试：

```python
def test_handoff_cannot_be_redeemed_by_other_product(login_fixture):
    import pytest
    from platform_service.errors import PlatformError
    flow = login_fixture.complete(product_id="easel")
    with pytest.raises(PlatformError, match="handoff_binding_mismatch"):
        login_fixture.redeem(flow, product_id="fixture-second")
    assert login_fixture.redeem(flow).scope.product_id == "easel"
    with pytest.raises(PlatformError, match="handoff_used"):
        login_fixture.redeem(flow, redemption_id="different-attempt")
```

- [ ] 执行 `rtk proxy uv run --project services/platform --group test pytest services/platform/tests/test_oidc.py services/platform/tests/test_handoff.py services/platform/tests/test_auth_origin.py -q`，观察上述行为 RED。
- [ ] 实现固定中央 OIDC 回调，两个独立 verifier、5 分钟事务／60 秒交接码、精确地址 allowlist 和 pending 状态机。使用I1的PlatformError，不含上游文本。首次身份映射时同事务建立个人tenant、owner成员和零余额billing_account，绝不自动发会员／额度。交接消费与设备创建同一事务；相同 redemption_id＋相同证明重试仅在 60 秒窗口返回同一私有结果，结果快照用服务端独立加密密钥保存，过期删除，不能存明文 refresh。不同证明不返回结果。

  OIDC只请求`openid profile email`，不默认请求offline_access；IdP token仅用于本次服务端验证，完成后不落产品会话。认领或全设备撤销要求新鲜认证时使用max_age并校验auth_time，不能把token签发时间代替用户认证时间；缺少可验证auth_time则重新认证或拒绝该敏感操作。Management API凭证另走服务身份，不能复用用户ID token。

```text
pending -> authorizing -> verified -> handoff_issued -> redeemed
   \-> cancelled / expired / failed（任一终态不能重新签发交接码）
```

- [ ] 同命令 GREEN；再执行真实隔离 Logto 登录冒烟，保留脱敏 issuer、client/audience、回调路径、拒绝案例及镜像版本证据。测试邮件仅落本地捕获器，未配置邮件不能宣称邮箱验证／找回通过。
- [ ] scoped commit：`feat(platform): add Logto broker and one-time login handoff`，包括 `errors.py` 和 migration；机密、env、数据库、token 快照均不 stage。

## I3：产品设备会话、租户授权和撤销租约

**Files:**

- Create: `services/platform/src/platform_service/identity/{sessions,authorization,revocation,tenants}.py`
- Create: `services/platform/migrations/003_session_revocation.sql`
- Create: `services/platform/tests/{test_sessions,test_tenant_authorization,test_revocation}.py`
- Modify: `services/platform/src/platform_service/identity/router.py`、`app.py`

**Interfaces:** Consumes I1 Scope／成员投影；Produces `authenticate(token, expected_product_id) -> Scope`、`require_scope(scope, *, tenant_id, product_id, capability) -> None`、`refresh(refresh_token, rotation_id) -> ProductSession`、`revoke_session(scope, session_id) -> None`、`switch_tenant(scope, target_tenant_id) -> ProductSession`。所有订阅请求先调用 authorization；事件处理为 `apply_identity_event(source,event_id,payload) -> bool`，bool 表示首次处理，重复事件无副作用。

`tenants.py`另提供`create_organization(operator,name,owner_user_id) -> str`和`remove_member(scope,member_user_id) -> None`。创建仅供受限operator初始化；成员移除仅owner/admin并禁止移除最后一名owner。中央组织先处于provisioning，outbox完成Logto organization映射后才active；外部失败重试不重复建组织。最小角色表：owner管理成员及账务，admin管理普通成员和产品用量，member使用获授权产品并读取共享钱包汇总；成员均不得调用operator发额。authorization_epoch是中央(user,tenant)授权版本，device/session另有独立revoked状态，两者每次准入都查。

- [ ] RED：产品 A bearer 调产品 B 返回403；成员移除后旧任务 token 失效；refresh 重放撤销族；错误用户不能撤销设备；外部禁用事件丢失后租约过期拒绝。

```python
def test_expired_identity_lease_fails_closed(identity_service, clock):
    import pytest
    from platform_service.errors import PlatformError
    session = identity_service.issue_fixture_session(product_id="easel")
    identity_service.provider_available = False
    clock.advance(301)
    with pytest.raises(PlatformError, match="identity_lease_unavailable"):
        identity_service.authenticate(session.access_token, "easel")
```

- [ ] 执行 `rtk proxy uv run --project services/platform --group test pytest services/platform/tests/test_sessions.py services/platform/tests/test_tenant_authorization.py services/platform/tests/test_revocation.py -q`，确认 RED。
- [ ] 实现 hash-only 设备 token、15 分钟 access／30 天 refresh、数据库原子轮换和重放处理；事件失序不得恢复被禁用主体；以版本/epoch拒绝陈旧事件，无法判断则主动读取权威。外部身份租约最长300秒，超期复核失败503且不派发上游。成员撤销使 tenant epoch 递增；切换 tenant 新发会话、撤销旧空间会话族。当前设备、全部平台设备及浏览器 IdP logout 为独立操作，其中全平台撤销要求 `authenticated_at` 距当前≤300秒。

```python
def require_live_epoch(scope: Scope, current_epoch: int) -> None:
    if scope.authorization_epoch != current_epoch:
        raise PlatformError(403, "authorization_revoked", retryable=False)
```

- [ ] 同命令 GREEN，使用两个产品／两个租户的 fixture 验证不串账、不串设备；身份事件模拟重试／乱序／丢失。中央业务撤销立即检查数据库，不能用300秒缓存延后本平台主动撤销。
- [ ] scoped commit：`feat(platform): enforce product tenant sessions and revocation`。

## I4：旧账号双重证明与关联冲突处理

**Files:**

- Create: `services/platform/src/platform_service/migration/{__init__,identity_claims}.py`
- Create: `services/platform/src/platform_service/identity/claim_router.py`
- Create: `services/platform/migrations/004_legacy_claims.sql`
- Create: `services/platform/tests/{test_identity_claims,test_claim_conflicts}.py`
- Modify: `easel_platform/api.py`（迁移开关下，新增受限旧账号证明端点；原密码登录不变）
- Test: `tests/test_platform_legacy_claim.py`

**Interfaces:** Consumes 已存在的旧 scrypt 验证函数、I3 新鲜 Scope／VerifiedIdentity；Produces `begin_legacy_claim(scope) -> str`、`issue_legacy_proof(email,password,claim_id) -> LegacyProof`（旧服务端用密码认证后签名，subject为旧account_id，另含audience/claim_id/issued/expires/jti，不含密码或hash）、`claim_legacy(scope, proof) -> LegacyLink`。LegacyLink 为 source_system/legacy_account_id/user_id/linked_at。claim_id由中央生成、绑定当前新鲜身份并限时；证明仅中央可验证，audience固定迁移服务，≤300秒、一次性jti，传输HTTPS（本地夹具除外）。旧服务未配置迁移签名密钥则端点关闭。中央路由为POST`/v2/legacy/claims/start`与POST`/v2/legacy/claims/complete`，旧路由为POST`/v1/migration/proof`，均禁止浏览器直连携带凭证。

- [ ] RED：同名未验证邮箱不能自动关联；旧密码＋新鲜已验证新身份成功；proof 重放、过期、错 aud、错误密码、两个新 subject 争一个旧 ID 返回冲突且无部分写入。

```python
def test_matching_email_does_not_link_accounts(claim_fixture):
    old = claim_fixture.old_account(email="same@example.invalid")
    new = claim_fixture.logto_identity(email=old.email, email_verified=True)
    claim_fixture.sign_in(new)
    assert claim_fixture.lookup_link(old.id) is None
    claim_fixture.claim(old, password=old.fixture_password, identity=new)
    assert claim_fixture.lookup_link(old.id).user_id == new.user_id
```

- [ ] 执行 `rtk proxy uv run --project services/platform --group test pytest services/platform/tests/test_identity_claims.py services/platform/tests/test_claim_conflicts.py -q` 及根目录 `rtk proxy .venv/bin/python scripts/validation/run_isolated_tests.py --pytest-path /opt/anaconda3/lib/python3.12/site-packages tests/test_platform_legacy_claim.py tests/test_platform_identity.py -q --tb=short`，确认 RED。
- [ ] 实现 legacy mapping 与双重证明唯一约束，旧 ID 永久保留在 mapping 和历史，中央 user_id 可不同。禁止自动合并两个已经独立活动的钱包／中央身份；无法证明旧所有权则返回 `legacy_recovery_required`，进入人工审核记录。关联合同不产生会员和额度；旧密码绝不转发 Logto，旧 password_hash不写中央。Proof验证和 jti消费在一次事务中完成，失败不占用旧账号。

```sql
INSERT INTO legacy_accounts(source_system,legacy_account_id,user_id)
VALUES ('easel-v1', :legacy_account_id, :user_id)
ON CONFLICT (source_system,legacy_account_id)
DO UPDATE SET user_id=EXCLUDED.user_id
WHERE legacy_accounts.user_id IS NULL OR legacy_accounts.user_id=EXCLUDED.user_id
RETURNING legacy_account_id;
-- 不返回行或另一唯一约束冲突转换为legacy_link_conflict，绝不覆盖其他user_id。
```

- [ ] 同命令 GREEN；所有失败响应使用统一错误体，不泄露邮箱是否存在；证明端点加账号／真实peer限速、固定错误与等成本失败验证。旧密码只送明确配置且冻结的旧平台地址，不提供向任意请求URL转发密码的能力；中央完成关联时必须匹配claim_id所属Scope。
- [ ] scoped commit：`feat(platform): link legacy Easel accounts with dual proof`。

## I5：身份环境运维与可复现验收

**Files:**

- Create: `services/platform/dev/logto-bootstrap.json`（非机密声明：easel、fixture-second、audiences、固定回调和允许 scope）
- Create: `services/platform/scripts/verify_identity.py`
- Create: `services/platform/tests/test_identity_acceptance.py`
- Modify: `services/platform/dev/README.md`、`docs/platform/logto-migration-runbook.md`
- Create: `docs/validation/2026-10-16-logto-identity.md`（实施时填实际记录，不能预写通过）

**Interfaces:** Consumes I1–I4；Produces 脱敏 evidence JSON，字段 `checks`、`versions`、`owned_resources`、`cleanup`、`status=passed|failed|blocked`。脚本退出码0仅当所有必须项 passed；失败／未执行不能写 passed。入口 `verify_identity.py --fixture-only --evidence-dir <owned_path>`；另有显式 `--with-owned-logto` 运行隔离真实 IdP。

- [ ] RED：脚本测试要求失败检查使总退出非0、日志无 fixture secrets、清理只操作 owned resources。

```python
def test_incomplete_identity_evidence_cannot_pass(acceptance_runner):
    result = acceptance_runner(required_check="logto_callback", state="blocked")
    assert result.returncode != 0
    assert result.evidence["status"] == "blocked"
```

- [ ] 执行 `rtk proxy uv run --project services/platform --group test pytest services/platform/tests/test_identity_acceptance.py -q`，确认 RED。
- [ ] 实现验证器和操作文档：最小权限 Management API 机密、密钥轮换及旧 JWKS 窗口、日志脱敏、邮件捕获、Logto 与业务库独立备份、仅私网 Console、迁移 Action 默认关闭。真实域名／邮件供应商在部署前由配置注入，缺少配置阻止公开注册／密码恢复，不假称已完成。
- [ ] GREEN 后执行 `rtk proxy uv run --project services/platform --group test pytest services/platform/tests -q`（当时存在的完整服务测试）及隔离 IdP 验证；记录两个产品 SSO、两租户拒绝、登录取消、IdP故障、撤销上限和清理。人工登录 UI 不能由协议夹具代替；没有人工／浏览器证据明确记 blocked。
- [ ] scoped commit：`test(platform): verify unified identity lifecycle and isolation`。通过此门槛只表示身份可用，不能标记共享额度或 Easel 迁移完成。
