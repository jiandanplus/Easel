# New API 新增图片模型：目录发现与协议边界

日期：2026-10-27。目录发现之后，主任务已对两款模型分别完成一次真实 Images 调用及原图下载、完整解码和视觉核对。中央服务扣点与工作台 Pi 调用仍须后续验收，不能由协议探针替代。

## 服务器只读发现

由主任务在 154.217.245.179 内使用既有业务令牌进行 GET-only 检查，令牌仍留在服务器。本子任务没有读取令牌、供应商 Key 或执行供应商 POST。

`/v1/models` 返回四个模型：

| 模型 | 本次变化 | 目录中的端点信息 |
| --- | --- | --- |
| deepseek-flash | 原有文本 | 本记录不调整 |
| deepseek-v4-pro | 原有文本 | 本记录不调整 |
| gpt-image-2.5-flare | 新增图片候选 | owned_by=newapi；models 端点数组为空 |
| gpt-image-2.5-sunburst | 新增图片候选 | owned_by=newapi；models 端点数组为空 |

主任务读取的 `/api/pricing` 对两款新增模型均报告 `quota_type=0`、`model_ratio=37.5`、`completion_ratio=2`、`model_price=0`，default 分组。该模式是 token 计费，`model_price=0` 不是免费声明，也不是中央平台的点数费率。

pricing 同时列出 image-generation、openai、openai-response、openai-response-compact、anthropic、gemini、openai-alpha-search。这个列表不等于这些端点均已真实验证；需要继续确认渠道类型、模型映射、实际镜像版本与真实响应。

## 文档与固定公开源码

按项目 AGENTS，先执行了本问题的 Context7 `library`，再执行两条独立 `docs`（`/quantumnous/new-api`）。返回内容主要是 billingexpr 和 relaykit，未覆盖当前 Images 响应头及旧版具体 pricing 实现，因此没有把它们作为兼容成功证据。

随后只读检查仓库 lock 中固定的官方提交 `6cf0d90b5ff27f901697dddd61e0c28aba8efefc`，公开源码副本位于 `/private/tmp/easel-m5-newapi-public-20261027/`。它是协议比对基线；仍需与运行中镜像比对，不推断生产版本完全一致。

