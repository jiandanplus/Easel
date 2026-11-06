# 中央运营后台

Console 是独立管理网站与 BFF，不进入 Easel 桌面包，不复用产品 bearer 或用户工作区。它复用中央数据库和业务规则，使用独立 OIDC 应用、数据库角色、会话 Cookie 与权限检查。Easel 的自定义接口和普通账号页面保持各自路径。

## 本版管理范围

导航按任务组织（今天、用户、用量与费用、模型与套餐、操作记录），钱包明细、订单与支付、供应商、产品和结果不明的任务收在“高级”。

概览、用户、产品、套餐版本、共享钱包、订单与支付、报价、模型、供应商、调用记录、运营审计均使用中央业务数据。列表有搜索、稳定排序与有界分页；用户、钱包、订单有独立详情。

有权限的管理员／运营者可以发布不可变套餐、报价和预验证模型版本，撤回报价、启停模型；每次操作要求原因和幂等键，业务变更、操作结果与不可变审计同事务提交。只读角色只能查询。修改权益或价格使用新版本，不覆盖旧定义。模型发布默认停用，启用要求匹配服务器预先登记的验证依据；网页不能自行声明供应商已经验证。

内部体验发放、所有者额度发放、测试发点、支付凭据、供应商 Key、管理员授予、实际退款、手工付款确认、未知费用核算仍保留在受控运营流程；网页没有这些入口。特别不能把内部体验计划销售为付费套餐。

## 构建与打包

```sh
rtk proxy npm ci --prefix services/platform/frontend --ignore-scripts
rtk proxy npm test --prefix services/platform/frontend
rtk proxy python3 services/platform/scripts/build_console.py
```

构建生成 `frontend/dist/console-assets.json`，包含公开源码输入和输出资源摘要。独立 wheel 把 `frontend/dist` 纳入 `platform_service/console_static`。需要核验 wheel 内的实际路径和逐文件摘要，不能仅凭构建命令成功就宣布资源已打包。

源码运行可通过 `serve-console --static-dir /absolute/path/to/frontend/dist` 指定已验证产物。默认使用 wheel 内 `console_static`。没有资源时返回明确的 503，不显示模拟后台。页面与 JSON 禁止缓存，带哈希的静态资源可长期缓存；未知 API 和缺失资源不回退成 SPA。

## 独立配置

运行配置由服务器安全文件或密钥系统提供，仅显式读取 `CENTRAL_CONSOLE_*`，不自动读取仓库 `.env`，也不回退到产品或迁移 owner 凭据：

| 变量 | 用途 |
| --- | --- |
| `CENTRAL_CONSOLE_ENABLED=1` | 显式启用，否则拒绝启动 |
| `CENTRAL_CONSOLE_DATABASE_URL` | 独立低权限 Console 数据库角色 |
| `CENTRAL_CONSOLE_PUBLIC_URL` | 唯一规范 origin，不含路径／query；正式环境 HTTPS |
| `CENTRAL_CONSOLE_DB_SCHEMA` | 与目标中央业务数据库对应的固定 schema |
| `CENTRAL_CONSOLE_OIDC_ISSUER` | 固定 Logto issuer |
| `CENTRAL_CONSOLE_OIDC_CLIENT_ID` | 独立 Traditional Web 应用 ID |
| `CENTRAL_CONSOLE_OIDC_CLIENT_SECRET` | 仅服务器持有的应用 secret |
| `CENTRAL_CONSOLE_ALLOW_LOOPBACK_HTTP=1` | 仅本机内部预览，显式允许 literal loopback HTTP |
| `CENTRAL_CONSOLE_PROVIDER_REGISTRY` | 不含密钥的预验证绑定元数据 JSON；缺省空目录 |

支付状态由创建管理服务时传入的 `payments_mode` 只读元数据展示，独立 CLI 当前默认 `disabled`；这个参数不会启用收款。本次预览的实际中央支付也处于禁用状态。

所有私有配置设 `0600`，Web 进程使用独立系统身份。运行配置不包含任何 migration DSN；`serve-console` 检测到 owner 环境变量就拒绝启动，且只绑定 loopback。正式服务通过 HTTPS 反向代理转发，保持规范 Host；不信任任意代理头，不添加通配 CORS，不记录 callback query、Cookie、Authorization 或请求正文。

