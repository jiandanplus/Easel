# 中央平台部署准备与正式切换手册

本目录对应 S5-A1：生成与验证部署准备包。生成工具不连接服务器，不修改 DNS，不申请证书，不安装／重启服务，不读取私有配置，不迁移身份或钱包。`production_ready: false` 是有意保留的证据边界；必须完成本文实际部署门槛，不能通过手改报告消除。

后续 S5-A3 的真实预检、enum 兼容修复与隔离安装证据见[2026-10-24 验收](../../../docs/validation/2026-10-24-central-production-preflight.md)；按实际服务器、原账本、两个 Logto 应用与 OpenResty 路径填写的[本次切换清单](CUTOVER_20261024.md)补充本文。公开配置已使用新的 `production-preflight-20261024` release 目录；旧 S5-A1／S5-A2 归档保持原内容，不能混用旧目录与新 wheel。新正式服务、真实身份恢复与域名切换尚未执行。

用户已指定中央后台为 `https://console.drawbookai.cn`，并确认登录、中央 API 分别使用 `https://login.drawbookai.cn`、`https://accounts.drawbookai.cn`，统一目标主机为 `115.190.211.112`。本轮最初公共 DNS 中 console／login 指向 `139.196.51.51`，accounts 指向目标；用户随后完成两条独立 A 记录修改，2026-10-23 22:57（UTC+8）公共 DNS 复核三个 A 均指向目标，均未查到 AAAA。22:58 对固定目标 IP 的受验证 TLS 检查中，三个站点均未通过证书主机名校验，未绕过验证或发送登录凭据。域名已经解析不证明 TLS／应用已经部署。现有 `api.drawbookai.cn` 继续属于 New API，不覆盖该站点。

## 公开部署合同

通用示例是 `production.example.json`，本次已确认目标的公开配置是 `drawbookai.public.json`。目标主机固定为上述 `115.190.211.112`；端口、目录和实际服务器资源在安装前仍需核对。文件只能放公开地址、目录和端口；不能添加 DSN、secret、Key、Cookie 或 bearer。固定字段为：

| 字段 | 合同 |
| --- | --- |
| `version` | 整数 `1` |
| `public_url` | 中央 API 的 HTTPS origin，例如 `https://accounts.drawbookai.cn` |
| `console_public_url` | Console 的不同 HTTPS origin，例如 `https://console.drawbookai.cn` |
| `oidc_issuer` | discovery 返回的精确 HTTPS issuer；本批部署合同使用 `/oidc` |
| `source_oidc_issuer` | 迁移来源的精确 issuer，仅用于提出迁移门槛，不授予信任 |
| `api_port` / `console_port` | 不同的 1024–65535 端口；示例 19874／19875，实际占用需在目标主机检查 |
| `release_dir` | 新版本的独立绝对路径，例如 `/opt/easel-central/releases/20261023-preparation` |
| `config_dir` | 独立机密目录，例如 `/etc/easel-central-production` |
| `certificate_dir` | 证书目录；生成站点使用其 `api/fullchain.pem`、`api/privkey.pem` 与 `console/fullchain.pem`、`console/privkey.pem` |

API／Console origin 会规范化缺省 HTTPS 端口；issuer 作为身份边界保留精确字符串，包括显式 `:443`。不要自行规范化或去掉 issuer 的一部分。正式服务主机名应与 Logto 登录主机不同。所有路径须为规范、无空白、无插值字符的 ASCII 绝对路径。示例的 `.invalid` 主机仅用于离线演示，报告会标记其未完成域名配置。

## 在开发机器生成部署包

先按锁定前端依赖构建独立 Console，再构建中央 wheel。工具校验当前源与 wheel 的完整 Python／SQL／静态文件集合和每个文件的 SHA256，不解压或执行 wheel：

```bash
rtk proxy python3 services/platform/scripts/build_console.py
rtk proxy uv build --project services/platform --wheel --out-dir /private/tmp/easel-central-release
rtk proxy python3 services/platform/scripts/prepare_deployment.py --config /private/tmp/central-public.json --wheel /private/tmp/easel-central-release/easel_central_platform-0.1.0-py3-none-any.whl --output /private/tmp/easel-central-deployment-new
```

