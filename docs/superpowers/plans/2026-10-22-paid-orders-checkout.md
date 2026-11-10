# S2 Paid Orders and Checkout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [x]`) syntax. 用户已批准继续下一阶段；已选微信＋支付宝，并说明商户资料稍后通过服务器安全配置接入。执行本计划，无需重新询问是否开始。

**Goal:** 完成套餐购买订单、微信／支付宝适配器、验签通知、幂等会员及点数入账、查单恢复、受控退款和 Easel 收银台／订单界面；真实商户收款另行验收。

**Architecture:** 沿用 Logto、独立 PostgreSQL 与 S1 钱包。支付 HTTP 在持久 outbox worker 中执行，不在钱包锁事务内调用上游；验签后的支付事实进入 inbox，在同一数据库事务内更新订单、授予会员、点数批次、平衡分录及业务 outbox。Easel 只读自己的订单并展示二维码，不能确认付款或自报可信价格。

**Tech Stack:** 独立 Python 3.12/FastAPI/HTTPX/Psycopg3/cryptography 服务；Python qrcode SVG；React/TypeScript；现有真实 PostgreSQL fixture。

## Global Constraints

- 留在当前 `codex/ui-redesign`，不切换分支、不合并、不自动 commit／push。开工已有 154 个未提交文件，保留其原改动。
- 不修改真实 `.env`、旧 v1 私有会话或自定义供应商配置。当前用户正在 Logto branding 页面操作，不重建／重置 Logto，不覆盖其账号、应用或 branding。
- S2 测试使用 `/private/tmp/easel-s2-runtime/database.json` 提供的独立 loopback PostgreSQL；fixture 缺 DSN 必须失败，不跳过、不用 SQLite。现有预览的 PostgreSQL 容器不能停止；测试只创建／清理自己的 schema／role。
- 只追加迁移 `005_payments.sql`（必要时追加 006），不能编辑已应用的 001–004 校验和。预览业务迁移前先做私有备份，最终授权受限 runtime role。
- 默认 payments mode 为 disabled；test 和 live 明确区分。test mode 只允许显式开发 loopback 配置，模拟成功只产生测试权益；没有真实商户配置／商业价格时不展示真实购买可用。
- 商户私钥、APIv3 key、证书、公钥路径只在服务器受保护文件配置，不放到浏览器、工作区 `.env` 或日志。开发不调用真实支付域名，不发起真实扣款或退款。
- 金额为 CNY 整分，点数为整数 microcredits，公共 API 都用规范十进制字符串；不使用浮点计价，不接受客户端 price／wallet／credits。
- 用户主动扫码购买／续订，不实现自动扣款。未定义的部分退款或已消费退款不开放；全额未使用退款采用 `unused_full_only` 条款。
- 支付页面返回、客户端“刷新”或用户提交均不能确认付款。仅渠道验签通知或经验签的主动查单事实可入账。
- 同一订单同一次付款只发点一次；已关闭订单晚到的真实付款仍授予一次并审计；新交易重复付款不再次发点，记录待核算。
- 退款不删除历史、不改原发放数量、不产生负余额；未知支付／退款结果保留待核算并查单恢复。
- 订阅模型执行 S3 仍关闭；自定义接口匿名可用，平台故障不自动切换来源。

## Shared Contracts

### Provider facts and transport

新包 `platform_service/payments/` 存 provider 合同与适配器。`PaymentOrder` 是已冻结的服务端订单投影：`id`（32 位字母数字渠道订单号）、`amount_minor:int`、`currency='CNY'`、`description:str`、`expires_at:aware datetime`。

`Checkout` 返回 `qr_code:str, expires_at:aware datetime`。适配器提供 async `create(order) / query(order) / close(order) / refund(order, refund) / query_refund(order, refund)`；同步 `verify_notification(raw:bytes, headers:Mapping)` 只验证及解析，不进行网络调用。退款输入含稳定 `id`、`amount_minor:int`、`reason:str`。

验签事实最终规范化为字典，给 core 的 `apply_verified_event(event)`：

