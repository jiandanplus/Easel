# 中央支付、订单与退款运维

本阶段提供微信 Native 和支付宝当面付适配器、持久订单／通知／查单队列、原子会员与共享点数入账，以及未使用全额退款。真实商户接入尚待服务器安全配置和验收；本地展示的 0.01 元是明确模拟价格，不实际收款。订阅模型和媒体执行仍未开放，自定义接口可以继续独立使用。

## 模式、价格和发布顺序

支付默认 `CENTRAL_PAYMENTS_MODE=disabled`。此时目录为空，不能创建购买订单。`test` 仅允许显式 `CENTRAL_ALLOW_LOOPBACK_HTTP=1` 且公开地址为 literal loopback 的开发环境，只售测试套餐。`live` 要求正式 HTTPS origin、关闭 loopback 开发开关，并加载至少一个有效渠道；只售 `test_only=false` 的正式套餐。

先按 [部署说明](README.md) 备份中央业务库、执行新增迁移和 `grant-runtime`，再发布不可变 plan 与 offer。S2 新增 `005_payments.sql`；001–004 不重写。不要只换程序却漏迁移或运行权限，也不要以数据库 owner 启动常驻服务／worker。

plan 定义包含共享产品、能力、期限、并发和整数微点数量；offer 引用 plan，另有 CNY 整分价格和退款条款。金额及微点必须用规范十进制字符串；`1 点 = 1,000,000 microcredits`，没有自动人民币兑换率。实际商业价格、覆盖能力与可履约条款由运营者确认，本仓库不预设正式售价。正式模型执行尚未开放期间，不应将套餐元数据解释为该能力已可交付。

测试示例可按顺序发布：

```bash
rtk proxy uv run --project services/platform --frozen --env-file /etc/easel-central/central-operator.env python -m platform_service publish-plan --file services/platform/examples/test-plan.json --operator YOUR_OPERATOR_ID
rtk proxy uv run --project services/platform --frozen --env-file /etc/easel-central/central-operator.env python -m platform_service offer-publish --file services/platform/examples/test-offer.json --operator YOUR_OPERATOR_ID
```

正式 plan 与 offer 在仓库外另存，均使用 `test_only=false`，填写真实合同。offer 精确字段为 `id,plan_id,version,amount_minor,currency,test_only,terms`；terms 为 `refund_policy=unused_full_only` 和用户可见的 `description`。同 plan/version 的 offer 不可变；调价发布新版本。撤回不改既有订单快照：

```bash
rtk proxy uv run --project services/platform --frozen --env-file /etc/easel-central/central-operator.env python -m platform_service offer-withdraw --offer OFFER_ID --reason REASON --operator YOUR_OPERATOR_ID
```

## 商户文件

在服务器创建受保护目录 `/etc/easel-central/secrets/`，仅服务部署账号可读。复制 [商户模板](examples/payments-live.example.json) 为仓库外 `/etc/easel-central/payments.json`，删除暂未启用的整个渠道节点。模板不含真实密钥。

- `payments.json`、商户私钥和 APIv3 key 都必须是普通文件、权限 `0600`；不接受最终路径符号链接。配置只读文件路径和商户／应用标识，不接受任意网关 URL。
- 微信 `merchant_id` 是直连商户号，`app_id` 是开通 Native 支付的应用，`mch_serial` 是商户证书串号；商户 RSA 私钥至少 2048 位。APIv3 key 文件是恰好 32 字节原始密钥，不能附加换行。
- 微信 `verification_key_files` 由平台证书 serial 或公钥 ID 映射到可信 PEM 平台证书／公钥。必须从受信商户维护渠道安全获取。未知 serial 失败关闭，不从通知给出的 URL 下载信任材料。
- 支付宝配置直连 `merchant_id`（seller ID）、应用 `app_id`、应用 RSA 私钥和可信支付宝 RSA 公钥；当前采用公钥 RSA2 验签模式。未实现应用证书／根证书模式，不能将证书模式资料直接当成已支持的配置。
- 公钥／平台证书可按服务账号只读权限配置；避免泄露身份元数据，勿将商户文件、通知原文、完整签名、支付用户资料或私钥写入仓库、聊天、前端、普通日志。

正式常驻和受限运营配置各增加：

```dotenv
CENTRAL_PAYMENTS_MODE='live'
CENTRAL_PAYMENTS_CONFIG_FILE='/etc/easel-central/payments.json'
CENTRAL_PAYMENTS_WORKER='1'
```

只需在服务端配置一处，Easel 用户无需填写支付或模型密钥。公开回调地址由中央 origin 固定生成：

```text
https://YOUR_CENTRAL_ORIGIN/v2/payments/wechat/notify
https://YOUR_CENTRAL_ORIGIN/v2/payments/alipay/notify
```

微信支付和退款通知共用微信入口。HTTPS 代理需保留完整原始请求体、签名头、正确 Host 和成功／失败返回值；不要重写 form 或 JSON、要求用户登录、缓存通知或开放任意 Origin。原始通知有界读取并先验签。微信通知还需 AES-256-GCM 解密；支付宝通知拒绝重复 form 键。成功应答只表示已持久接收和处理，包括需人工核算的已验证事实，不能据此认定每条通知都已授予权益。

支付宝适配器固定正式网关 `https://openapi.alipay.com/gateway.do`，微信固定 `https://api.mch.weixin.qq.com`，禁重定向和环境代理。当前没有任意代理、服务商子商户或支付宝沙箱 endpoint 参数。未开通 Native／当面付、未授权该应用或使用不同商户体系时，需先明确适配范围再验收，不能通过改 host 绕过。