输出目录必须此前不存在；工具不会覆盖旧包。每次发布使用新目录／版本名，记录 wheel SHA256、生成文件 SHA256 和源清单。生成包包含：

- `deployment-report.json`：离线结果、公开地址、源／wheel 哈希和实际部署待办；不含真实凭证。
- `easel-central-production.service`、`easel-console-production.service`：独立单进程 unit，后端固定 `127.0.0.1`，独立非 root 用户，512 MiB／256 MiB 内存上限。
- `nginx-central.conf`：API 与 Console 的 HTTPS 站点片段，固定规范 Host，保留 Origin，关闭响应缓冲，不放行通配 CORS。
- `central.env.template`、`console.env.template`：需要由服务器运营者填入的配置字段；`REPLACE` 值不是可启动配置。

该工具不验证 Python 依赖包是否可安装，也不验证远端镜像或证书。发布前应从本服务 `uv.lock` 导出完整、带哈希的运行依赖，在全新隔离 venv 中安装这些依赖和本次 wheel，然后使用合成配置构造 API 与 Console。不能复用旧 `/tmp` 预览环境来证明干净安装。

## 目标主机与资源核验

首先核对准确服务器身份、IP、OS、4 核／4 GB 资源、磁盘空间、当前 New API／数据库／代理、端口和已有服务名称。确认三个公开域名的 A／AAAA 记录和证书实际指向同一计划环境；A 记录正确但错误 AAAA 仍可能导致一部分用户访问错误主机。

首版采用 API 单进程，Console 独立单进程，数据库连接池分别不超过 5 和 2。现有上游并发控制部分是进程内资源，不能直接通过增加 Uvicorn workers 宣称集群限额不变。512／256 MiB 是本包服务上限，不是整机容量保证；Logto、两个数据库和 New API 必须一起实测内存、CPU、连接、磁盘和并发。未完成压测前不承诺吞吐或可用性等级。

不把已有 New API 数据卷、凭据、代理站点或用户工作区纳入新服务目录。数据库只监听受控内部网络。新 release 目录由部署者持有并对 Web 用户只读；API 使用 `easel-central`，Console 使用 `easel-console`，两者都无 sudo、无共享可写代码目录。检查目标系统的 systemd 版本是否支持 unit 中所有隔离指令。

## Logto 与原账号的迁移门槛

Logto 必须迁到服务器长期运行，使用独立身份数据库、固定已验收版本和独立凭据。认证域名为 `login.drawbookai.cn`；Logto 管理端保留在 VPN／受控运营网络，不作为普通用户入口。配置 `ENDPOINT`、独立 `ADMIN_ENDPOINT` 与 HTTPS 代理信任，精确核对 discovery 的 `issuer`，分别登记产品和 Console 的 Traditional Web 应用回调：

```text
https://accounts.drawbookai.cn/oidc/easel/callback
https://console.drawbookai.cn/oidc/console/callback
```

不能只把原本机 Logto 数据库复制过去，再改 `CENTRAL_OIDC_ISSUER` 就开放登录。产品用户和管理员都按精确 `(issuer, subject)` 绑定；新 issuer 无映射会产生新用户／钱包或新的管理员申请。S5-A2 已补充受控同 Logto 恢复比对、owner-only 迁移命令与保留原 admin ID 的 alias；正式使用前须按[身份连续性手册](../IDENTITY_MIGRATION.md)核对真实结构、实际恢复、写入冻结与本人新 issuer 登录。S5-A1 审计中记录的缺口属于当时版本，不能替代本批验收。

实际迁移必须先形成受审的一对一身份清单，以可信 Logto 数据迁移并完成新 issuer 的本人登录验收，保留原中央 user、tenant、billing account、admin ID、角色状态和历史审计。禁止按邮箱／用户名自动合并、复制 bearer、删除旧身份、重建余额或重发体验点数。禁用管理员不能因迁移重新启用。历史背景见 [S5-A1 身份迁移审计](../../../docs/validation/2026-10-23-central-production-identity-audit.md)，当前代码与隔离结果见 [S5-A2 验收](../../../docs/validation/2026-10-23-central-identity-continuity.md)。