```python
{
    "channel": "wechat", "mode": "test", "event_id": "fixture-event",
    "order_id": "stable-order-id", "provider_transaction_id": "trade-id",
    "status": "paid",  # paid / pending / closed / refunded / refund_pending / refund_failed
    "amount_minor": 1, "currency": "CNY", "merchant_id": "fixture-merchant",
    "app_id": "fixture-app", "occurred_at": "2026-10-22T00:00:00+00:00",
    "raw_digest": "sha256-of-bounded-raw-body",
    # Refund facts additionally carry refund_id and provider_refund_id.
}
```

Provider 层核对固定应用／商户、签名、算法、序列号、金额格式和最终状态；core 再核对订单已冻结的 channel/mode/merchant/app/amount/currency。原始个人支付资料不入普通日志；inbox 保存必要投影与摘要。

### Billing core

`BillingService(db, mode='disabled', channels=())`；channels 是服务端字典序列 `{id,merchant_id,app_id,label,enabled}`，public 目录剔除商户标识。接口：

```python
await service.publish_offer(operator, offer)
await service.withdraw_offer(operator, offer_id, reason)
await service.catalog(product_id)
await service.create_order(scope, *, offer_id, channel, idempotency_key)
await service.get_order(scope, order_id)
await service.orders(scope)
await service.apply_verified_event(event)
await service.request_refund(operator, order_id, *, idempotency_key)
await service.get_refund(operator, refund_id)
await service.claim_outbox(*, worker_id, limit=20, lease_seconds=60)
await service.finish_outbox(job_id, lease_token, *, result=None, error=None, retry_seconds=30)
```

Offer 引用已发布不可变 plan，不从用户表单取得价格：

```python
{
    "id": "fixture-cny-v1", "plan_id": "fixture-plan-v1", "version": "1",
    "currency": "CNY", "amount_minor": "1", "test_only": True,
    "terms": {"refund_policy": "unused_full_only", "description": "隔离测试商业条款，不是真实报价"},
}
```

真实 offer 必须 `test_only=false` 且与 plan 一致，运营者明确给出条款和价格。示例 1 分仅作为明确的模拟夹具，不作为真实产品定价。撤回 offer 使用新不可变记录，已创建订单保持快照。

订单只向有权 Scope 返回。公共订单包含 `id,status,channel,mode,currency,amount_minor,revision,offer,created_at,expires_at,paid_at,credit_state,checkout,refund`；金额／点数为字符串。状态区分 creating、pending、paid、closed、reconciliation_required、refunded，退款还包含 pending／manual reconciliation 等事实；UI 不将 unknown 当作失败或成功。checkout 包含二维码载荷／失效时间，由 HTTP 层生成安全 SVG data URL，不透传任意 SVG。

### HTTP and native bridge

公共业务接口均保留产品 bearer／Origin 边界：

```text
GET  /v2/billing/catalog?product_id=easel
POST /v2/orders                     {offer_id,channel,idempotency_key}
GET  /v2/orders
GET  /v2/orders/{id}
POST /v2/orders/{id}/refresh         查询／排队查单；不能确认付款
POST /v2/orders/{id}/close           关闭指令；渠道未确认前不谎称已关闭
POST /v2/payments/{channel}/notify   仅精确白名单渠道，原始字节验签
```

本地路由为 `/api/platform/central/billing/catalog`、`/orders`、`/orders/{id}`、`/orders/{id}/refresh`、`/orders/{id}/close`。私有 bearer 留在 native bridge；请求字段白名单，拒绝 wallet／价格／任意 URL。没有用户确认付款、发点、退款或模拟成功的公共 API。

## Task 1 — Signed payment adapters

**Own files:** `services/platform/src/platform_service/payments/{__init__,contracts,wechat,alipay}.py`、`services/platform/tests/test_payment_providers.py`。不编辑 core／app／config。

- [x] 官方当前文档按项目 ctx7 要求核对；记录文档来源与未知项，不请求／输出真实商户资料。
- [x] RED：合法 RSA 通知和 AES-GCM 密文在缺实现时失败；错误签名、串号、密文、金额、商户／应用、重复 form 键、未最终支付状态不能产生 paid 事实。

