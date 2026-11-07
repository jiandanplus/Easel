# Easel Platform Integration and Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 用户当前只要求计划；本文件不是启动实施的指令。

**Goal:** 将 Easel 作为统一账号、会员和共享额度的首个产品，在不损失自定义模式与旧数据的前提下完成接入及迁移演练。

**Architecture:** Easel 本地桥代理中央 API，系统浏览器完成登录，本机保存产品会话并冻结任务身份。旧 SQLite 保留为旧 lane 与历史来源，通过只读盘点、双重证明及按账户准入开关完成迁移，不做全量覆盖或双账扣费。

**Tech Stack:** 现有 Python／FastAPI 本地桥、React／TypeScript、Node frontend tests、pytest；中央依赖前两份子计划，Easel 桌面不增加数据库／Logto 依赖。

## Global Constraints

- 保留当前 `codex/ui-redesign` 分支；不合并主分支，不擅自推送。
- 不触碰原 7860；所有服务使用自己创建的临时 HOME、工作区、DB、端口和进程。
- 开始修改前重新核对已有12个UI文件的工作区状态，不覆盖其内容；只按本任务所需的明确hunk修改相关文件，绝不全量stage。
- 自定义接口、模型和密钥字节不变；匿名自定义可用；没有平台请求的隐式回落。
- 本地 bearer 不进浏览器、localStorage、公开输出、日志、归档或桌面包。
- 已接受／运行任务中不切换账号、tenant、平台地址、来源和模型。
- 旧token和中央token不跨服务转发；旧账与新账不同时消费同一权益。
- 依赖 [身份计划](2026-10-16-logto-identity.md) I1–I5 和 [额度计划](2026-10-16-central-membership-credits.md) C1–C5；E1可以用固定合同夹具先行，E5必须依赖两服务真实实现。
- [设计](../specs/2026-10-16-logto-central-membership-design.md) 及 [迁移手册](../../platform/logto-migration-runbook.md) 是共同验收标准。

## E1：浏览器登录、本地私有会话和版本兼容

**Files:**

- Create: `easel/platform/{login,contracts}.py`
- Modify: `easel/platform/{client,session,web,privacy}.py`
- Modify: `easel_platform/api.py`（仅新增无敏感信息的legacy能力声明）
- Modify: `services/platform/src/platform_service/app.py`（新增注册产品的中央能力声明）
- Test: `tests/test_platform_{browser_login,session_v2,client}.py`、`tests/test_output_privacy.py`

**Interfaces:** Consumes I2 begin_login/redeem、I3会话合同；Produces `PlatformClient.start_login(product_id="easel") -> LoginStatus`、`cancel_login(transaction_id) -> None`、`PlatformClient.wallet() -> dict`、`PlatformClient.select_tenant(tenant_id) -> PublicStatus`。LoginStatus仅transaction_id/state/expires_at，不能返回verifier或token；authorization_url只交系统浏览器opener，校验为已配置平台的HTTPS地址（owned loopback夹具除外）。`SessionStore` version2新增product_id/tenant_id/billing_account_id/authorization_epoch/auth_mode，保留原url/auth结构以减少改动。

本地另提供GET`/api/platform/wallet`、POST`/api/platform/tenant`、POST`/api/platform/legacy/claim`。最后一个调用`PlatformClient.claim_legacy(email,password,*,legacy_url)`，先从中央取得绑定当前Scope的claim_id，再向已明确配置的旧平台请求proof、送中央complete；密码提交后清空并注册隐私过滤，不落文件、不送中央或Logto。legacy_url优先取私有v1配置，首次手工配置需显示目的域并保存后再操作；HTTPS／loopback及禁redirect规则与主平台相同，运行中不可更改。响应只返回脱敏link状态。

能力探测为中央GET`/v2/capabilities?product_id=easel`、旧服务GET`/v1/capabilities`；请求不附已有bearer。404不能触发携带凭证的自动降级；旧版本无声明时仅在用户明确选择legacy模式后使用原协议。URL更换、模式变更均先退出再重新认证。

- [ ] RED：callback state/PKCE不匹配不写会话；端口先绑定再创建事务；callback真实peer及Host校验；v1会话不被自动转发到v2；取消和任务冲突不留下listener／新会话。

