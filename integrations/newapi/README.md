# Easel × New API：接入入口与当前能力

本目录保存 Easel 的 New API 版本锁、插件、私有音频桥和运维工具。**截至 2026-10-20，本次目标服务器已从 `v0.13.2` 升级到 `v1.0.0-rc.45`，13 个官方市场插件与 5 个 Easel 私有插件均已完成生产注册、源码哈希和 runtime 读回；5 个私有插件的 126 个在线 fixture 重放通过。** 该版本仍是 RC，不是正式 v1 stable。

当前供应商渠道数为 **0**。因此，已完成的是宿主升级、插件部署和确定性合同验证；真实模型调用、音色审核、音视频播放、供应商费用和商业收费流程仍待配置并验收。Fish 桥尚未部署。图片、视频、音乐、语音的显式客户端适配已完成本地回归，原有自定义接口继续保留。

**2026-10-21 客户端设置更新：** 已增加“设置 → 模型配置 → 统一媒体网关”，地址与业务密钥填写一次，再按能力选择网关或保留的自定义接口。统一配置使用独立 `NEW_API_*` 字段，仅在各媒体进程内派生所需参数。见[设置使用说明](../../docs/platform/media-gateway-settings.md)和[本地验收记录](../../docs/validation/2026-10-21-media-gateway-settings.md)。本次没有连接或修改生产 New API，以上服务器状态仍为 10 月 20 日的收据。

## 状态、锁定与证据入口

| 入口 | 用途 |
| --- | --- |
| [生产升级与插件验收](../../docs/validation/2026-10-20-new-api-upgrade-plugins.md) | 本次服务器执行、备份、恢复验证、插件读回和外部连通性结果 |
| [脱敏服务器收据](../../docs/validation/2026-10-20-new-api-server-receipt.json) | 本次最终只读核查的结构化事实 |
| [官方 catalog 锁](./catalog.lock.json) | 宿主 tag/commit/镜像摘要，13 个市场插件的版本、固定源码 URL、SHA-256 |
| [私有插件锁](./private-plugins.lock.json) | 5 个私有插件的版本、源码路径、SHA-256 和 fixture 路径 |
| [发布物与变更说明](./RELEASE.md) | 选定版本的依据、RC 标识、官方市场清单、升级变化和管理 API 合同 |
| [安装工具](./ops/README.md) / [升级工具](./ops/UPGRADE.md) | 只读预检、显式执行和读回协议；工具说明不是每次执行的收据 |
| [独立本地 HTTP 审查](../../docs/validation/2026-10-20-new-api-private-plugins-http-review.md) | 假上游配合真实 rc.45 宿主的隔离、账务、重试和客户端安全验证 |
| [最初接口审计](../../docs/validation/2026-10-19-new-api-easel-interface-audit.md) / [插件评估](../../docs/platform/new-api-plugin-evaluation.md) | 接入前的差异及演进背景；实际部署状态以本页和生产收据为准 |

各子目录 README 保留了相应开发阶段的验收范围。本页汇总后续现场部署状态；各模块的输入限制和技术合同继续以对应说明为准。

生产最终核查时间为 **2026-10-20 09:15:19 UTC**（北京时间 17:15:19）：

| 项目 | 已读回结果 | 验收边界 |
| --- | --- | --- |
| 应用 | `v1.0.0-rc.45`，应用及依赖容器健康 | RC 发布物；不据此推导任何供应商模型可用 |
| 插件 | 18 个，源码 hash 与锁一致、registered/runtime 正常；runtime generation `19` | 包括 11 个内置 factory、2 个额外官方市场插件、5 个私有插件 |
| 在线 fixture | 私有插件共 `126/126` | 宿主 hook 用假输入／响应重放，不发送真实供应商请求 |
| 数据与配置 | `users=1`、`channels=0`、`tokens=0`、`tasks=0`；`RetryTimes=0`、注册关闭、任务插件开启 | 尚无供应商渠道或业务 token；没有真实生成、计费验收 |
| PostgreSQL / Redis | 原配置哈希保持一致，容器健康 | 本次没有升级这两个依赖或重配其持久化 |
| 备份 | `/srv/backups/new-api/20261020T085002Z-af86b3d1`；归档 hash、`COMPLETE`、独立 PostgreSQL 恢复及表计数核对通过 | 未执行整个应用／Redis 的生产切回演练；数据库迁移后不能仅换回旧镜像 |
| 服务器内部 HTTPS | 通过目标域名和正常证书校验的内部链路健康 | 内部 HTTPS 结果单独成立 |
| 当前环境访问公网 | HTTP 返回 `302` 并进入 `webblock`；公网 HTTPS 遇到 TLS EOF | 公网访问链路尚未验收健康；不能用内部健康结果替代 |
| 业务鉴权 | 无凭据访问模型／原生接口返回 `401` | 证明入口要求鉴权，不证明已存在可调用模型或渠道 |

