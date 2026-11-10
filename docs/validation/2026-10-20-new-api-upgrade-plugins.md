# New API 升级与 Easel 插件接入验收

日期：2026-10-20。开发分支：`codex/ui-redesign`。目标服务器：`volcengine-4h4g / 115.190.211.112 / iv-ye8aaj2u4gcva4gofev6`。最终服务器检查时间：`2026-10-20T09:15:19Z`。

**已实际将服务器从 v0.13.2 升级为固定镜像摘要的 v1.0.0-rc.45；13 个官方市场最新版插件及 5 个 Easel 私有插件均注册成功，126 个在线 hook 样本通过。服务器没有供应商渠道，真实模型生成与供应商计费尚未验收。** 公网域名另受火山引擎拦截，内部 HTTPS 健康不能代替公网可用性。

机器读回证据保存在[脱敏服务器收据](2026-10-20-new-api-server-receipt.json)。各能力的使用入口、模型和限制见[集成说明](../../integrations/newapi/README.md)。原[管理员运维手册](https://chatgpt.com/space/page_3f3e66622538819180b138734ac4badc)记录的是 v0.13.2 部署基线；本记录作为本次升级补充，未覆盖其历史正文。

## 服务器实际状态

| 检查项 | 结果与证据范围 |
| --- | --- |
| 应用版本 | v0.13.2 → v1.0.0-rc.45；后者是核对时官方 Latest，标签名称仍为 RC |
| 应用镜像 | `calciumion/new-api:v1.0.0-rc.45@sha256:6c63c7ee9cd5bf7880df3984e99dd3ef07992d7a97538cfb2719adf9d87bc3f9` |
| 宿主源码 | `6cf0d90b5ff27f901697dddd61e0c28aba8efefc` |
| 三个容器 | `1panel-new-api`、`1panel-new-api-db`、`1panel-new-api-redis` 均 running / healthy |
| PostgreSQL / Redis | 保留 `postgres:17-alpine`、`redis:7-alpine`；Config、HostConfig、Mounts、Image 的组合哈希与升级前一致 |
| Compose | 仅替换应用的唯一 image 行；其余字节与有效配置在升级工具内比对，数据库、Redis、网站代理和原有密钥未重配 |
| 端口 | 应用仅 `127.0.0.1:3000`；PostgreSQL、Redis 没有发布主机端口 |
| 业务数据 | users=1、channels=0、tokens=0、tasks=0 |
| 最终有效设置 | `RegisterEnabled=false`、`TaskPluginEnabled=true`、`RetryTimes=0`；本批只读取验证，没有改写这些设置 |
| 未授权访问 | 无业务令牌访问 `/v1/models` 及私有 Fun-Music 提交均返回 401；没有借此创建令牌或付费任务 |
| 管理连接 | 使用原有私密记录，经 1Panel 正常加密登录和已认证管理 API 操作；未修改 SSH 授权或关闭安全入口 |

版本来源：[官方 rc.45 发布](https://github.com/QuantumNous/new-api/releases/tag/v1.0.0-rc.45)。GitHub 的 `prerelease:false` 不改变标签仍名为 RC 的事实。版本、官方插件源码 commit、图标和源文件 SHA256 均保存在 [catalog.lock.json](../../integrations/newapi/catalog.lock.json)。

## 已执行的备份和恢复验证

实际备份目录：`/srv/backups/new-api/20261020T085002Z-af86b3d1`，由 root 私有目录保护。先预拉固定摘要镜像，再停应用和 Redis，PostgreSQL 保持运行，完成以下备份后才替换应用镜像：

- `postgres.dump`：`pg_dump -Fc` 逻辑备份，执行 `pg_restore --list`。
- `configuration.tar`：含隐藏文件的原始应用配置，排除单独备份的数据目录。
- `data.tar`、`logs.tar`：应用数据与日志。
- `redis-data.tar`：停 Redis 后完整冷备，包括 AOF 子目录和 manifest。
- `website.tar`：完整 `/opt/1panel/www/sites/new-api`，保留网站和代理配置。

备份已恢复到独立数据库 `newapi_verify_20261020t085002z_af86b3d1`。源库与恢复库的 users=1、channels=0、options=4 一致。验证库保留，没有执行 DROP；原应用镜像也保留，没有 prune。

最终只读检查再次验证全部六个文件的 SHA256、`COMPLETE` 所记录的 manifest SHA256、恢复验证标记和对应表计数。具体摘要见收据。这证明 PostgreSQL 逻辑备份可恢复以及所比较对象一致；没有执行完整应用加 Redis 的灾难恢复切换，也没有自动数据库回滚。后续恢复应参考私密备份目录的 `RESTORE.md`，不能把旧镜像直接接到可能已经迁移的新数据库。

可复核工具：[升级工具与恢复边界](../../integrations/newapi/ops/UPGRADE.md)、[插件安装工具](../../integrations/newapi/ops/README.md)。

## 插件安装与在线验收

官方市场固定提交 `423535f2c6d46171d2e9553c6d33a0987116aa63` 中，每个 key 的最新版本共 13 个：Alibaba、Doubao、Google、Hailuo、Incho、Jimeng、Kling、Sora、SunoAPI、TypeSafe、Vertex AI、Vidu、xAI。其中 11 个随 rc.45 内置，源哈希与市场一致；另上传 Incho 1.0.2、TypeSafe 1.2.0。没有为相同 factory 插件重复创建 override，也没有把每个历史版本全部安装。

为 Easel 缺口增加以下私有插件，各为 1.0.0，固定源哈希见 [private-plugins.lock.json](../../integrations/newapi/private-plugins.lock.json)：

| 插件 | 本次实现的合同 | 在线 hook 样本 |
| --- | --- | ---: |
| `easel-dashscope-music` | 当前同步 Fun-Music 提交、公共任务查询、音频产物及真实 duration 用量 | 25 |
| `easel-dashscope-voice` | create → verify → synthesize，音色私有 ID 保留在宿主私有字段，后续请求引用公共任务 | 35 |
| `easel-minimax-voice` | 样本上传 → 声音登记 → 短文本 JSON/hex 合成，使用公共引用和原渠道绑定 | 29 |
| `easel-image-variations` | 私有 multipart variations 路由，适配仍提供旧合同的兼容供应商 | 16 |
| `easel-fish-tts` | 私有桥 JSON 收据与公共 artifact content 合同；桥尚未部署 | 21 |
| 合计 | 无真实供应商请求的宿主 hook 回放 | 126 |

最终读回全部 18 个 key 的版本、source_hash、active、enabled 和 `runtime_status=registered`，运行时 generation=19，数据库与插件错误列表为空。在线 dryrun 采用真实 rc.45 宿主 JS 引擎，不会请求供应商。

Fish fixture 原先使用固定 `unixNow=1000`，直接在在线实时时钟中回放会把成功样本判为过期。续验仅将样本及预期中的 `expires_at` 按相同差值平移，保留未来和已过期的相对关系；没有修改发布插件源码或哈希。第一次部分验收结果未当成全部成功，后续逐项回放和最终读回全部完成。

## Easel 客户端和本地独立审查

音乐、语音、视频与图片变体采用显式 New API 模式；未选择该模式时保留原有自定义供应商行为。图片仅将 variations 接入私有路由，不改变其他生成/编辑分支。Gemini TTS 默认使用有实现的 Generate Content speech 路径，显式 Interactions 路径继续保留。

- [音乐与声音配置、公共 ID 及阶段恢复](../platform/new-api-media-client.md)
- [视频配置、五个显式 profile 与 content 下载](../platform/new-api-video-client.md)
- [Gemini TTS 路由与音频格式](../platform/gemini-tts-new-api.md)
- [图片变体的私有入口与兼容供应商限制](../platform/new-api-image-client.md)

本地独立审查使用官方 rc.45 根模块、隔离 SQLite、假 HTTP 供应商，实际验证 Fun-Music 与 CosyVoice 的数据库终态、所有者查询、跨用户拒绝、原渠道绑定和计费事实覆盖。四笔成功任务的 quota 总扣费为 655000，与逐笔 615000 / 5000 / 5000 / 30000 一致。它是宿主与假上游的合同证据，不是真实采购金额或真实供应商计费。

审查发现并关闭音乐重定向转发 token、声音中间阶段 ID 未交付、视频下载短读误报成功三项问题；修复有独立 HTTP 复现与复验。声音 `--resume-id` 能跳过已完成的上传/create，不能自动恢复结果未知的同步付费 POST。完整证据及本机复现路径见[独立审查报告](2026-10-20-new-api-private-plugins-http-review.md)。

新鲜本地验证为：运维工具 52 项、五个插件 Node 50 项、Fish 桥 ASGI 23 项、图片/音乐/语音/Gemini/视频与原 Agnes 图像客户端联合 139 项通过。客户端测试含自己的随机回环服务器，无真实供应商调用。[本地验证收据](2026-10-20-new-api-local-validation.json)保留具体命令和证据类别。

## 当前未开通的运行条件

1. **供应商渠道与价格尚未配置。** 数据库实际 channels=0、tokens=0，没有自行创建测试密钥或猜测供应商价格。插件注册不能代替上游账号授权、模型支持、计量和实际生成验收。Codex Responses 的完整工具往返仍须在真实可用渠道上独立验证。
2. **Fish 桥尚未部署。** 插件已注册，私有桥源码、ASGI 测试及无公网端口模板已完成；缺少供应商凭据时不启动一个伪可用服务。桥容器构建、私网网络、真实音频和供应商扣费均未验收。
3. **保留合同限制。** MiniMax 超出 JS 安全整数范围且以 JSON number 返回的 file_id 会拒绝，必须有可保持精度的字符串合同；短音频受宿主结果体大小约束，当前不是完整长音频方案。视频仅核对五个 profile 的明确子集，不代表对应平台每种高级参数都已支持。
4. **已退役上游不能通过插件恢复。** OpenAI DALL·E 2/3 于 2026-05-12 停用，官方 variations 端点已退役；Sora/Videos API 于 2026-09-24 停用。已安装的相关协议插件只可用于实际仍提供对应合同的兼容渠道。新的 GPT Image edits 工作流应作为另一项功能设计。[官方弃用表](https://developers.openai.com/api/docs/deprecations)、[variations 端点说明](https://developers.openai.com/api/reference/resources/images/methods/create_variation)
5. **同步任务的未知消费需要对账。** 已验证生产 `RetryTimes=0`。本地实证将其设为 2 会在某些缺失 usage 场景造成重复上游 POST；本次没有变更该值。宿主内部退款不证明供应商消费为零，完整中央 pending/采购对账仍待后续平台开发。

## 公网域名限制

内部通过正确域名/SNI、启用证书验证且将解析定位到 loopback 的 `https://api.drawbookai.cn/api/status` 成功，版本正确。没有关闭 TLS 验证、修改系统 DNS 或将 3000 暴露到公网。

当前外部访问证据为：

```text
GET/HEAD http://api.drawbookai.cn/
HTTP/1.1 302 OK
Server: Suzaku
Location: https://webblock.volcengine.com

https://api.drawbookai.cn/api/status
TLS connection closed before HTTP response
```

这确认公网 HTTP 被火山引擎导向拦截页；具体原因尚未通过云账号状态确认，不能仅据重定向断言备案、接入或其他具体违规原因。需要在火山引擎控制台核对该域名的公开访问限制与相应通知，再从外部重新验收 HTTPS。当前没有绕过云厂商限制或以更换端口代替处理。

## 分支与配置保留

所有代码仍在 `codex/ui-redesign` 工作区，未提交、未推送、未合并主分支。预先存在的 12 个 UI 修改文件 SHA256 与本次执行前基线一致，`main` ref 仍为 `bb3c8ff0463f36c202454d74171682ba6e470cb1`。没有改写产品 `.env` 或自动将用户切换到网关模式。

本批工作是现有 New API 的维护与模型协议接入，不表示 Logto、跨产品统一会员、共享额度、中央持久账本或 4 核 4GB 性能目标已经实现或测得。