```python
def test_tampered_wechat_notification_cannot_confirm(provider, signed_fixture):
    with pytest.raises(PlatformError):
        provider.verify_notification(signed_fixture.raw + b" ", signed_fixture.headers)
```

- [x] 微信 Native：RSA-SHA256 API 授权与响应验证、证书／公钥串号选择、通知原文验签和 AES-256-GCM 解密、下单／查单／关单／退款／退款查询。
- [x] 支付宝当面付：RSA2 请求和原始 JSON 响应片段验签、form 通知规范化、金额整分转换、precreate／trade.query／trade.close／trade.refund／refund.query。不能 parse→dump 后验签。
- [x] 固定官方 HTTPS host、禁重定向、trust_env=False、有界 timeout/body、所有错误为稳定码且不携带签名原文／密钥。MockTransport 验证请求，开发不联系支付生产域名。
- [x] GREEN：运行 provider suite，报告红绿证据、字段合同与未验收的商户能力。

## Task 2 — Orders, atomic issuance and refund ledger

**Own files:** 新 `billing/` core 包、`005_payments.sql`；必要变更 `membership.py/credits.py/permissions.py`，新增 `test_billing.py/test_billing_refunds.py/test_billing_integrity.py`。不编辑 root app／provider／frontend。

- [x] RED：同key同订单返回原结果、同key变套餐409；客户端跨 Scope 读取拒绝；冻结价格不受后续目录撤回影响；错误金额／商户／应用不授予。

```python
async def test_duplicate_verified_payment_grants_once(billing, scope, offer, verified_event):
    order = await billing.create_order(scope, offer_id=offer["id"], channel="wechat", idempotency_key="one")
    await billing.apply_verified_event({**verified_event, "order_id": order["id"]})
    await billing.apply_verified_event({**verified_event, "order_id": order["id"]})
    assert (await billing.get_order(scope, order["id"]))["credit_state"] == "granted"
    # Independently query one membership, one lot and one balanced issue journal.
```

- [x] 原子 payment inbox＋order＋membership＋lot＋journal＋outbox；交易唯一键按 channel/mode/merchant 防跨订单复用。closed→verified paid正常一次授予；第二交易记待核算，不重复发点。
- [x] Outbox 使用租约、随机 token、SKIP LOCKED 和 fencing；支付 HTTP 不在 DB事务内；过期租约可恢复，同订单外部订单号固定，worker崩溃重做不会产生第二份权益。
- [x] 扩展 test/live plan 与 lot 来源，S1旧行为兼容。API 展示模式真实，不把测试余额标成全量正式余额。
- [x] 新增 refunded bucket，原 issued 不变；frozen lot 的 available 不可预留，summary 单列 frozen。会员撤销是新不可变行，active_plans 排除撤销会员。
- [x] 退款准入和消费共用钱包锁，只有全部未用、无 held／spent／expired／refund 时允许本地全额退款；先冻结，超时保留 pending。成功新记补偿分录；已耗用意外退款冻结 lot、撤销会员并记录人工核算，绝不改历史或负钱包。
- [x] 数据库限制订单绑定、财务历史 UPDATE／DELETE／TRUNCATE、退款量和账本一致性；低权限 runtime 必要列级授权。真实 PG 并发测试证明退款与预留不能同时花同一批次。
- [x] GREEN：以上三套与 S1 wallet／identity／权限测试，无 skip。

## Task 3 — Configuration, durable worker, API and operator CLI

**Root owns:** `payment_config.py/payment_runtime.py/payment_web.py/checkout_qr.py`、`app.py/__main__.py/pyproject.toml/uv.lock`、新增 `test_payment_config.py/test_payment_api.py/test_payment_runtime.py/test_checkout_qr.py/test_payment_operator.py`。