```python
def test_wrong_state_does_not_replace_private_session(login_bridge, session_store):
    before = session_store.path.read_bytes()
    flow = login_bridge.start()
    response = login_bridge.callback(flow, state="wrong-state")
    assert response.status_code == 403
    assert session_store.path.read_bytes() == before
    assert login_bridge.raw_tokens_seen_by_ui == []
```

- [ ] 执行 `rtk proxy .venv/bin/python scripts/validation/run_isolated_tests.py --pytest-path /opt/anaconda3/lib/python3.12/site-packages tests/test_platform_browser_login.py tests/test_platform_session_v2.py tests/test_platform_client.py tests/test_output_privacy.py -q --tb=short`，确认 RED。
- [ ] 实现 owned listener、5分钟pending内存、双guard检查、timeout/cancel清理、回调后单次redeem与原子写文件。系统浏览器 opener 接受参数列表，不走shell。version1维持legacy客户端行为；选择v2需显式重新认证，旧版本文件仅在原路径的受限同级私有备份中保留，任何格式／权限错误停止且不销毁原文件。平台域更换必须先退出，不携带旧bearer。

```text
POST /api/platform/auth/start -> 同源/peer/运行guard -> start_login
GET /api/platform/auth/state -> 脱敏 LoginStatus
POST /api/platform/auth/cancel -> cancel_login
GET owned-listener /platform-auth/callback -> pending匹配 -> 私有redeem -> status
```

- [ ] 同命令 GREEN；并测noreferrer、禁缓存回调、恶意Host、路径穿越、重放／超时、失效Refresh、权限／nofollow、输出下载快照与归档无凭证。保留现有POSIX边界，Windows未支持时自定义路径仍可启动。
- [ ] scoped commit：`feat(easel): add private browser login for central accounts`。

## E2：统一账号与会员额度界面，保留旧入口和自定义

**Files:**

- Modify: `web/frontend/src/components/PlatformAccountSettings.tsx`
- Modify: `web/frontend/src/lib/api.ts`
- Create: `web/frontend/src/components/platform/{AccountIdentity,MembershipSummary,WalletSummary,DeviceSessions}.tsx`
- Test: `tests/frontend/{platformAccount,platformApi,harnessSettings}.test.mjs`
- Create: `tests/frontend/platformCentralAccount.test.mjs`

**Interfaces:** Consumes本地PublicStatus／wallet／entitlements；Produces `platformStartLogin()`、`platformCancelLogin()`、`platformSelectTenant(tenantId)`、`platformWallet()`，API返回可辨别 `auth_mode` 和 `ledger_mode`。`WalletSummary`的available/reserved/spent为字符串，不能先Number()后显示；无值为unknown，不冒充0。仅在legacy模式渲染原有密码表单，Logto模式使用统一站登录按钮和明确的旧账号关联入口。

- [ ] RED：未登录、登录中／取消、已登录、过期、网络失败、无会员、有会员无余额、待核算、旧token账等状态；新增主题状态不依赖平台请求。

```javascript
test('unavailable balance is distinct from zero', () => {
  assert.equal(walletText({ state: 'unavailable' }), '额度暂不可用');
  assert.equal(walletText({ state: 'ready', available: '0' }), '0');
});
```

`walletText` 为新建 `WalletSummary.tsx` 导出的纯格式函数，由测试使用现有前端测试的源码加载机制读取；对大整数使用字符串／BigInt格式化，不添加另一套运行时。

- [ ] 执行 `rtk proxy node --test tests/frontend/platformCentralAccount.test.mjs tests/frontend/platformAccount.test.mjs tests/frontend/platformApi.test.mjs tests/frontend/harnessSettings.test.mjs`，确认 RED。
- [ ] 实现组件拆分，仅改账号订阅区域；文案为“统一账号登录／注册”“平台共享额度”“Easel 本期用量”“预留额度”“待核算请求”“自定义接口”。显示test_only标签，无虚构套餐价格。scope不可切换时按钮禁用且后端同样拒绝；界面不请求刷新token、不缓存bearer。既有A–E主题及日夜模式由原样式控制。