现有中央和 Console 会话不会因为改 issuer 自动全部失效；切换方案必须明确停止新登录、保留／撤销旧会话、在途任务和待核账记录的处理。旧入口暂时保留可达，客户端需先停止任务、在旧地址正常退出，再更新中央地址并重新登录；不能先拆旧入口，或修改本地 Pi 会话绑定来续接旧任务。

## 私有配置和数据库权限

部署者将 env 模板逐一配置为服务器私有普通文件，权限 0600，分别属于对应服务用户；目录不得允许另一 Web 用户读取。模板本身不启动服务。

- API env 仅包含 runtime DSN、产品 OIDC secret 及实际需要的私有模型配置路径。初始支付 `disabled`，不随模板发体验额度。
- Console env 使用 Console 独立受限 DSN 和独立 OIDC secret，不包含产品模型业务 Key、支付私钥、owner DSN 或运营发点凭据。
- operator 文件单独保管，只给一次性维护命令使用，不挂载到常驻进程，不交给 Web 用户。
- 远程数据库使用 `sslmode=verify-full` 和受信 CA；主机 loopback 数据库也应确认认证与访问范围，不能仅因连接可用就认为角色安全。

服务启动不自动执行迁移。对明确目标数据库与 schema，先核对现有迁移 SHA，再使用独立 owner 执行 `migrate`。随后分别执行 `grant-runtime`、`grant-console`，以真实低权角色运行两种现有权限守卫。新环境才做必要的 `register-product`；恢复已有数据时不重复初始化账号、钱包或赠送。现有 CLI 参数见上级 README 和 CONSOLE.md。

## 备份与隔离恢复演练

先在私有维护清单精确绑定原中央权威 DB／schema。首发默认复用原账本，下面的恢复数据库仅作隔离演练，不作为第二套生产可花余额。如果原数据库设施不适合长期运行，先停在准备阶段，补受审的数据库迁移与写入截止方案，不能静默启用恢复副本。

切换前先关闭所有产品新准入、新购买、新模型派发和新登录，保留并核查在途／pending，等待或记录晚到结算与 recovery 的最终状态。客户端在旧地址正常退出后，停止旧 API、Console 目录写入和相关后台 writer，核查数据库会话、角色准入及服务管理器不会重启旧版本，记录最后写入的核账截止点和账本摘要。只有确认旧版本不能继续写账，才做最终备份与迁移并开放唯一的新入口；停止新派发本身不足以实现这一点。当前 CLI 没有一键 drain/fence 模式，实施前必须准备明确且受审的运营步骤。

备份中央业务库、Logto 身份库、数据库角色／权限和必要安全配置；所有归档、角色文件与检查结果留在受控私有目录，不上传公共验收报告。

数据库连接使用私有 `PGSERVICEFILE`／`PGPASSFILE`，避免将密码 DSN 放进命令参数。恢复必须指向**全新隔离的目标**，不能指向仍承载旧钱包消费的数据库。操作前由维护者核对 service 名对应的主机、数据库及角色；下面仅说明工具与隔离关系，不由生成器执行：

```bash
umask 077
PGSERVICEFILE=/etc/easel-maintenance/pg_service.conf PGPASSFILE=/etc/easel-maintenance/pgpass pg_dump --dbname=service=central_source --format=custom --file=/var/backups/easel-private/central-before-cutover.dump
PGSERVICEFILE=/etc/easel-maintenance/pg_service.conf PGPASSFILE=/etc/easel-maintenance/pgpass pg_dump --dbname=service=identity_source --format=custom --file=/var/backups/easel-private/logto-before-cutover.dump
PGSERVICEFILE=/etc/easel-maintenance/pg_service.conf PGPASSFILE=/etc/easel-maintenance/pgpass pg_restore --dbname=service=central_restore_isolated --exit-on-error --single-transaction --no-owner --no-acl /var/backups/easel-private/central-before-cutover.dump
PGSERVICEFILE=/etc/easel-maintenance/pg_service.conf PGPASSFILE=/etc/easel-maintenance/pgpass pg_restore --dbname=service=identity_restore_isolated --exit-on-error --single-transaction --no-owner --no-acl /var/backups/easel-private/logto-before-cutover.dump
```

