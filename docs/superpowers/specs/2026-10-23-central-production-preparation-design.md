# 中央平台正式部署准备设计（S5-A1）

日期：2026-10-23。分支：`codex/ui-redesign`。用户在架构与进度汇总、正式部署优先的建议后回复“继续”，本批据此推进部署准备；不合并主分支、不自动提交或推送。本批交付可审阅、可离线验证的部署包，不把配置已确认或未经验证的身份迁移当成已完成发布。

用户随后确认：中央 API `accounts.drawbookai.cn`、后台 `console.drawbookai.cn`、登录 `login.drawbookai.cn`，统一目标为既有 `115.190.211.112` 主机。最初公共 DNS 中 console／login 指向另一主机；用户已回复完成两条独立 A 记录修改，实际解析传播与 TLS 仍需复核。

## 1. 范围与方案

沿用已确认的独立中央服务、Logto、PostgreSQL、Pi 本机执行及自定义／订阅双渠道。首个生产目标仍为既有 4 核 4 GB 主机，支付初始 disabled，媒体与云执行保持待开放。

比较三种部署路线：全量容器化会要求改动现有强制 loopback 的 Console 部署合同；继续临时 SSH／本机 Logto 无法长期独立运行；本批选择 **API 与 Console 使用独立 systemd 单进程，现有 HTTPS 代理转发到主机 loopback，Logto 单独迁至服务器**。保持应用原有 Host／Origin／CSRF 和低权限数据库边界，避免为部署放宽认证。

本批实现：

1. 无机密的部署 JSON 合同和 stdlib 离线检查／生成工具。
2. 对本次 wheel 内 Python 源码、001–009 SQL、Console 全部静态资源逐文件比对，并输出公开 SHA256。
3. 生成 API／Console systemd unit、Nginx HTTPS 站点片段和不含真实机密的独立 env 模板。
4. 输出明确为 offline-only 的检查报告，列出 DNS／TLS、真实 Logto、数据库权限、身份迁移及恢复演练等未验证门槛。离线通过永远不等于 production_ready。
5. 运行自动化测试、构建和新环境装包检查，形成独立验收记录与实际切换／回退手册。

本批不执行 DNS 修改、证书申请、反向代理 reload、systemd install/restart、备份真实数据库、迁移身份／余额、发放点数或调用供应商。域名和目标已确认；实际解析／证书、身份映射迁移、唯一账本写入者和恢复演练仍是正式切换门槛，不影响本批部署包开发。

## 2. 部署合同与安全输出

公开 JSON 仅接收固定字段：`version`（1）、`public_url`、`console_public_url`、`oidc_issuer`、`source_oidc_issuer`、`api_port`、`console_port`、`release_dir`、`config_dir`、`certificate_dir`。URL 不携带用户名、密码、query、fragment；正式三个服务采用不同 hostname，公开 API／Console 为无路径 HTTPS origin，issuer 为精确 HTTPS `/oidc` URL。源 issuer 仅用于迁移提示，允许原始 literal loopback HTTP，不用于信任或认证。

端口仅可为 1024–65535 且不同；后端绑定硬编码为 `127.0.0.1`。路径为规范绝对 ASCII 路径，拒绝空白、控制字符、`..`、shell/systemd/Nginx 插值字符。配置输入最多 64 KiB，拒绝重复／未知字段。工具不读取真实 env、DSN、供应商配置或客户端会话。

输入与输出都不会包含口令、客户端 secret 或供应商业务令牌。输出到一个此前不存在的新目录，权限 0700；文件用排他创建并拒绝覆盖已有目录，错误只返回固定代码，不打印原始 JSON、wheel 内容或异常。工具不执行任何 shell／安装／数据库／网络命令。

源 issuer 与目标不同必须在报告标记 `identity_migration_required`；输出不接受“已批准迁移”布尔值来消除这个门槛。正式身份迁移需要另行具备可信的原始 Logto 数据库、用户 subject／应用 ID 保留证明、原中央 ID 和钱包不变对比、管理员精确授权与新登录验证。

## 3. 版本与配置