## 四类 Easel 客户端如何选择

推荐使用[统一设置入口](../../docs/platform/media-gateway-settings.md)，它从服务根地址为各能力派生正确路径。自定义接口和旧版手工环境配置继续保留；手工配置中**不同能力的 base 前缀含义不同**，请按相应文档设置，不能把一个全局 `/v1` 地址用于所有分支。

| 能力 | Easel 入口与模式 | 配置与文档 | 当前客户端状态 |
| --- | --- | --- | --- |
| 图片 | `ai_image.py`；统一设置接管生成／编辑／变体；旧手工 `IMAGE_API_MODE=new-api` 继续仅切换 `variations` | [统一设置](../../docs/platform/media-gateway-settings.md)、[旧手工图片接入说明](../../docs/platform/new-api-image-client.md) | 三类路径与保存后 CLI 调用本地验证；自定义与旧手工模式保留，未做真实供应商验收 |
| 视频 | `ai_video.py --provider openai-compatible`；`VIDEO_API_MODE=new-api` | [视频接入说明](../../docs/platform/new-api-video-client.md)；`VIDEO_BASE_URL` 为带可选代理前缀的 `/v1` 根，显式模型与 profile | 本地 mock／loopback HTTP 验证完成；未做真实视频生成或播放验收 |
| 音乐 | `ai_music.py`；`MUSIC_API_MODE=new-api` | [音乐接入说明](../../docs/platform/new-api-media-client.md)；DashScope / Suno 使用各自网关路由根地址 | 本地协议、完成态及重定向凭据边界验证完成；未做真实音乐验收 |
| 语音 | `voice_clone.py` + `newapi_voice.py`；MiniMax / DashScope / Fish 显式 `VOICE_API_MODE=new-api` | [语音接入说明](../../docs/platform/new-api-media-client.md#语音显式私有网关模式)；`NEW_API_BASE_URL` 为网关根，`NEW_API_KEY` 为用户业务令牌；[Gemini TTS 核心路由说明](../../docs/platform/gemini-tts-new-api.md)另列 | 私有模式本地验证完成；Fish 仍依赖未部署的桥，其他真实音色／合成待验收 |

客户端填写的是用户的网关业务令牌。供应商密钥由管理员放在实际渠道或私有桥中，root 管理凭据仅供管理 API 使用，三者不能互换。Gemini 预置声音 TTS 沿用核心原生 Generate Content 路径，不需要另造一个重复私有插件。

## 能力矩阵

“已注册”表示当前生产宿主存在该路由／插件；“客户端本地验证”表示请求和假响应合同通过。所有行的真实供应商状态均受 `channels=0` 限制。

| Easel 所需能力 | 网关接口或组件 | 本轮覆盖范围 | 明确限制 |
| --- | --- | --- | --- |
| Agent 文本、工具调用 | 核心 `/v1/responses`、`/v1/chat/completions`、Anthropic 兼容 `/v1/messages`，按引擎选实际协议 | 保留原核心接入；无需通过媒体私有插件 | 实际模型工具调用、流式事件、上下文与用量须逐模型验收；`/messages/count_tokens` 在该版本未开放 |
| 原生 Gemini | 核心 `/v1beta/models/{model}:generateContent` | Generate Content 的预置声音音频输出可复用核心；客户端本地验证 | rc.45 无 Gemini `/interactions` 原生入口；3.8 的 `speech_metadata` 情绪元数据可能被核心转换丢弃 |
| 图片生成／编辑 | 核心／官方插件的 `/v1/images/generations`、`/v1/images/edits` | 保留已有标准合同，按实际渠道选择 | 现代 GPT Image 的编辑工作流与 legacy variations 是不同能力 |
| legacy 图片变体 | 私有 `easel-image-variations`：`POST /easel/openai/v1/images/variations` | 服务端插件已生产注册／hash／runtime 验证；客户端显式路径、multipart 与直接 data 响应本地验证完成 | 核心 `/v1/images/variations` 是 `501` 占位；仅能转发到仍实际提供此旧协议的兼容供应商 |
| 视频 | 官方视频 factory；`POST/GET /v1/videos` 与 `/v1/videos/{public_id}/content` | 客户端有 standard、Veo、Kling、Wan 2.6 t2v、Seedance 1.0 五个保守 profile；完成无 URL 时鉴权下载 content | 其余模型、分辨率、音频开关和高级操作不自动视为已支持；官方 Sora 已退役 |
| Fun-Music | 私有 `easel-dashscope-music` | 非流式同步 Fun-Music，完成时按供应商时长事实结算；客户端显式映射 | 无伪造异步供应商查询；地区／模型开通、预扣预算及真实音频仍待验证 |
| Suno | 官方市场 `sunoapi`：`/suno/submit/music` → `/suno/fetch/{id}` | 客户端显式 native envelope、状态和任务查询映射 | 不把旧 `/generate` 合同或 legacy 默认模型直接套入 native 路由 |
| 系统／预置声音 TTS | 核心 `/v1/audio/speech`，Gemini 另走原生路由 | 继续使用实际渠道支持的标准协议 | 不允许用任意供应商私有 voice ID 绕过私有克隆资源的归属链 |
| 音频转写／翻译 | 核心 `/v1/audio/transcriptions`、`/v1/audio/translations` | 保留核心标准入口 | 上传格式、模型权限、返回格式及计量需按供应商验证 |
| MiniMax 声音克隆 | 私有 `easel-minimax-voice` | 样本上传公共任务 → 音色登记公共任务 → 非流式短文本 hex 音频 | 仅本插件声明的模型与参数；int64 精度、1 MiB JSON、短文本限制见下文 |
| CosyVoice 克隆 | 私有 `easel-dashscope-voice` | 登记 → 审核查询 → 已审核音色合成；origin 约束用户、原渠道与 target model | 登记成功不等于音色审核通过；无声音列表／删除／更新、实时 WebSocket 或 SSE |
| Fish TTS／样本复刻 | 私有 `easel-fish-tts` + [私有二进制桥](./fish_bridge/README.md) | 插件已注册；桥及客户端本地契约完成，inline 样本和管理员预置 ID 白名单 | **桥未部署**，当前无法组成生产链路；无任意共享供应商 voice ID 读取、多说话人或全部高级参数 |

核心原始路由依据：[rc.45 relay router](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.45/router/relay-router.go)、[video router](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.45/router/video-router.go)。协议存在不是账号／模型已开通的证据。

## 已部署的插件集合

官方市场以 [catalog.lock.json](./catalog.lock.json) 的固定索引和源码 hash 为准，共 13 个：`alibaba`、`doubao`、`google`、`hailuo`、`incho`、`jimeng`、`kling`、`sora`、`sunoapi`、`typesafe`、`vertex-ai`、`vidu`、`xai`。其中 11 个随 rc.45 内置，Incho 和 TypeSafe 额外安装。本次生产读回已覆盖全部 13 个，不需要把同版内置源码再次上传为 override。

Easel 私有插件均为 `1.0.0`，版本和源码 hash 锁定在 [private-plugins.lock.json](./private-plugins.lock.json)：

| 私有插件与合同说明 | 补充能力 | 在线 fixture 数 |
| --- | --- | ---: |
| [easel-dashscope-music](./plugins/easel-dashscope-music/1.0.0/README.md) | Fun-Music 同步生成、已保存任务与音频 artifact | 25 |
| [easel-dashscope-voice](./plugins/easel-dashscope-voice/1.0.0/README.md) | CosyVoice 登记、审核、同模型合成 | 35 |
| [easel-minimax-voice](./plugins/easel-minimax-voice/1.0.0/README.md) | 文件上传、音色登记、短文本克隆音色合成 | 29 |
| [easel-image-variations](./plugins/easel-image-variations/1.0.0/README.md) | 私有 multipart legacy variations 路由 | 16 |
| [easel-fish-tts](./plugins/easel-fish-tts/1.0.0/README.md) | 对接私有桥、公共任务和有归属的音频产物 | 21 |
| 合计 | 5 个插件 | **126** |

在线 fixture 使用假供应商参数与响应，不执行描述符中的外部 HTTP。Fish fixture 按现场宿主时钟校正有效期后重放；它没有证明私有桥已运行。新增插件没有配置渠道、模型价格、用户额度或中央会员服务。

## 调用前必须处理的边界

**同步未知结果不自动重发。** 当前生产 `RetryTimes=0`，应保持该设置。已在真实本地 rc.45 宿主验证：将 `RetryTimes` 改为 `2` 时，一个上游已执行但响应解析失败的客户端 POST 可触发 3 次上游 POST。固定原渠道只能避免换渠道，不能避免同渠道重放。客户端也不会在超时后换协议、换供应商或自动重新生成。宿主错误／退款不代表供应商没有收费；正式服务需要 pending、人工核对和中央账本对账。

**用量未知不等于零。** Fun-Music 请求时没有实际时长，部分声音请求也未取得完成事实，需要管理员明确配置并验证预扣预算、限流和完成结算。fixture 中的假价格不能直接作为生产费率。Fish 仅报告桥观察到的请求数与输入字符，不伪造精确供应商 token 账单。统一会员、跨产品共享额度及中央供应商成本账本仍是单独的中央服务工作。

**MiniMax 的宿主边界。** rc.45 提交 JSON 响应上限为 1 MiB；私有插件 hex 音频另限 900,000 字符、非流式 MP3 与最多 500 个 UTF-16 文本单位。更长音频不能通过截断伪装成功。完整 int64 字符串 ID 可精确保留；如果上游以 JSON number 返回超过 `Number.MAX_SAFE_INTEGER` 的文件 ID，宿主在 hook 前的 float64 转换已丢失精度，插件会拒绝，不能猜回原 ID。完整覆盖此类数字返回需要宿主保留原始 JSON／int64。

**已退役接口。** OpenAI DALL·E 2／3 和官方 legacy variations 已停止服务；私有 variations 插件不能恢复它们，现代图片变体应按明确的 edits 工作流另行设计。OpenAI Sora／Videos API 已于 2026-09-24 关闭；市场仍有 Sora 格式插件不代表官方服务恢复。[官方弃用说明](https://developers.openai.com/api/docs/deprecations)

**Fish 二进制桥仍待部署。** [私网部署模板](./fish_bridge/compose.private.example.yml)无主机公开端口，桥只接 loopback／私有 Docker 网络；需要管理员准备强随机内部 secret、真实 `FISH_API_KEY`、允许共享的预置声音 ID、私有持久目录及网络接入。桥缺密钥会拒绝启动，不在仓库保存生产 secret。用户仅访问 New API，由公共 task ID 和宿主归属校验进入产物下载；不能绕过网关账本直接公开桥。完整 New API → 私有桥 → 真实 Fish、容器资源和网络隔离尚未验收。

## 后续配置与验收顺序

1. 先按生产验收记录处理公网 `webblock` / TLS EOF 的外部链路，再从真实用户网络核对 HTTPS 和业务鉴权；保留现有内部健康证据。
2. 按本页能力矩阵选一项实际可用的供应商／模型，配置对应渠道、地域、权限、价格和有界预扣；保留 `RetryTimes=0`。不要一次性把未确认的模型声明开放给用户。
3. 为受限测试用户配置业务额度与业务令牌，按该类客户端文档显式选择网关模式；保留已有自定义接口配置。
4. 逐项验收真实生成结果、公共任务归属、跨用户拒绝、下载和播放、异常不重发、用量与余额核对。声音链路还需核对原渠道、审核状态、target model 和资源保留。
5. 对 Fish 先单独完成私网桥部署与密钥／持久幂等验收，再接生产模型渠道；对图片变体选择仍提供旧协议的实际供应商，再按已完成的显式客户端路径验收。
6. 发布或扩容前保留每个模型的实际验收收据。恢复和回退遵循备份目录内 `RESTORE.md` 与 [升级说明](./ops/UPGRADE.md)，不能让旧镜像直接连接可能已迁移的数据库，也不能删除 pending 记录当作问题已解决。