| 固定源码 | 核对结果 |
| --- | --- |
| [common/endpoint_type.go](https://github.com/QuantumNous/new-api/blob/6cf0d90b5ff27f901697dddd61e0c28aba8efefc/common/endpoint_type.go) | NewAPI/Sub2API 渠道会声明多个协议；图片端点又按模型名分类前置。该元数据可由渠道类型和名称推导，不能代替真实探针。 |
| [model/pricing.go](https://github.com/QuantumNous/new-api/blob/6cf0d90b5ff27f901697dddd61e0c28aba8efefc/model/pricing.go) | 固定价格存在时才设置 quota_type=1；否则设置 ratio、completion ratio 和 quota_type=0。 |
| [common/constants.go](https://github.com/QuantumNous/new-api/blob/6cf0d90b5ff27f901697dddd61e0c28aba8efefc/common/constants.go) 与 [middleware/request-id.go](https://github.com/QuantumNous/new-api/blob/6cf0d90b5ff27f901697dddd61e0c28aba8efefc/middleware/request-id.go) | 这个固定版本生成本实例的 `X-Oneapi-Request-Id`。不能根据其他版本文案猜成 `X-New-Api-Request-Id`。 |
| [service/http.go](https://github.com/QuantumNous/new-api/blob/6cf0d90b5ff27f901697dddd61e0c28aba8efefc/service/http.go) | 保留本实例 Oneapi ID，其他上游 header 可转发；header 存在不能自动说明它是供应商资源 object ID。 |
| [relay/channel/openai/relay_image.go](https://github.com/QuantumNous/new-api/blob/6cf0d90b5ff27f901697dddd61e0c28aba8efefc/relay/channel/openai/relay_image.go) | 非流式 Images body 原样转发；内部用 Images usage 归一化参与网关计费。 |
| [relay/image_handler.go](https://github.com/QuantumNous/new-api/blob/6cf0d90b5ff27f901697dddd61e0c28aba8efefc/relay/image_handler.go) | 缺失 token 时内部存在补 1 的行为，因此网关日志里的 1 不应被中央平台当作供应商真实 token 证据。 |

OpenAI 当前 [Images API](https://developers.openai.com/api/reference/resources/images) 描述 GPT image 默认返回 base64，且不使用 response_format=url。这里只用于设计显式兼容探针，不把两个新别名等同于某一官方模型或猜测它们的全部参数。

## 已审 M1 包的离线复证

生产候选仍是独立冻结包：

`/private/tmp/easel-media-m1-wheel-20261027-review-fixes/easel_central_platform-0.1.0-py3-none-any.whl`

SHA256：`b070d12f7c48f65709ebdfb295a76a2e1e45b79b612404893e5e541f00d1dfd9`。本次重新读取包并核对 SHA；没有从包含未完成 M2/M3 的工作树重打。

对该 wheel 直接运行无网络 MockTransport，固定 body 仅含 created 和 data 数组（合成占位字节，不宣称有效图片）：

| 响应头 | 旧 M1 解析结果 |
| --- | --- |
| X-Oneapi-Request-Id | invalid_media_response |
| X-New-Api-Request-Id | invalid_media_response |
| x-request-id | accepted，旧字段 response_id 回退为该 header |

旧 adapter 只识别 x-request-id/request-id；若没有 body.id，就把受识别的 HTTP ID 放入原 response_id。真实 New API 如果只返回 created/data 和 Oneapi 头，修改目录配置不能补齐此差异。

需要按实响选择明确 profile，分别持久化供应商 object ID、网关 HTTP correlation 的 namespace/value。body 没有 object 时应保留空 object，不能把网关 ID 伪装为供应商资源，也不能随机造 ID 解锁结算。

## 最小真实探针建议（尚未在本子任务执行）

1. 仅在服务器私有 0700 目录写入 attempt UUID、精确 body SHA、开始时间与 submission_uncertain；证据文件 0600。
2. 先确认真实渠道是 Images generation 合同，再对 flare 发送一次合成内容请求；不包含项目/用户内容。若确认支持，采用 n=1、1024x1024、quality=low、output_format=png；否则不根据名字猜参数。
3. 不传 response_format，不用 stream；限制一次 POST、180 秒、32MiB、identity 编码、禁止重定向和自动重试。超时或网络未知保留该次不确定记录，不能自动改投 sunburst。sunburst 是另一独立 once 验收。
4. 原始响应留在服务器。只回传白名单：状态、Content-Type、真实关联头 namespace/value、顶层 keys、created、data 数量与 payload 类型、原 usage 数值，以及实际图片全解码后的 MIME、宽高、字节数和 SHA。
5. token 用量只取原响应的 input_tokens/output_tokens 和可校验的 details/total。缺失时保持 unknown，不能采用预估图数、网关补 1 或预扣量替代。
6. 真实 probe 成功后再审中央服务兼容增量、费率和会员权益配置。没有明确点数费率前保持目录关闭。M4 工作台 host/image 客户端尚未接入，不能把后端探针成功表述为工作台已可生图。

本记录未修改生产、未启用模型、未改变文本渠道、自定义接口或钱包。

## 主任务真实协议和原图验收

两次探针均在 154 服务器内部读取已有业务令牌，固定 `/v1/images/generations`，只发送 `model/prompt/n=1/size=1024x1024`。未猜测 quality/output_format，未传 response_format；各模型只发送一次独立 POST，未自动重试或用另一模型回退。内容为蓝色圆形／橙色方块的合成测试，不含项目文件。

| 模型 | HTTP / 时间 | 原图验证 | 原图 SHA256 |
| --- | --- | --- | --- |
| gpt-image-2.5-flare | 200 / 32.975 秒 | PNG，1024×1024，546232 字节，全解码及视觉核对通过 | `07bad59f73f221315f4263c6f036136cf5c5e49ced296bfa09c84b16230bd1ff` |
| gpt-image-2.5-sunburst | 200 / 30.834 秒 | PNG，1024×1024，410786 字节，全解码及视觉核对通过 | `7c5f8d098314306da44bac17458e34417929b36d079c3ba49ac63abd22a6c973` |

两次响应只有 created/data，无供应商资源 object ID，无原始 usage；实际网关关联头为 `X-Oneapi-Request-Id`。下载域名均为 `https://api.image2pro.top`，下载时未转发业务 Authorization、不跟随重定向。原始响应及签名地址保留在服务器 root 私有目录。本机仅取回合成图片和白名单安全回执，位于 `outputs/_run/newapi-image-sync-20261027/evidence/`，摘要见该目录 `manifest.json`。

解码采用锁定的 Pillow 12.3.0 官方 Linux wheel，逐字 SHA 与项目 uv.lock 对齐；只安装于 root 的验收 site，原生产 Python 未改变。两次直接协议探针均未创建中央预留、未改钱包或追溯扣点。

用户将本次体验定价和使用范围交由项目方决定；采用两款各 1 点／实际验证交付的图片，只扩展原体验账号 image 权益，不增点、不延期、不修改原计划／grant／lot／账本，不打开正式支付。具体迁移与适配见 `docs/superpowers/specs/2026-10-27-newapi-images-m1_1-design.md`。中央完整链路与工作台验收完成前，此段不声明目录已启用或桌面已可用。
