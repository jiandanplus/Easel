# New API 升级、插件安装与 Easel 接入执行记录

日期：2026-10-20。分支：`codex/ui-redesign`。用户已授权检查并升级现有New API、安装官方市场全部插件，以及为Easel模型接口缺口开发适配。此授权不表示中央会员平台P0已经实施，也不表示允许合并主分支。

## 目标与边界

目标服务器限定为 `volcengine-4h4g / 115.190.211.112 / iv-ye8aaj2u4gcva4gofev6`。保留定制Compose、数据库、Redis、密钥、管理员和1Panel/OpenResty。只升级New API应用镜像，不升级PostgreSQL、Redis或面板。没有供应商渠道时，不创建虚构渠道、价格或测试令牌，不声称真实模型调用通过。

## 已核对的输入

- 通过已有私密凭据及1Panel正常加密登录建立管理连接；无需修改SSH授权或关闭安全入口。
- 线上实际为v0.13.2，三个业务容器健康；磁盘剩余约27GB；users=1、channels=0、tokens=0、未完成tasks=0。
- 官方Latest为v1.0.0-rc.45，名称仍为RC。固定宿主commit与镜像摘要见 `integrations/newapi/catalog.lock.json`。
- 当前官方市场13个插件；候选宿主内置11个，与市场版本/hash相同；另外安装Incho、TypeSafe。
- 公网域名在当前访问路径TLS握手失败，HTTP 302 指向火山引擎 `webblock.volcengine.com`；服务器通过回环地址和正确SNI访问HTTPS正常。具体拦截原因待云账号核对，两者分别验收。

## 执行清单

- [x] 读取运维手册和已有私密访问记录，核对目标身份、版本、容器、端口、数据规模。
- [x] 锁定官方release/镜像/插件源码commit与SHA256，避免浮动latest。
- [x] 预拉唯一目标应用镜像；拉取期间原应用继续运行。
- [x] 维护窗口停应用/Redis，保存PG逻辑备份、完整Redis冷备、配置隐藏文件、应用数据/日志和网站代理。
- [x] 校验SHA、pg_restore目录，恢复到独立验证库并检查基础对象；写COMPLETE后才改镜像。
- [x] 只修改new-api镜像并重建应用；重新验证版本、原有密钥/配置、数据库/Redis健康和端口边界。
- [x] 正常root认证后核对11个factory并安装2个附加官方插件，读回版本/hash/runtime。
- [x] 新增私有音乐、声音和变体适配，按供应商文档合同及假上游测试；Fish二进制桥已实现并通过本地ASGI验证，缺供应商凭据未部署。
- [x] 对自研插件做Node与真实rc.45/moejs校验；安装后读回并通过126个无上游调用的dryrun。
- [x] 将Easel图片变体、视频、音乐和语音的客户端协议差异按显式网关模式处理，继续保留自定义供应商；139项客户端与旧Agnes回归通过。
- [x] 汇总线上证据、本地测试、真实供应商未测项和未覆盖接口，保存实际验收记录；真实模型、Fish桥部署和公网解除拦截仍待各自运行条件。

## 新增文件责任

`integrations/newapi/catalog.lock.json`保存可复核官方清单；`ops/install_plugins.py`提供严格root认证、哈希与配置保护；`ops/upgrade_newapi.py`提供限定目标和备份门槛。各`plugins/<key>/<version>/`独立保存发布源、fixture、测试和协议边界。Fish二进制如果采用私有桥，独立部署且不新增公网端口。

## 验证规则

CLI lint/fixture只证明真实JS引擎与hook协议；插件注册只证明宿主加载；渠道、供应商调用、文件播放和真实计费必须有各自证据。未提供供应商密钥时不会将离线成功升级为真实调用通过。线上修改前必须有可恢复备份，不自动把旧镜像连到已经迁移的新库。既有UI文件的SHA和main ref在完成时再次比较。

## 实际收据

[服务器升级与插件验收](../../validation/2026-10-20-new-api-upgrade-plugins.md)及[脱敏机器读回](../../validation/2026-10-20-new-api-server-receipt.json)记录备份、恢复库、18个插件、运行时、端口和公网限制。[本地独立审查](../../validation/2026-10-20-new-api-private-plugins-http-review.md)记录假供应商HTTP、结算与客户端安全验证，不充当真实供应商验收。