wheel 必须是完整中央服务构建：Python 源、现有全部迁移、前端 dist 静态文件集合及字节与当前仓库一致；拒绝重复 ZIP 条目、异常路径、超出边界大小、缺文件和额外服务源码。SHA 检查证明包与当前源一致，不证明第三方依赖安全、真实商户就绪或公网可达。

API 与 Console 各自使用固定非 root 系统账号、独立 env 和池上限；不加载 owner 配置。unit 强制隔离 Python 用户环境、只读系统目录、私有 tmp、重启限速和内存上限。Console 不获得产品供应商密钥。env 模板明确需要在服务器 0600 私有文件配置，不能直接作为可启动凭据。

Nginx 片段固定唯一 Host、保留 Origin，不引入通配 CORS；API 请求体 1 MiB，Console 64 KiB；流式转发关闭缓冲，超时覆盖应用上游期限；不记录请求行、callback query、Cookie、Authorization 或正文。证书文件由运营者提供，不发起自动申请；不包含全局 default_server 或对现有 New API 站点的修改。Logto HTTPS 部署依官方 `ENDPOINT`、`ADMIN_ENDPOINT` 和 `TRUST_PROXY_HEADER` 合同单独配置，管理端仅给受控运营网络访问。

## 4. 身份与财务切换边界

现用户身份以 `(issuer, subject)` 精确绑定，管理员 ACL 也依赖此组合。修改 Logto `ENDPOINT` 会改变 issuer；直接改中央 issuer 后重新登录可能新建用户／钱包。禁止用邮箱匹配、复制 bearer、删除旧身份、重发体验点数或直接 UPDATE 余额解决迁移。

真实切换先以私有维护清单绑定原中央权威数据库／schema。首发默认继续复用这一个账本，隔离恢复副本只供恢复演练，不开放生产消费；若原基础设施不能长期保留，本批禁止直接切换到副本，需要先补专用数据迁移方案。

冻结所有产品新准入，停止旧版本新登录／购买／模型派发；排空或明确记录在途任务、late settlement、recovery 和 pending。客户端在旧地址完成退出后，停止旧 API、Console 写操作及相关后台 writer，核查旧 DB 会话和角色准入，记录最终核账截止点／摘要，确认旧版本无法再改账，才备份、迁移与启用新版。不能把“停止新派发”当作已阻断旧结算。现有 CLI 尚无一键 drain/fence 模式，实际切换必须先实现或审阅明确的运营步骤，生成器不伪造该能力。

备份中央数据库、Logto 数据库、角色与安全配置。在隔离恢复目标实际恢复并比对原中央用户 ID、tenant、钱包、账本、会员、管理员 ACL 及 pending。新 issuer 映射需要受审迁移方案，当前工具仅提示阻挡，不实施映射。即使未来获准启用恢复副本，也必须先以最终写入截止点证明原库不再写、恢复包含完整最终历史、仅一个权威目标开放消费，不能出现两套可写钱包。

代码回退仅在与当前数据库合同兼容时回退服务版本。共享钱包已经消费后，不把旧备份恢复为可继续花费的生产余额，不清除未知费用；正式数据恢复需要前向核算和重新准入检查。

## 5. 验收

自动化覆盖配置注入／异常 URL／相同 origin／不安全路径、端口冲突、重复字段、未知键、wheel 篡改／缺失／静态文件集合、排他输出、错误脱敏、HTTPS／loopback／非 root／来源隔离和 SSE 配置。干净环境仅安装构建产物及锁定依赖，验证 API 与 Console 导入及构造，不连接实际数据库或供应商。

本批报告分别列出离线自动化、构建／装包、真实域名／TLS、真实登录、迁移／恢复、真实模型／付款。后五项没有实际执行则写未验证。现有预览数据、服务、用户工作区和自定义接口不纳入本批修改范围。

## 官方资料

本轮通过 Context7 查询 Logto 官方资料：[OSS 部署与配置](https://github.com/logto-io/docs/blob/master/docs/logto-oss/deployment-and-configuration.mdx)、[OSS 开始使用](https://github.com/logto-io/docs/blob/master/docs/logto-oss/get-started-with-oss.mdx)。官方明确 ENDPOINT 影响 OIDC issuer，HTTPS 代理下设置 TRUST_PROXY_HEADER，认证与管理 endpoint 分开转发。该资料不替代本项目的身份映射迁移或恢复演练。
