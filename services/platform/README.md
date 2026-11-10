# 中央账号、套餐订单与共享点数服务

中央运营后台使用独立的管理网站、Logto 应用和数据库角色，提供用户、产品、套餐、钱包、订单、模型、执行记录与审计查询，并支持有审计的套餐／报价／模型发布。部署和首个独立管理员授权步骤见 [中央运营后台说明](CONSOLE.md)。普通产品账号不会自动获得后台权限，桌面自定义接口模式继续独立使用。

本目录是独立 Python 服务，负责中央账号、产品设备会话、测试套餐和跨产品共享整数点数账本。它不导入 Easel 桌面服务，不读取用户工作区，不使用原有 SQLite，也不加入桌面应用的安装依赖。

当前包含 S1 中央账号／钱包、S2 套餐订单／支付适配、中央文本流与恢复、本地内置 Pi 工具执行，以及独立运营 Console。运营者发布不可变套餐、报价和模型费率；只有渠道验签通知或经验签的主动查单结果可以确认付款并原子发点。文本执行按可信 token 用量结算共享钱包，费用未知的已派发请求保留待核算。没有公开发点、改余额或用户自行结算接口。

2026-10-23 的内部预览已完成本人注册登录、Pi 真实 GLM 5.3 文本及文件工具调用、内部体验额度扣点，以及独立管理员登录和 Console 读取验收；详情见 [工作台真实验收](../../docs/validation/2026-10-23-workbench-subscription.md) 和 [Console 验收](../../docs/validation/2026-10-23-central-console.md)。这不等于正式付费订阅或生产发布：真实商户收款、商业费率、订阅媒体、云端执行、多产品实接及生产恢复仍待完成。

正式部署准备工具、独立 systemd／HTTPS 代理模板和身份／钱包迁移门槛见 [部署准备与切换手册](deploy/README.md)。生成器只做离线检查，不能通过改 issuer 或手改报告冒充账号已迁移、正式站点已上线。

S5-A2 增加受控的同 Logto 恢复比对、owner-only 身份迁移命令和管理员 alias，保留原 user／wallet／admin 与历史资金状态。操作与支持边界见[身份连续性手册](IDENTITY_MIGRATION.md)；本批隔离验收不代表真实身份库迁移或域名切换已经完成。

2026-10-24 的 S5-A3 已检查真实服务器、权威账本与 Logto 源库，并修复实际身份库使用的原生 enum／enum[] 兼容性；真实源库只读采集与包含 enum 的合成备份恢复分别验证。实际环境仍是服务器运行中央 API／Console、本机运行 Logto；三个新域名尚未通过 TLS／站点验收。当前证据见[S5-A3 预检报告](../../docs/validation/2026-10-24-central-production-preflight.md)，逐项实际迁移门槛见[本次切换清单](deploy/CUTOVER_20261024.md)。

支付和文本执行均默认 `disabled`；显式 loopback 演练可以启用 `test`，真实部署使用 `live`。`/v2/capabilities?product_id=easel` 声明实际模式；只有 live 模式及对应验证配置就绪才返回正式支付／执行可用标志，测试可用性单列 `test_payments_enabled`、`test_execution_enabled`。Easel 的旧 v1 开发账号和匿名自定义接口保留各自配置、会话与执行路径；中央故障不会自动切换来源。商户配置见 [支付运维说明](PAYMENTS.md)，模型目录、费率、执行协议和恢复见 [文本执行运维说明](EXECUTION.md)。

## 依赖与镜像基线

使用 Python 3.12、独立 `pyproject.toml` 与 `uv.lock`，从仓库根目录运行：

```bash
rtk proxy uv sync --project services/platform --python 3.12 --frozen
rtk proxy uv run --project services/platform --frozen python -m platform_service --help
```

部署时保持锁文件，不在服务器执行依赖升级。测试依赖通过 `--group test` 显式启用。不要使用仓库根虚拟环境替代本服务的 `.venv`。

官方镜像固定引用如下。它们是本批锁定的基线，镜像引用本身不构成生产部署或业务验收证明：