角色、schema 所有权和原必要权限要另外备份与重建，不能把 `--no-owner --no-acl` 当成已保留权限；重建已记录的 owner／ACL 后才运行身份 collector。此处 pg_dump／pg_restore 的私有 servicefile 合同与 collector 的显式 DSN 配置不同，后者拒绝 service／passfile 间接解析。Logto 备份后先用同一固定版本恢复，不在迁移主机的同时升级身份库版本。dump 可解码校验不等于实际恢复演练。

恢复后核对中央 user／tenant／钱包 ID、身份映射、产品授权、会员、所有资金桶、lots／到期、journal／entry 平衡、预留／pending、订单／支付队列，以及管理员 ID／active／role／ACL 事件。比较公开报告只保存摘要和计数；真实身份清单私有保管。恢复数据库默认不开放消费；不能把旧快照直接当成最新生产可花余额。未来若选择替换权威 DB，必须证明恢复包含最终核账截止点后的完整事实、旧库所有 writer 已被阻断、只有一个权威目标开放准入；未知费用不能为迁移而清零。

## 服务启动与无消费验收

完成身份迁移方案、恢复演练和安全配置后，才审阅并安装 unit／代理片段。先检查生成器记录的 wheel 和所有待安装文件 SHA，按锁定依赖安装到新 release 的 `.venv`，保持 release 对运行用户不可写。不要用源码可写目录或旧预览 venv 替代正式 release。

证书提供给 `certificate_dir` 指定路径，API／Console 各自证书有效且主机匹配。站点片段先运行目标代理的配置检查；不得覆盖全局 default_server、现有 New API 站点或证书续期作业。Nginx 的 access log 关闭，error log 不记录可能包含授权码的请求行；进程状态、DB 和 worker 告警另外以不含原始请求的指标提供。

以下检查都不应产生模型消费、付款或发点：

| 检查 | 必须观察的实际结果 |
| --- | --- |
| API `/health` | 受验证 HTTPS、规范 Host 下 200；只证明数据库连接 |
| Console `/health` | 200、`assets_ready=true`，另核验完整静态资源清单 |
| API `/v2/me`、Console `/api/session` | 匿名 401 |
| 错误 Host／跨站 Origin | 按应用规则拒绝；不通过删 Origin 或通配 CORS 绕过 |
| Logto discovery | HTTPS 正常，issuer 精确等于配置，端点不依赖本机 SSH |
| `/v2/capabilities?product_id=easel` | 登录配置就绪、支付 disabled；仅已配置且验收过的模型开放，媒体／云端待接入 |
| DB 权限与迁移 | runtime／Console 实际低权校验通过；迁移全部 SHA 匹配 |
| 身份迁移真实登录 | 本人重新登录后仍为原 user／wallet，管理员仍为原 admin／role；需要单独记录 |
| 重启与恢复 | 系统重启后服务恢复、日志与磁盘受限、备份实际恢复后核账；各自记录，不能由 health 推导 |

真实文本模型、实际管理员目录写操作、正式商户支付／退款另做有界验收，不纳入日常无消费健康探测。未部署 metrics／告警前不宣称已具备生产可观测性。

## 回退

身份／源文件／账本比对失败时不开放新环境。保留旧入口和历史映射；恢复配置或回退兼容的代码版本，禁止回滚数据快照继续花费、清空 pending、删除审计或重建钱包。已有新环境消费时必须先停止准入、保留最新事实并作前向核账，再决定恢复路径。

本批不执行这些正式切换步骤。实际进度以 [本批验收记录](../../../docs/validation/2026-10-23-central-production-preparation.md) 为准；离线配置生成、真实恢复、公开 TLS、真实登录和商业收款是独立证据。

## 官方部署依据

Logto 的 `ENDPOINT` 会影响 issuer；反向代理时认证／管理端分别处理，HTTPS 代理下配置 `TRUST_PROXY_HEADER`。本批通过 Context7 核对 [Logto OSS 部署配置](https://github.com/logto-io/docs/blob/master/docs/logto-oss/deployment-and-configuration.mdx) 和 [OSS 入门](https://github.com/logto-io/docs/blob/master/docs/logto-oss/get-started-with-oss.mdx)。这些文档没有替本项目完成账号别名、共享钱包或管理员审计迁移。