```typescript
type WalletState =
  | { state: 'loading' }
  | { state: 'unavailable' }
  | { state: 'ready'; available: string; reserved: string; spent: string; test_only: boolean };
```

- [ ] GREEN 后执行 `rtk proxy npm --prefix web/frontend run lint`、`rtk proxy npm --prefix web/frontend run build`。浏览器检查宽屏／窄屏、键盘焦点、loading、网络失败和所有现有主题，记录截图；无浏览器证据不能声称视觉验收通过。
- [ ] scoped commit：`feat(easel): show unified membership and shared wallet settings`；不stage此前12个未提交UI文件的无关改动。

## E3：任务产品／租户冻结及新旧账路由

**Files:**

- Modify: `easel/service_contracts.py`、`easel/harnesses/{base,runtime,store}.py`
- Modify: `easel/platform/client.py`、`web/app.py`（仅已有任务接受／运行接缝）
- Test: `tests/test_{service_contracts,service_cli_contracts,subscription_execution}.py`
- Create: `tests/test_platform_task_scope.py`

**Interfaces:** Consumes本地v2 Scope／C4tasktoken；Produces追加字段的TaskContext／RunRequest、`PlatformClient.authorize(model) -> TaskAuthorization`、`task_credential(account_id,model,platform_url,*,product_id,tenant_id,billing_account_id,authorization_epoch,invocation_id) -> str`。TaskAuthorization保留account_id/platform_url，新增user_id映射信息及scope；account_id在legacy仍为旧ID，v2为中央user_id，历史另存legacy_account_id。新增参数为keyword-only，旧custom调用不变。

- [ ] RED：任务接受后切换租户／账号／服务地址被拒；已保存的tenant不能由后续UI配置替换；历史旧订阅不可换身份恢复；custom完全不构造PlatformClient。

```python
def test_custom_route_never_contacts_central(monkeypatch, custom_config, tmp_path):
    from easel.service_contracts import task_context
    def forbidden(*args, **kwargs):
        raise AssertionError("custom contacted platform")
    monkeypatch.setattr("easel.platform.client.PlatformClient", forbidden)
    context = task_context(custom_config, root=tmp_path)
    assert context.service_source == "custom"
```

- [ ] 执行 `rtk proxy .venv/bin/python scripts/validation/run_isolated_tests.py --pytest-path /opt/anaconda3/lib/python3.12/site-packages tests/test_service_contracts.py tests/test_service_cli_contracts.py tests/test_subscription_execution.py tests/test_platform_task_scope.py -q --tb=short`，确认 RED。
- [ ] 追加Scope字段并在接受任务时冻结。原生每轮请求得到固定invocation/attempt跟踪；新token调用中央 `/v1/responses`，旧token只调用旧服务同名路径，URL和账本模式都由已冻结的授权返回。不因v2失败回落legacy，不向新域发送旧token。来源切回custom保持原有供应商模型配置。暂不开放订阅cloud、其他引擎或媒体。

```python
@dataclass(frozen=True)
class CentralBinding:
    product_id: str
    tenant_id: str
    billing_account_id: str
    authorization_epoch: int
    invocation_id: str
```

`CentralBinding` 在 `easel/platform/contracts.py` 中定义，TaskContext和RunRequest追加同名扁平字段以保持现有序列化兼容。runtime调用task_credential前校验与存储会话完全匹配，拒绝任意缺字段的v2订阅恢复；custom字段可为空。

- [ ] 同命令 GREEN；用dummy provider覆盖工具调用／多轮／取消／SSE、秘密过滤、TaskContext旧位置参数构造、上游key不进入原生环境。检查原生未携带稳定幂等key的重试行为并记录限制，不能声称已消除所有重试计费。
- [ ] scoped commit：`feat(easel): freeze central billing scope for subscription tasks`。

## E4：只读盘点、可重复导入和旧权益围栏

**Files:**

- Create: `services/platform/src/platform_service/migration/{inventory,importer,cutover,cli}.py`
- Create: `services/platform/migrations/008_migration_checkpoints.sql`
- Create: `services/platform/tests/{test_migration_inventory,test_migration_import,test_cutover_fence}.py`
- Modify: `easel_platform/{store,api,ledger,gateway}.py`（仅按账户旧lane准入围栏与内部迁移控制）
- Create: `tests/test_platform_cutover_fence.py`
- Modify: `docs/platform/logto-migration-runbook.md`