| 组件 | 固定镜像引用 |
| --- | --- |
| PostgreSQL 16 | `postgres@sha256:9e2cf6287f25c3151f4c9421ccce2b17f3e60061c111a29165e0d2cdd9ff0a97` |
| Logto 1.45.0 | `ghcr.io/logto-io/logto@sha256:097cd47689546461edb4637fb6eee60b9ab6d3d4c08ebb20bc44a1b1809b82c0` |

Logto 和中央业务应使用独立数据库及凭据。Logto 自身数据库迁移、Console 管理入口、公开认证域名、邮件发送、TLS 和备份按 [Logto OSS 官方部署说明](https://docs.logto.io/logto-oss) 配置。本 README 不提供缺少这些参数的容器启动片段，也不要求复用任何现有 7860 服务或数据库。

## PostgreSQL：迁移 owner 与运行 role 分离

新建专用数据库。以下示例由 PostgreSQL 管理员在 `psql` 中执行，数据库名、角色名和 schema 是部署示例；如已有数据库，先核对所有权与现有授权再采用相应步骤。密码使用交互式 `\password` 或部署密钥系统提供，不写进 SQL 文件、命令历史或仓库。

```sql
CREATE ROLE central_owner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE central_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
\password central_owner
\password central_runtime

CREATE DATABASE easel_central OWNER central_owner;
REVOKE ALL ON DATABASE easel_central FROM PUBLIC;
GRANT CONNECT ON DATABASE easel_central TO central_owner, central_runtime;

\connect easel_central
REVOKE ALL ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA central AUTHORIZATION central_owner;
REVOKE ALL ON SCHEMA central FROM PUBLIC;
```

不要给 `central_runtime` 数据库、schema 或表的所有权，不要授予 owner／高权限角色的成员关系，也不要给 CREATE、DELETE、TRUNCATE、TRIGGER 或不可变历史表的 UPDATE 权限。应用启动会检查实际及可继承角色、所有权和危险授权；不满足要求会拒绝启动。

迁移和常驻服务分别使用配置文件：

- `/etc/easel-central/central.env`：常驻服务所需配置，仅含 runtime DSN。
- `/etc/easel-central/central-operator.env`：受限运营配置，包含相同服务参数和额外的 `CENTRAL_MIGRATION_DATABASE_URL` owner DSN。只供维护命令使用，不挂载到常驻进程。

两个文件都应是受保护的普通文件，只对对应部署账号可读。先从本目录 `.env.example` 复制模板到仓库外，替换占位符，设置文件权限 `0600`。URL 内密码按 URL 规则编码。远程数据库使用受验证的 TLS 配置；模板中的 `sslmode=verify-full` 需要正确的主机名与受信根证书。

在仓库根运行初始化命令：

```bash
rtk proxy uv run --project services/platform --frozen --env-file /etc/easel-central/central-operator.env python -m platform_service migrate
rtk proxy uv run --project services/platform --frozen --env-file /etc/easel-central/central-operator.env python -m platform_service register-product --product easel --client easel-desktop
rtk proxy uv run --project services/platform --frozen --env-file /etc/easel-central/central-operator.env python -m platform_service grant-runtime --role central_runtime
```

`migrate`、`register-product`、`grant-runtime` 和 `accounts` 优先使用 `CENTRAL_MIGRATION_DATABASE_URL`；未设置时会回退到 `CENTRAL_DATABASE_URL`，因此运营文件必须明确配置 owner DSN。`publish-plan`、`grant-test` 使用 `CENTRAL_DATABASE_URL` 的 runtime role。`grant-runtime` 不创建角色，只给已存在且通过检查的角色授予当前 schema 的必要 DML 权限；新增迁移后再次执行它。

迁移按文件名顺序执行，在同一数据库事务内使用锁，保存每个 SQL 文件的 SHA256。再次执行返回空应用列表；修改或删除已应用文件会失败。后续结构变更新增迁移文件，不编辑已应用历史。服务启动不自动迁移。

## 服务配置与启动

| 参数 | 用途 |
| --- | --- |
| `CENTRAL_DATABASE_URL` | 必填 PostgreSQL URL，常驻进程使用 runtime role |
| `CENTRAL_PUBLIC_URL` | 必填中央公开 origin，例如 `https://accounts.example.invalid`；不带路径、查询或 fragment |
| `CENTRAL_DB_SCHEMA` | 受控 schema，例如 `central`；禁止来自用户请求 |
| `CENTRAL_OIDC_ISSUER` | 固定 Logto issuer，例如 `https://login.example.invalid/oidc` |
| `CENTRAL_OIDC_CLIENTS` | 按 product ID 配置的 JSON 字典，见下一节 |
| `CENTRAL_ALLOW_LOOPBACK_HTTP` | 默认关闭；仅隔离的 literal loopback HTTP 演练设 `1` |
| `CENTRAL_POOL_MIN` / `CENTRAL_POOL_MAX` | 默认 `1` / `5`，允许范围满足 `1 <= min <= max <= 20` |
| `CENTRAL_EXECUTION_CONFIG_FILE` | 可选服务器私有模型配置文件；缺省关闭，详见 EXECUTION.md |
| `CENTRAL_MEDIA_CONFIG_FILE` | 可选服务器私有媒体配置文件；缺省关闭，详见 [MEDIA.md](MEDIA.md) |
| `CENTRAL_MIGRATION_DATABASE_URL` | 仅受限运营配置使用的 owner URL，常驻配置不包含 |

所有配置只从服务端环境加载；CLI 不会自动读取仓库根 `.env`。以下 `--env-file` 是显式指定独立配置文件：

```bash
rtk proxy uv run --project services/platform --frozen --env-file /etc/easel-central/central.env python -m platform_service serve --host 127.0.0.1 --port 18762
```

将进程交给受控服务管理器，以独立系统账号运行。HTTPS 反向代理保留 `CENTRAL_PUBLIC_URL` 对应 Host，后端只监听内部接口。CLI 已关闭 Uvicorn access log 和代理头信任；反向代理也应避免记录 OIDC 回调查询串、Authorization、请求正文和 cookie。不要把 `/oidc/` 回调重定向改写为普通业务请求，也不要添加任意 Origin 的 CORS 放行。

`GET /health` 只证明数据库连接可用。进一步检查已注册产品的 capabilities 及 `identity_ready`；没有 issuer／该产品 OIDC 客户端配置时，登录返回未就绪，不提供绕过身份验证的开发登录。

## Logto 产品应用配置

中央 broker 是保管机密的服务端，因此每个产品在 Logto Console 创建一个 **Traditional Web** 应用。Easel 本地应用只持有公开 `client_id=easel-desktop`，不持有 Logto secret。Traditional Web 的机密客户端与服务端换码方式见 [官方 OIDC 接入说明](https://docs.logto.io/integrate-logto/third-party-applications)。

Easel 配置顺序：

1. 确认 Logto 认证公开地址、TLS 和托管登录可用。从应用详情取得 OIDC discovery endpoint；其 JSON 的 `issuer` 必须与 `CENTRAL_OIDC_ISSUER` 一致。常见 discovery 为 `https://login.example.invalid/oidc/.well-known/openid-configuration`，issuer 为 `https://login.example.invalid/oidc`。
2. 创建 Easel 的 Traditional Web 应用，将 Redirect URI 精确设为 `https://accounts.example.invalid/oidc/easel/callback`。这里使用中央公开域名，不填 Easel UI 地址或随机本机回调端口。
3. 将该 Logto 应用的 ID 和有效 secret 配到服务器的 `CENTRAL_OIDC_CLIENTS`。JSON 外层 key 是中央产品 ID：

```dotenv
CENTRAL_OIDC_CLIENTS='{"easel":{"client_id":"REPLACE_LOGTO_APPLICATION_ID","client_secret":"REPLACE_LOGTO_APPLICATION_SECRET"}}'
```

4. 确认中央已执行 `register-product --product easel --client easel-desktop`。该公开产品 client ID 与 Logto 应用 ID 分别校验，不可混用。
5. 第二产品重复创建独立 Traditional Web 应用，回调为 `/oidc/SECOND_PRODUCT_ID/callback`，注册自己的公开产品 client，并在同一个 JSON 中追加产品配置。每个产品独立取得设备 bearer；共享账号／钱包不通过复制 Easel bearer 实现。

使用 discovery 返回的端点详情核对 issuer，不将普通登录站 origin 当作 issuer。服务仅接受 RS256／ES384，并严格绑定 RSA 或 EC P-384 密钥类型，同时校验签名、issuer、audience、nonce、时效及一次性证明，并采用独立 OIDC PKCE 与本机交接 PKCE。邮箱在提供时记录，`email_verified` 仅接受布尔 `true`；身份归并依据是 `(issuer, sub)`，相同 email 不自动合并。

当前客户端配置每个产品只接收一个 `client_secret` 字符串。Logto 新版 Management API 的应用 secret 是列表资源：`GET /api/applications/{id}/secrets` 返回含 `name/value/createdAt/expiresAt` 的对象数组；不要假设旧应用详情中的单个 `secret` 字段始终存在。由受限操作员选取目标 Traditional Web 应用仍有效的 secret，安全写入产品配置；轮换时部署新值并按维护流程重启服务，完成验证后再停用旧值。见 [官方应用 secret 列表合同](https://openapi.logto.io/operation/operation-listapplicationsecrets)。

如果通过 Management API 自动管理应用，应使用单独受限的 Machine-to-machine 管理应用和对应 Management API 权限。管理 token、M2M secret 与 Traditional Web secret 是不同凭据；本服务运行时不调用 Management API，也没有它的环境参数。不要把 M2M 管理凭据填进 `CENTRAL_OIDC_CLIENTS`，不要把 secret 列表响应输出到普通日志、浏览器或验收文档。

## 测试套餐和发放

示例 `examples/test-plan.json` 只定义测试权益：7 天、100 点、并发上限 2，覆盖 `easel`。`features` 必须与同模式文本模型的功能匹配；列出某项权益不会自动开放待接入的媒体或云端能力。整数微点精度为 `1 点 = 1,000,000 microcredits`；示例中的 `"100000000"` 是测试数量，不是价格或人民币兑换比例。

测试套餐明确 `test_only=true`，正式套餐明确 `false`；`grant-test` 拒绝正式套餐，正式权益只由已验证付款授予。`credits` 是规范十进制整数字符串，不能用浮点。发布前先注册 `included_products` 中的所有产品。套餐版本不可原地修改；如需更改数量、期限或覆盖产品，新增 ID 和版本。

```bash
rtk proxy uv run --project services/platform --frozen --env-file /etc/easel-central/central-operator.env python -m platform_service publish-plan --file services/platform/examples/test-plan.json --operator YOUR_OPERATOR_ID
```

用户通过 Logto 首次登录后，中央创建个人 tenant 和 billing account。受限操作员使用以下命令查询账号与钱包映射；输出包含账号资料，只在私有维护终端核对，不上传报告或公共文件：

```bash
rtk proxy uv run --project services/platform --frozen --env-file /etc/easel-central/central-operator.env python -m platform_service accounts
```

确认目标 `billing_account_id` 后，以稳定来源键发放：

```bash
rtk proxy uv run --project services/platform --frozen --env-file /etc/easel-central/central-operator.env python -m platform_service grant-test --wallet BILLING_ACCOUNT_ID --plan easel-test-v1 --key UNIQUE_ISSUANCE_KEY --operator YOUR_OPERATOR_ID
```

同一 operator 和来源键、相同钱包及套餐重复执行返回原结果；更换钱包或套餐会拒绝，不会重复发放。确需新增一次测试发放时使用新的、可追踪的来源键。CLI 的 operator ID 进入审计来源，它不是额外认证因子；执行权限依赖受保护的主机账号、配置文件和数据库凭据。不要在公共 HTTP 上包装这些 CLI。

点数账本区分 available、reserved、spent、expired、issued、frozen、refunded 和 pending reconciliation。测试／正式可用额分别展示，冻结批次不能消费。API 数量使用字符串；未知余额与真实 `"0"` 分别显示。分录不可原地 UPDATE／DELETE，退款通过新补偿分录与不可变会员撤销处理；不能手改表或恢复旧快照继续写账。

## Easel 本地桥接

在 **Easel 本地服务的启动环境** 配置以下参数，中央服务不读取它们：

```dotenv
EASEL_PLATFORM_URL='https://accounts.example.invalid'
EASEL_PLATFORM_CLIENT_ID='easel-desktop'
```

正式入口由产品配置预置；明确的自托管地址可以在未登录时设置。已登录更换服务地址前先退出，不能把既有 bearer 带到另一服务。

在账号设置中进入中央账号，点击登录／注册，浏览器完成 Logto 托管登录后回到 Easel。托管注册、邮箱验证与密码恢复由 Logto 配置负责。本机桥先拥有随机 loopback listener，再接受绑定 state、PKCE 与固定路径 `/platform-auth/callback` 的单次交接。普通页面只接收脱敏账号／钱包状态，不接触 access 或 refresh token。取消、超时、会话撤销及平台不可达均保留明确状态。

私有 v2 会话路径为 `<Easel工作区>/outputs/_harness/central-platform-session.json`，锁文件为同目录 `central-platform-session.lock`。目录权限 `0700`，凭据文件 `0600`，采用普通文件／无符号链接检查、锁和原子替换。旧 v1 文件 `platform-session.json` 保持独立。不要复制、展示、提交或归档这些文件。当前安全文件锁依赖 `fcntl`；不支持的系统会明确拒绝私有平台会话操作，自定义接口仍可使用。

中央退出撤销当前产品设备；不能把它理解为自动退出 Logto 浏览器 SSO。旧 v1 登录、自定义模型／供应商配置和用户的现有 `.env` 都不需要为本服务重写。中央 v2 产品会话不能交给旧 `/v1/responses` 网关。

## 真实 PostgreSQL 测试

测试必须使用自己拥有的临时 PostgreSQL 16 数据库，host 为 literal loopback（如 `127.0.0.1`），数据库名以 `easel_s1_test` 开头。测试凭据需要在该专用数据库创建／清理独占随机 schema 的权限；不要指向生产数据库。缺少 DSN 会失败，不跳过，也不替换为 SQLite。

推荐把 DSN 写入仓库外受保护的普通文本文件，例如 `/etc/easel-central/test-dsn`，文件权限 `0600`；只向测试进程传路径，避免把密码放进命令行。也支持直接设置 `EASEL_CENTRAL_TEST_DSN`，但不应打印其值。

```bash
rtk proxy uv sync --project services/platform --python 3.12 --frozen --group test
rtk proxy env EASEL_CENTRAL_TEST_DSN_FILE=/etc/easel-central/test-dsn uv run --project services/platform --frozen --group test pytest services/platform/tests -q
```

独立 fixture 每次只创建／清理自己的 schema。默认短回溯可避免 Psycopg 长回溯显示连接参数；不要启用 `--showlocals` 或收集包含凭据的调试日志。若运行环境限制 loopback 网络，需在获准访问该自有测试数据库的执行环境运行。

假 IdP／签名 JWKS 测试、真实 PostgreSQL 约束测试、真实 Logto 登录和生产部署是不同证据层级。测试通过不自动证明已部署 Logto、真实收款、媒体调用或生产并发。具体本批验证结果记录在仓库的独立验收报告，本 README 不保存滚动通过数量。

## 日常维护与恢复边界

启动失败先核对 owner／runtime 是否分离、迁移是否执行、授权是否更新、Host 与公开 URL 是否一致、OIDC 产品 key／应用 ID／回调是否匹配。CLI 只返回安全失败信息；在私有环境检查配置及数据库状态，不为排错开启明文 token、secret 或完整回调日志。

备份分别覆盖中央业务数据库、Logto 数据库和安全配置，恢复先在隔离环境演练。同一共享钱包开始写账后，不把旧快照接回生产继续消费；先停止所有产品新准入，保留未决预留和完整历史，再作前向恢复。卸载测试环境仅清理本次拥有的服务、schema 和容器，不触碰旧 v1 数据或用户工作区。