独立 Logto 应用固定登记 `<CENTRAL_CONSOLE_PUBLIC_URL>/oidc/console/callback`。secret 不进入前端或桌面，浏览器只持有随机 HttpOnly Cookie；管理会话绝对期限 8 小时，登录事务期限 5 分钟，角色撤销即时检查。同步 CSRF 值仅保留在页面内存；有副作用的请求还必须携带精确同源 Origin。

Logto 当前接口依据已通过 Context7 核对：[应用配置](https://github.com/logto-io/docs/blob/master/docs/integrate-logto/application-data-structure.mdx)、[传统 Web 授权码流程](https://github.com/logto-io/docs/blob/master/docs/integrate-logto/third-party-applications/README.mdx)。本服务继续复用既有严格签名与 issuer／audience／nonce 校验，不引入浏览器 token 存储。

## 数据库迁移与角色

先核对数据库／schema 所属、备份与原迁移摘要，再按原中央服务的 owner 流程应用新增 `008_console_auth.sql` 和 `009_console_operations.sql`，001–007 不编辑。产品 runtime 与 Console role 分别授权；产品角色不能读取 Console 表或使用 Console 审计 sequence。

另存运营配置 `/etc/easel-console/operator.env`，其中保留相同 Console 参数，并额外提供显式 `CENTRAL_CONSOLE_MIGRATION_DATABASE_URL`。此文件只供本地受控 CLI，不能挂载给 Web：

```sh
rtk proxy uv run --project services/platform --frozen --env-file /etc/easel-console/operator.env python -m platform_service grant-console --role central_console_runtime
rtk proxy uv run --project services/platform --frozen --env-file /etc/easel-console/runtime.env python -m platform_service serve-console --host 127.0.0.1 --port 18875
```

Console role 不拥有数据库或表，不继承 owner，不可 DDL、删除历史、修改余额、写入管理员 ACL 或内部发放表。只给实际查询所需的业务列、认证事务／会话写入，以及有限目录表和审计写入。启动会复核实际权限和可达角色，不能靠 NOINHERIT 隐藏更高权限。

## 首个独立管理员

1. 打开 Console 首页，点“前往统一身份登录”，由本人注册独立管理员账号并设置密码。
2. 正常 OIDC 验证成功且尚无 ACL 时显示“等待管理员授权”和申请编号。此时没有管理会话，也没有新增 Easel 钱包或点数。
3. 用户向运营者提供申请编号，无需密码、验证码、授权码或 token。运营者只批准已验证、未过期、issuer 匹配的准确申请。

```sh
rtk proxy uv run --project services/platform --frozen --env-file /etc/easel-console/operator.env python -m platform_service console-requests
rtk proxy uv run --project services/platform --frozen --env-file /etc/easel-console/operator.env python -m platform_service console-approve --request=VERIFIED_REQUEST_ID --role admin --operator host-operator
```

4. 授权后重新登录。普通注册不会自动获权；邮箱、用户名相同或第一个注册都不是授权依据。
5. 撤销管理员使用 owner-only `console-disable --admin=ADMIN_ID --operator host-operator`，旧会话会立即失效。网页不能授予自己或其他人管理员权限。

申请编号不是登录凭据，但仍应交给对应运营者核对。审批、撤销有不可变 ACL 审计。登录过期时关闭旧页面，从首页重新发起；账号已经注册成功不需要重新注册。

## 财务与证据语义

点数与金额在 JSON 中使用十进制整数字符串，前端使用 BigInt 显示，不能以浮点数扣账。只读页不会执行过期结算、查单、供应商请求或发点。

账本 `available` 是持久化桶；`spendable_available` 才是快照时刻未冻结且未到期的可花余额。冻结 available 与尚未过账的到期 available 是诊断量，可能重叠，不能直接相加。正式执行资金可包含内部体验赠送，非付款来源保留标记。缺失或未知金额显示未知，不显示为零。

生产收款、退款、媒体和云执行能力以对应服务实际配置与验收为准。Console 可展示目录和状态，不构成已完成真实商户、所有供应商、性能压测、恢复演练或生产发布的证明。测试 fixture、浏览器操作、真实 OIDC 管理员和部署资源身份分别记录在验证报告中。