轮换微信验证材料时保留仍在使用的旧公钥／证书 serial，加入新 serial 后重启并核验；配置最多 16 个可信条目。商户号／app 更换会被订单快照校验挡住，旧订单不能发到新商户。应先停止新购，保留原配置处理旧订单和未决退款，再安排独立迁移；不要直接改历史订单或批量改状态。

## 订单、worker 和恢复

用户在 Easel 设置 → 账号与订阅中登录，在套餐购买选择微信或支付宝，点击购买后展示本地生成的二维码和冻结的条款／数量／期限。用户主动扫码；没有自动续费或自动扣款。页面返回、点击刷新和客户端传参都不能确认付款。浏览器只经本机桥访问自己的订单，bearer 留在私有 native 会话中。

常驻服务默认运行持久 outbox worker。若独立调度，设置服务的 `CENTRAL_PAYMENTS_WORKER=0`，由同一受限 runtime 配置周期运行以下命令；命令一次有界处理 1–100 项：

```bash
rtk proxy uv run --project services/platform --frozen --env-file /etc/easel-central/central.env python -m platform_service payments-work --limit 20
```

队列使用租约和 fencing token，支持多个 worker 竞争；支付网络请求在数据库事务外执行。成功结果在新的短事务入账。未收到通知时自动主动查单；网络超时、未知结果、过期关单失败均保留重试，不能假装成功／退款或释放冻结点数。到期订单请求渠道关单后继续查单；已关闭订单晚到的可信付款按原快照只授予一次。

同支付交易不能发给两个订单；同订单新交易、金额／商户不符、未知订单签名事实保留独立核算记录。查询 `billing_orders.reconciliation_reason`、`payment_event_audits`、`payment_orphan_events` 和未决 outbox 需使用私有维护终端，脱敏输出。当前没有公开改余额、标已付或人工核算完成的接口；需追加受控前向补偿设计，不能 SQL 手改终态。

`order_paid`／`order_refunded` 业务 outbox 当前在本服务确认持久处理，不发送邮件／短信／外部 webhook。后续通知投递需要独立接入和幂等消费。订单列表仅返回元数据，不为每笔历史订单重复生成二维码；选中详情时单独获取本地二维码。订单页面使用服务器 revision 丢弃旧财务响应，同版本只允许补充同一 checkout 的派生二维码图片，退款／冻结事实变化会刷新钱包与权益。

## 退款

当前退款政策仅 `unused_full_only`：对应批次全部未使用、未预留、未过期、未退款且未被冻结。申请和消费共用钱包锁，先冻结再排队请求渠道；已消费或部分退款不开放本地发起。操作命令没有金额参数，金额固定来自原订单：

```bash
rtk proxy uv run --project services/platform --frozen --env-file /etc/easel-central/central-operator.env python -m platform_service refund-request --order ORDER_ID --key STABLE_REFUND_KEY --operator YOUR_OPERATOR_ID
rtk proxy uv run --project services/platform --frozen --env-file /etc/easel-central/central-operator.env python -m platform_service refund-query --id REFUND_ID --operator YOUR_OPERATOR_ID
```

重复同 operator/key 返回原退款；更换订单拒绝。正式 live 退款命令会导致 worker 发起真实退款，只应在核实订单、政策和用户退款意愿后由受限运营者执行。用户浏览器没有退款管理接口。

未知／处理中保持冻结；经过验签确认成功才增加 refunded 补偿桶并追加会员撤销。原始 issued、消费和分录不删除。明确失败只在不存在其他未决／成功／人工核算退款时解除冻结。渠道侧意外退款、部分退款、已消费退款或异常交易进入人工核算，冻结／撤销相应批次，不制造负余额或虚假已收回记录。

## 本地模拟与正式验收

本地 test 配置只含 `test_key_file`（32–256 随机字节、0600）和 `test_state_file`（私有普通 JSON 文件路径），格式由服务维护。初始化可不创建 state 文件，但父目录需存在且受保护。不得复用正式商户资料。模拟 QR 是 `easel-test-payment://...`，不能用微信或支付宝实际付款。

受保护 CLI 可生成并验证模拟事实；普通 HTTP 不提供此操作：

```bash
rtk proxy uv run --project services/platform --frozen --env-file /PRIVATE/LOOPBACK/central-operator.env python -m platform_service test-payment --order ORDER_ID --status paid --key UNIQUE_EVENT_KEY --operator YOUR_OPERATOR_ID
# 退款申请后，仅用对应退款号模拟渠道确认：
rtk proxy uv run --project services/platform --frozen --env-file /PRIVATE/LOOPBACK/central-operator.env python -m platform_service test-payment --order ORDER_ID --refund REFUND_ID --status refunded --key UNIQUE_REFUND_EVENT_KEY --operator YOUR_OPERATOR_ID
```

这只是演练，不收付款、不产生正式权益。`disabled`／`live` 均拒绝模拟命令。测试事件保存模拟 operator 来源；重放保持幂等。测试通道状态可跨重启恢复。

真实商户资料安全接入后，单独验收每个渠道：开通状态和应用绑定 → HTTPS 回调及信任材料 → 经批准的小额真实订单 → 手机扫码 → 原始验签通知／主动查单 → 一次会员和点数入账 → 重复通知不重发 → 未使用全退、退款签名和补偿 → 超时、关单、轮换、重启恢复。记录实际渠道交易／退款结果的脱敏证据。没有完成这些步骤前，只能声明离线协议、真实 PG 和本地 HTTP／浏览器验证通过，不能声明真实收款或生产就绪。
