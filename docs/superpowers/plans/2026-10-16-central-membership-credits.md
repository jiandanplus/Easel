# Central Membership and Shared Credits Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 用户当前只要求计划；本文件不是启动实施的指令。

**Goal:** 为多个产品提供统一测试会员、功能资格和可并发安全消费的共享额度服务。

**Architecture:** 以中央 PostgreSQL 为账务唯一权威；会员授权与整数额度账本分离，通过 Scope 绑定租户和产品。网关先原子预留再调用上游，按可信 usage 结算；不确定费用进入待核算。

**Tech Stack:** 独立 Python 3.12 服务、PostgreSQL 16 事务／行锁、版本化 SQL、HTTPX 文本 Responses 网关；沿用身份计划建立的测试环境。

## Global Constraints

- 依赖 [身份计划](2026-10-16-logto-identity.md) I1 的 Scope、数据库和测试隔离；连接真实 Logto 前需 I2／I3。
- 当前分支 `codex/ui-redesign`；不合并主分支，不触碰 7860 或真实数据库。
- 初期仅 `test_only=true` 的测试会员／额度；没有价格、订单、支付成功或真实扣款状态。
- 内部单位 microcredit，1 credit = 1,000,000 microcredits；API 用十进制字符串，不使用浮点。
- 钱包不能负数；预留不是消耗；待核算不得当零；历史分录不可修改或删除。
- Easel 自定义来源不请求身份／额度服务；上游 key 只在服务端。
- 新服务不依赖产品工作区或 `web.app`；协议以 [设计](../specs/2026-10-16-logto-central-membership-design.md) 为准。

## 合同与目录

新增 `services/platform/src/platform_service/membership/`、`credits/`、`gateway/`。从 `contracts.py` 导出以下不可变数据合同；C1 定义，C2–C5 使用同名字段：

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class CreditQuote:
    product_id: str
    model: str
    rate_version: str
    upper_bound: int
    expires_at: int

@dataclass(frozen=True)
class Reservation:
    id: str
    scope: Scope
    operation: str
    idempotency_key: str
    body_hash: str
    quote: CreditQuote
    state: str

@dataclass(frozen=True)
class MeteredUsage:
    provider: str
    attempt_id: str
    event_id: str
    input_tokens: int
    output_tokens: int
    observed_at: int
    receipt_hash: str
```

Scope 来自 I1；MeteredUsage 的生产入口仅网关／已认证计量服务。保留原始 usage 和 provider receipt 摘要，不保存提示词正文。生成 `journal_entries` 时，available、held、spent、expired、issuance_source 为不同账本科目；一笔事务各分录有符号合计必须为0，credit_lot 的 issued = available + held + spent + expired。

## C1：钱包、费率及平衡分录基础

**Files:**

- Create: `services/platform/migrations/005_credit_ledger.sql`
- Create: `services/platform/src/platform_service/credits/{__init__,rates,repository,journal}.py`
- Modify: `services/platform/src/platform_service/contracts.py`
- Create: `services/platform/tests/{test_rates,test_journal,test_wallet_constraints}.py`

**Interfaces:** Consumes I1数据库／Scope；Produces `quote(scope, model, upper_usage, deadline) -> CreditQuote`、`price_usage(rate_version, input_tokens, output_tokens) -> int`、`post_journal(connection, transaction_id, entries) -> None`、`wallet_summary(scope) -> dict[str,str|int|bool]`。费率数值是非负有理数；分母>0；拒绝超 BIGINT、负单位和未知版本。钱包读写都须有 Scope，管理员命令走独立 operator identity，不接受外部传入 user_id 冒充。

- [ ] RED：费率对有理数总和只向上取整一次；不平衡分录不提交；超 BIGINT、负余额与混租户外键拒绝。

```python
def test_rate_rounds_once_for_the_complete_request(rate_fixture):
    from platform_service.credits.rates import price_usage
    rate_fixture(version="fixture-v1", input_ratio=(1, 3), output_ratio=(1, 3))
    assert price_usage("fixture-v1", input_tokens=1, output_tokens=1) == 1

def test_unbalanced_entries_roll_back(journal_fixture):
    import pytest
    from platform_service.errors import PlatformError
    with pytest.raises(PlatformError, match="unbalanced_journal"):
        journal_fixture.post(entries=[("lot_available", 100), ("source", -99)])
    assert journal_fixture.transaction_count() == 0