- [x] RED：disabled 配置不创建订单；live 缺证书／商业 offer 不显示真实购买；test mode 在非 loopback 生产 origin 拒绝；回调 body／签名／scope／任意Origin／价格字段拒绝。
- [x] `CENTRAL_PAYMENTS_MODE=disabled|test|live` 默认 disabled；`CENTRAL_PAYMENTS_CONFIG_FILE` 指向受保护服务器 JSON，只含商户标识和受保护文件路径；固定官方 endpoints。测试 adapter 仅构造隔离模拟事实，不从普通用户 API 接受“付款成功”。
- [x] Worker执行 create/query/close/refund/query_refund jobs，受保护 CLI 可 once drain；崩溃／网络不确定重试查单，成功 event 幂等。服务后台可轮询持久队列，优雅关闭只停止自己的 worker。
- [x] HTTP exact notify routes 使用原始 bounded bytes；微信返回渠道要求的成功／失败响应，支付宝返回纯文本 success／failure。未验证事件绝不入正常财务流水。
- [x] 新增 operator offer-publish／offer-withdraw／payments-work／refund-request／refund-query；operator 身份进入审计，不提供公共发点 bearer。测试完成通知由受保护 CLI 仅 test mode 触发。
- [x] QR用 qrcode SVG factory 本地生成，不调用第三方服务，不插入用户 HTML。data URL 从固定编码器构造，长度限制。
- [x] GREEN：有界真实 HTTP 支付夹具＋PostgreSQL证明 web callback→订单→钱包，包含重复／乱序／查单恢复；无真实付款。

## Task 4 — Easel checkout and order history

**Own files:** 新 `CentralBillingSettings.tsx/CSS`、`tests/frontend/centralBilling.test.mjs`；必要集成 `CentralAccountSettings.tsx/lib/api.ts` 与 native `central.py/central_web.py`；测试 `test_central_billing_client.py/test_central_billing_web.py`。不改 provider／数据库／旧 v1／自定义主题。

- [x] RED：未登录不购买；disabled 不显示真实收款；测试醒目标识；金额整数精确；错账号／旧响应不能覆盖新账号；用户返回／点刷新不标已付。
- [x] 收银台显示冻结套餐、CNY价格、点数、有效期、支付渠道、条款和本地二维码；区分等待、已确认到账、关闭、待核算、退款／人工核算。QR到期后停止展示；订单列表可恢复未完成订单。
- [x] POST create稳定幂等键，网络错误重试保留；GET轮询串行、有界、取消／卸载／账号变更停止，终态不会被旧pending覆盖；已确认入账后触发钱包／权益刷新。
- [x] 本机桥允许精确billing路径，不允许付款确认／自报价格／wallet。普通响应无token、签名或商户密钥；保留S3拒绝、自定义匿名、原会话隔离。
- [x] 真实React渲染和真实后端字段形状验证错误／退款／过期，完成frontend suite、build/lint与native相关回归。

## Task 5 — Independent review, preserved preview and acceptance

- [x] 每任务代码／合同审查，最终独立审查签名、交易唯一性、并发入账、租约、退款与消费及身份泄漏。重要问题全部修复并重跑覆盖测试。
- [x] 完整中央PG回归、native／旧接口回归、全部前端测试、build、lint、wheel迁移打包和diff检查；记录实际数量。
- [x] 私有备份当前preview中央DB；新增迁移与runtime授权只触及central schema，不重启／重置Logto。保留用户branding、账号和现有100测试点。
- [x] 隔离预览显式开启 test payment，发布1分模拟offer并醒目标识；真实浏览器展示下单、扫码占位、受控夹具确认、钱包只增加一次、查单恢复、关闭晚到支付、退款状态；不把模拟标成真实收款。
- [x] 主题A/C与390px窄屏检查；保留用户预览环境并说明merchant安全配置位置和真实验收步骤。
- [x] 更新部署模板、商户操作说明、验收报告及基线diff；真实 `.env`、原私有会话、HEAD/main保持，仍不合并主分支。

## 验收结果

本地 S2 开发与验证完成，证据见 [S2 验收](../../validation/2026-10-22-central-s2.md)。真实商户安全配置、真实收付款、服务器容量和 S3 执行仍为后续验收范围，不计作本计划已通过。