**Interfaces:** Consumes源库只读一致副本、I4LegacyLink、C1–C5；Produces `inventory(source_path, source_system) -> Inventory`、`import_snapshot(run_id,source_path,rule_version,dry_run=True) -> MigrationReport`、`fence_legacy_account(legacy_account_id, fence_epoch) -> FenceResult`。Inventory记录schema版本／源摘要、表数、账户数、active/dispatched/pending及历史totals，不输出密码/hash/token/email。MigrationReport字段见运行手册；dry-run不连接可写目标；apply必须验证run_id/source_sha256/rule_version一致。

- [ ] RED：同一快照导入两次不重复用户mapping或历史，不发共享credit；有pending不能宣告旧lane退出；迁移中源改变即拒绝；旧服务被fence账户不再mint/dispatch但可完成已派发核算。

```python
def test_import_never_converts_legacy_tokens_to_shared_credits(migration_fixture):
    source = migration_fixture.snapshot(token_quota=100000, used_tokens=123)
    migration_fixture.apply(source, run_id="fixture-run-1")
    migration_fixture.apply(source, run_id="fixture-run-1")
    assert migration_fixture.shared_credits_issued() == 0
    assert migration_fixture.imported_used_tokens() == 123
    assert migration_fixture.mapping_count() == 1
```

- [ ] 执行 `rtk proxy uv run --project services/platform --group test pytest services/platform/tests/test_migration_inventory.py services/platform/tests/test_migration_import.py services/platform/tests/test_cutover_fence.py -q` 及 `rtk proxy .venv/bin/python scripts/validation/run_isolated_tests.py --pytest-path /opt/anaconda3/lib/python3.12/site-packages tests/test_platform_cutover_fence.py -q --tb=short`，确认 RED。
- [ ] 实现SQLite只读备份API取得一致副本（不能简单复制仍写入的.db而漏WAL）；导入历史到独立legacy_history表，source_system＋旧ID唯一，旧密码／会话token不导入。增量快照按源指纹新建run，不能假定时间戳有序或所有行有updated_at。每批事务保存checkpoint，崩溃重跑只补未完成项；历史校验用行级摘要和整数汇总。围栏写入旧服务持久化DB，由mint和每次dispatch读取，中央与旧服务切换不是分布式原子事务，先关闭旧写入、排空、再启新准入，中间宁可暂不可用。

```text
inventory -> dry_run -> fence_legacy -> drain/reconcile -> final_snapshot
 -> apply_idempotently -> verify_totals -> enable_central_admission
```

旧服务增加受限`POST /internal/v1/migration/fence`与`GET /internal/v1/migration/drain`，只接受配置的迁移服务身份与窄scope，不认用户bearer；CLI不直接并发改正在运行的SQLite。`legacy_admission_fences`持久记录account_id/fence_epoch，重复同epoch幂等、旧epoch拒绝。mark_dispatched与围栏检查在旧DB同一事务串行化，关闭围栏后的新dispatch必须为0；已发生尝试仍允许可信usage结算。

中央`activate-central`命令仅在最终报告passed、旧围栏确认、pending=0时将该批`migration_admission`改为central；I3授权服务在task mint和每次准入读取此状态。处于迁移隔离的已关联legacy账户只能查看状态，不能绕过开关发模型请求。尚未关联旧账户的新用户按普通中央测试授予规则，不受不存在的迁移run阻挡。

- [ ] GREEN；注入批次中断／恢复、重复run、源快照篡改、未认领账号、邮箱冲突、pending、重复grant补发、两进程同时迁移。所有无法核定的旧余额／请求进入隔离列表，不隐式修零；Easel-only测试补发必须另用operator命令及唯一issuance_key。
- [ ] scoped commit：`feat(platform): add repeatable legacy import and admission fencing`。

## E5：Easel＋第二合成产品端到端验证

**Files:**