```

- [ ] 执行 `rtk proxy uv run --project services/platform --group test pytest services/platform/tests/test_rates.py services/platform/tests/test_journal.py services/platform/tests/test_wallet_constraints.py -q`，确认 RED。
- [ ] 实现 migration：credit_lots、rate_versions、reservations、reservation_allocations、journal_transactions/entries、usage_events。事务提交时由数据库约束触发器检查分录合计，不只依赖 Python；仓储同步维护受约束 lot 计数，审计查询定期与分录重算对齐。禁止 UPDATE／DELETE 已提交分录给应用 DB role，补偿使用新 transaction。事务外键包含 wallet／tenant／product 归属。

```sql
CHECK (issued >= 0 AND available >= 0 AND held >= 0 AND spent >= 0 AND expired >= 0),
CHECK (issued = available + held + spent + expired)
-- journal_entries 使用 signed_amount bigint，提交时 SUM(signed_amount)=0。
-- Python 以任意精度整数算有理数，最终写入前校验 0 <= value <= 9223372036854775807。
```

- [ ] 同命令 GREEN；真实临时 PostgreSQL 测试必须实际执行约束，不能用 SQLite替身。API 对超 JS 安全整数正确返回字符串；0 与无法读取状态分开。
- [ ] scoped commit：`feat(platform): add integer shared credit ledger and immutable rates`。

## C2：会员版本、产品功能及测试额度发放

**Files:**

- Create: `services/platform/migrations/006_membership.sql`
- Create: `services/platform/src/platform_service/membership/{__init__,plans,entitlements,router}.py`
- Create: `services/platform/src/platform_service/operator.py`
- Create: `services/platform/tests/{test_memberships,test_credit_issuance,test_operator_permissions}.py`
- Modify: `services/platform/src/platform_service/app.py`

**Interfaces:** Consumes Scope、C1 journal；Produces `entitlements(scope, at) -> EntitlementSnapshot`、`require_model(scope,model,at) -> EntitlementSnapshot`、`issue_test_membership(operator, billing_account_id, plan_version, issuance_key) -> str`。EntitlementSnapshot 字段：membership_id/plan_version/product_id/features/models/concurrency/valid_until/test_only。套餐定义不可变；更改创建新版本。issuance_key 用来源＋外部操作 ID，全局唯一；同key不同内容409。

- [ ] RED：Easel-only 会员不授予第二产品资格；会员到期后即使有余额也拒绝新准入；重复授予不延长时间、不重复发额；普通用户不能访问operator入口。

```python
def test_new_product_is_not_implicitly_included(membership_fixture):
    import pytest
    from platform_service.errors import PlatformError
    membership_fixture.issue(plan="easel-test-v1", included_products=["easel"])
    with pytest.raises(PlatformError, match="product_not_in_plan"):
        membership_fixture.entitlements(product_id="fixture-second")

def test_test_grant_is_idempotent(membership_fixture):
    first = membership_fixture.issue(key="operator:fixture:001", credits=1000)
    second = membership_fixture.issue(key="operator:fixture:001", credits=1000)
    assert first == second
    assert membership_fixture.total_issued() == 1000