- Create: `scripts/validation/verify_central_platform.py`
- Create: `tests/test_central_platform_verifier.py`
- Modify: `scripts/validation/verify_platform.py`（保留旧流程，增加可选择的中央合同模式，不覆盖旧证据）
- Create: `services/platform/tests/fixtures/second_product.py`
- Create: `docs/validation/2026-10-16-easel-central-platform.md`（实施时记实际证据）

**Interfaces:** Consumes I5、C5和E1–E4；Produces机器可读 evidence，checks包括identity/membership/shared_wallet/custom_unchanged/native_tools/privacy/migration/cleanup。入口 `verify_central_platform.py --fixture-only --codex /Users/jiandan/.nvm/versions/node/v24.14.0/bin/codex --evidence-dir /tmp/easel-owned-central-evidence`（原生路径为2026-10-16本机核对值，执行前再次验证）；`--with-owned-logto` 单独追加真实登录验证。所有凭证为合成值，上游dummy不得产生真实模型费。

- [ ] RED：故意跳过任一必需项，验证器必须非0；禁止引用用户现有HOME或7860；私有secret扫描命中则失败。

```python
def test_missing_native_proof_is_not_accepted(verifier_fixture):
    report = verifier_fixture.run(skip="native_tools")
    assert report.returncode != 0
    assert report.evidence["checks"]["native_tools"] != "passed"
```

- [ ] 执行 `rtk proxy .venv/bin/python scripts/validation/run_isolated_tests.py --pytest-path /opt/anaconda3/lib/python3.12/site-packages tests/test_central_platform_verifier.py -q --tb=short`，确认 RED。
- [ ] 实现owned资源编排，使用真实原生Codex执行本地Python marker、Responses工具回合、SSE和中央settlement；另一个合成产品从同钱包发并发请求。保存调用次数、整数余额／预留／pending、禁止案例及秘密扫描摘要，不存完整授权头。中央故障、IdP故障、订阅无权限和取消场景都验证自定义配置字节不变与可执行。
- [ ] GREEN；运行验证脚本与真实隔离Logto浏览器注册／邮箱验证／找回／SSO流程。最后执行完整根pytest、中央pytest、所有前端tests/lint/build，命令见运行手册。真实模型验证若未做，记录独立缺口，不阻止“模拟协议已验证”这个较窄结论，也不把它写成真实模型验收。
- [ ] scoped commit：`test(easel): verify central login billing and custom mode end to end`。

## E6：受控切换、回滚演练及交付审查

**Files:**

- Modify: `docs/platform/logto-migration-runbook.md`、`docs/platform/README.md`
- Modify: `services/platform/dev/README.md`
- Create: `services/platform/tests/test_rollback_authority.py`
- Create: `docs/validation/2026-10-16-central-cutover-rehearsal.md`（仅在实际演练后记录）

**Interfaces:** Consumes全套证据与E4围栏；Produces按run_id的迁移报告、异常清单、回滚演练证据，以及明确区分“开发验证通过／真实切换未执行”的状态。

- [ ] RED：中央已有消费时，回滚代码不可切回可写旧账；审计发现不平衡则整个切换验证失败。

```python
def test_rollback_keeps_central_ledger_authority(cutover_fixture):
    cutover_fixture.spend_central_credits(10)
    cutover_fixture.rollback_client()
    assert cutover_fixture.legacy_admission_enabled is False
    assert cutover_fixture.central_spent() == 10
```

- [ ] 执行 `rtk proxy uv run --project services/platform --group test pytest services/platform/tests/test_rollback_authority.py -q`，确认 RED。
- [ ] 实现演练脚本的故障开关和报告校验；在隔离副本演练登录回退、网关回退、迁移中断、旧lane围栏、中央多产品暂停／恢复。共享消费后只能保留中央账本前向修复；所有产品一起停止新reserve，不杀掉待核算记录。
- [ ] 同命令 GREEN；逐项核对手册验收矩阵、依赖锁、备份恢复证据、现有主题和自定义行为。单独出具部署所需真实域名、邮件、证书、secret存储和operator账户配置清单；真实值不可写入Git。当前任务仅计划，真实用户／公开切换需届时明确执行范围。
- [ ] scoped commit：`docs(platform): document central account migration and rollback evidence`。交付仍留当前分支；不创建合并主分支动作。正式收费／云执行／另一个真实产品接入使用下一份独立计划。