```

- [ ] 执行 `rtk proxy uv run --project services/platform --group test pytest services/platform/tests/test_memberships.py services/platform/tests/test_credit_issuance.py services/platform/tests/test_operator_permissions.py -q`，确认 RED。
- [ ] 实现测试套餐的版本、时间窗、显式included_products、模型及并发；测试 lot 与会员创建在同事务发平衡分录、审计和outbox。Operator 只从本机管理命令／受限服务身份进入，普通API无“给自己发额度”。初期 `source=test`，未来支付事件只能经过独立鉴别 inbox，不能通过用户传的 payment_success 字段授额。

```text
issue: issuance_source -N; lot.available +N
expire: lot.available -N; lot.expired +N
revoke: 禁止新 reserve；已派发尝试保留 held 等待可信 usage，不直接删 lot。
```

- [ ] 同命令 GREEN；测试跨个人／团队钱包、会员撤销、lot到期、套餐升版本不改变老reservation、重复／乱序 entitlement事件；注册默认不发测试会员。
- [ ] scoped commit：`feat(platform): add versioned memberships and scoped test grants`。

## C3：跨产品原子预留、并发与幂等

**Files:**

- Create: `services/platform/src/platform_service/credits/{reservations,internal_router}.py`
- Create: `services/platform/migrations/007_reservation_leases.sql`
- Create: `services/platform/tests/{test_reservations,test_credit_concurrency,test_credit_idempotency}.py`
- Modify: `services/platform/src/platform_service/app.py`

**Interfaces:** Consumes Scope、CreditQuote、C2资格；Produces `reserve(scope, quote, operation, idempotency_key, body_hash) -> Reservation`、`mark_dispatched(reservation_id, worker_id, attempt_id) -> None`、`release_undispatched(reservation_id, reason) -> None`。internal_router 验服务身份的audience、scope及product允许列表，再绑定用户scope；禁止普通设备token调用。请求唯一键为 wallet/product/operation/key，整个wallet先锁，lot按 expires_at NULLS LAST/issued_at/id 锁；数据库租约字段由C5恢复器使用。

- [ ] RED：给同一钱包100 microcredits，两个产品各预留80，两个数据库连接同时提交，必须只成功一个；相同key不同内容409。

```python
def test_two_products_cannot_overspend(shared_wallet_fixture):
    from concurrent.futures import ThreadPoolExecutor
    shared_wallet_fixture.seed(available=100, included_products=["easel", "fixture-second"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(shared_wallet_fixture.try_reserve_80,
                                ["easel", "fixture-second"]))
    assert sorted(results) == ["quota_exhausted", "reserved"]
    assert shared_wallet_fixture.summary() == {"available": 20, "held": 80}
```

- [ ] 执行 `rtk proxy uv run --project services/platform --group test pytest services/platform/tests/test_reservations.py services/platform/tests/test_credit_concurrency.py services/platform/tests/test_credit_idempotency.py -q`，确认 RED，夹具用 barrier 强制两个独立连接竞争，不能串行跑后假称并发。
- [ ] 实现单事务资格检查、wallet锁、稳定lot锁、并发占位、journal和幂等登记。死锁／序列化失败有界重试3次并带抖动，未提交事务不得调上游。余额不足无副作用；并发超限429；事务未知提交结果按幂等键重查，不能重新发新key。

  CreditQuote只能由可信中央计量计算，reserve重新验证rate/model/upper_bound和有效期；外部不能传一个更小upper_bound绕过预算。member／device撤销阻止新reserve，不阻止internal计量服务对已发生尝试settle，以免撤销后丢账。

```text
BEGIN
  lock wallet -> validate current scope/entitlement -> lock eligible lots
  find idempotency result; same body reuse, different body reject
  check credit/concurrency -> available to held -> reservation + allocations
COMMIT
then mark_dispatched atomically, then call provider
```

- [ ] 同命令 GREEN；加入32连接、多进程服务实例竞争、先到期lot优先、跨产品lot限制、并发名额释放、请求体hash冲突。检查全量分录合计0且lot恒等式成立。
- [ ] scoped commit：`feat(platform): reserve shared credits atomically across products`。

## C4：可信文本计量与中央 Responses 网关

**Files:**

- Create: `services/platform/src/platform_service/gateway/{__init__,router,protocol,metering,task_tokens}.py`
- Create: `services/platform/src/platform_service/credits/settlement.py`
- Create: `services/platform/tests/{test_gateway_contract,test_metering,test_settlement,test_task_tokens}.py`
- Create: `services/platform/tests/fixtures/responses_provider.py`
- Read as contract: `easel_platform/gateway.py`、`ledger.py`、`tests/test_platform_gateway.py`
- Modify: `services/platform/src/platform_service/{app,config}.py`

**Interfaces:** Consumes C3reserve／I3scope；Produces `mint_task_token(scope, model, invocation_id) -> TaskCredential`、`settle(reservation_id, usage: MeteredUsage, reconciliation_key) -> Settlement`。TaskCredential 为token/expires_at，仅中央到本机私有响应；Settlement为reservation_id/state/charged/released/rate_version。网关提供 `/v1/responses`，新token只由中央验证；旧服务同路径仍只认旧token，不跨服务透传任何bearer。

- [ ] RED：已预留80、真实usage收费30后available70/held0/spent30；重复usage只扣一次；错product/tenant/model/taskepoch拒绝；没有最终usage进入pending。

```python
def test_settlement_releases_only_unused_hold(credit_fixture):
    hold = credit_fixture.reserve(initial=100, maximum=80)
    usage = credit_fixture.measured_usage(charged_microcredits=30)
    credit_fixture.settle(hold, usage, key="settle:attempt-1")
    credit_fixture.settle(hold, usage, key="settle:attempt-1")
    assert credit_fixture.summary() == {"available": 70, "held": 0, "spent": 30}
```

- [ ] 执行 `rtk proxy uv run --project services/platform --group test pytest services/platform/tests/test_gateway_contract.py services/platform/tests/test_metering.py services/platform/tests/test_settlement.py services/platform/tests/test_task_tokens.py -q`，确认 RED。
- [ ] 实现有界JSON/SSE处理和服务端模型映射；采用现有网关的文本／function/custom一层namespace协议限制，通过同一组黄金夹具对旧网关与新网关做行为比较。没有远端conversation、hosted search或媒体。task token绑定完整Scope、model、invocation；每次请求查中央授权；provider env/key禁返客户端。输入预算含规范化请求与实际映射体保守上界，输出budget必须显式有限。

  同步提供GET`/v2/products/easel/models`，目录依据已验证Scope及当前会员过滤，不泄露服务端上游地址／key；C2会员router提供GET`/v2/plans`和GET`/v2/products/easel/entitlements`，与Easel前端合同一致。

```text
dispatch evidence -> bounded provider response -> terminal trusted usage
 -> compute with frozen rate -> held to spent + remainder to available/expired
unknown terminal usage / timeout after dispatch -> held stays; state pending_reconciliation
```

- [ ] 同命令 GREEN；证明重定向／环境代理关闭、上游错误body清洗、字段／响应限额、取消后核算、实际费用越界模型熔断、rate变化只影响新请求。旧账迁移绝不在此任务给用户自动发共享积分。
- [ ] scoped commit：`feat(platform): meter Responses against central shared credits`。

## C5：多实例恢复、对账、用量和额度审计

**Files:**

- Create: `services/platform/src/platform_service/credits/{recovery,reconciliation,usage_router}.py`
- Create: `services/platform/scripts/verify_shared_credits.py`
- Create: `services/platform/tests/{test_credit_recovery,test_reconciliation,test_usage_api,test_shared_credit_acceptance}.py`
- Modify: `services/platform/src/platform_service/{app,operator}.py`
- Create: `docs/validation/2026-10-16-shared-credits.md`（实施时记录真实执行）

**Interfaces:** Consumes已派发attempt证据、C4settle；Produces `recover_expired_leases(worker_id, now) -> RecoverySummary`、`reconcile(operator, reservation_id, usage, key) -> Settlement`、`audit_wallet(billing_account_id) -> AuditResult`。RecoverySummary为released/pending/skipped_live；AuditResult为balanced/lot_consistent/reservation_consistent/differences。用户API `/v2/wallet` 可看授权钱包总余额／预留／累计额度消耗；`/v2/usage` 明细只限当前产品和计费主体，指定另一product_id返回403。团队member可读共享钱包汇总及本人当前产品明细，owner/admin可读该团队当前产品所有成员明细。未来跨产品明细账户中心需独立权限设计，本阶段不把任意产品bearer提升成全产品明细权限。

- [ ] RED：worker B启动不释放worker A存活的hold；已派发失联转pending；重复／冲突对账不改账；审计检测故意构造的不一致。

```python
def test_startup_does_not_release_another_workers_live_hold(recovery_fixture):
    request = recovery_fixture.dispatched(worker="a", live_lease=True)
    summary = recovery_fixture.recover(worker="b")
    assert summary.skipped_live == 1
    assert recovery_fixture.state(request) == "dispatched"
    assert recovery_fixture.held(request) > 0
```

- [ ] 执行 `rtk proxy uv run --project services/platform --group test pytest services/platform/tests/test_credit_recovery.py services/platform/tests/test_reconciliation.py services/platform/tests/test_usage_api.py services/platform/tests/test_shared_credit_acceptance.py -q`，确认 RED。
- [ ] 实现30秒心跳、120秒owner租约与CAS认领；lease过期不能证明“未派发”则pending，超时不能删除费用。进程生命周期恢复不扫描释放其他实例全部记录。对账必须有可信attempt/receipt摘要与新鲜operator权限；同key同usage复用结果，不同usage409，人工补偿单独平衡分录。仅明确证明未消费的attempt允许reconciled_release，不能由用户单击取消自动触发。

```text
reconciliation key + usage hash + prior state + scope
  -> one transaction: transition + journal + allocations + audit + outbox
audit: SUM(entries)=0; per-lot identity; held = sum(open allocations)
```

- [ ] GREEN 后运行完整中央测试及 `rtk proxy uv run --project services/platform python services/platform/scripts/verify_shared_credits.py --fixture-only --evidence-dir /tmp/easel-owned-credit-evidence`。脚本自行创建该owned子目录，已有未知目录则拒绝覆盖；验证两个合成产品共同消费、超并发、撤销、断流、进程崩溃、重复结算／补偿、过期lot、未知费用和所有恒等式。总结果必须有cleanup、test_only标记；无真实模型费用。
- [ ] scoped commit：`test(platform): verify shared credit recovery and reconciliation`。真实支付事件／人民币定价另开阶段，不借测试会员验收宣称商业订阅完成。
