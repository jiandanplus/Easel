# New API 与 Easel 接口兼容性核对

日期：2026-10-19。Easel 分支 `codex/ui-redesign`，源码提交 `e972af0`。New API 基线为运维手册记录的 `calciumion/new-api:v0.13.2`，协议实现对照官方同名 tag。线上实例的当前版本、渠道、令牌和模型调用尚未读取成功。

2026-10-20补充：本报告保留 v0.13.2 历史核对。后续现场已完成 v1.0.0-rc.45 升级、13 个官方与 5 个 Easel 私有插件注册，详见[本次升级验收](2026-10-20-new-api-upgrade-plugins.md)和[当前接入入口](../../integrations/newapi/README.md)。下文“尚未现场读取”和客户端缺口描述均属于原检查时点，不应继续当成当前部署状态；真实供应商调用仍未验收。

**New API v0.13.2 可以作为 Easel 的主要模型网关，但当前不能覆盖全部接口，也不能仅替换 Base URL 就让所有现有脚本直接使用。** 文字、Responses、同步图片和标准语音有对应实现；视频、音乐需要协议适配；图片变体和部分声音克隆接口有明确缺口。Easel 账号、会员及跨产品共享额度继续由独立平台承担。

## 部署基线与线上检查

[volcengine 4h4g New API 管理员运维手册](https://chatgpt.com/space/page_3f3e66622538819180b138734ac4badc)记录的入口是 `https://api.drawbookai.cn`，OpenAI 兼容 Base URL 为 `https://api.drawbookai.cn/v1`。OpenResty 将请求转入本机 New API，PostgreSQL 和 Redis 不公开端口。手册编写时尚未添加上游渠道，未完成真实推理、流式和计费验收；这属于手册记录的历史状态，不能据此断言现在仍为空。

本次公共 HTTPS 检查在取得 HTTP 响应前失败：命令行返回 `SSL_ERROR_SYSCALL`，浏览器返回 `net::ERR_CONNECTION_CLOSED`。失败发生在登录之前，没有发送管理员凭证、创建业务令牌或提交模型任务。这个结果说明当前检查环境与入口未建立连接，不能单独证明服务宕机。

因此，下表“有实现”指 v0.13.2 源码中有处理链，不代表线上渠道已配置、上游支持相同功能，或真实调用已通过。发布 tag 与线上镜像的实际版本和摘要也尚未重新核验。

## 核心模型接口

| Easel 功能 | Easel 需要的接口 | New API v0.13.2 | 接入判断 |
| --- | --- | --- | --- |
| 模型列表与设置测试 | `GET /v1/models` | 有模型目录接口 | 需要有效业务令牌及可用渠道；目录成功不能代替推理验收。 |
| API 直连聊天 | `POST /v1/chat/completions`，SSE | 有聊天及流式处理 | 协议基础匹配。Easel 此模式收到工具调用会拒绝，不承担本机工具执行。 |
| Codex 本机 Agent | `POST /v1/responses`，SSE，客户端工具往返 | 有 Responses 及原始工具 JSON 处理 | 需要选择真正支持 Responses 的渠道，验收工具回传、终止事件和 usage。不能由模型名称推导兼容性。 |
| Claude 原生协议 | `POST /v1/messages` | 有原生转发与流式处理 | 可用于相应原生客户端；不会自动解决 Codex 的 Responses 需求。 |
| Gemini 原生生成 | `/v1beta/models/*path` 等 | 有原生模型路径 | 当前 Easel Gemini TTS 用的是另一个 `/interactions` 协议，不能由此推导 TTS 可接入。 |
| 同步文生图 | `POST /v1/images/generations`，返回 `data[].url` 或 `b64_json` | 有实现 | 需要渠道支持该模型及响应格式。 |
| 图生图与蒙版编辑 | `POST /v1/images/edits`，multipart `image`、可选 `mask` | 有 multipart 编辑处理 | 需要上游模型支持编辑、蒙版和输入尺寸。 |
| 图片变体 | `POST /v1/images/variations` | 路由进入 `RelayNotImplemented`，返回 501 | 明确缺口；当前变体功能不能直接切换。 |
| 预置声音或已登记 voice ID 合成 | `POST /v1/audio/speech`，二进制音频 | 有标准音频处理 | 可接入相应 TTS 渠道；不等于支持声音样本上传和克隆登记。 |
| 语音转文字 | Easel 当前主要用本地 `faster_whisper` | New API 另有 `/audio/transcriptions`、`/audio/translations` | 当前本地 ASR 不依赖网关。若以后切换云端，需要另加客户端。 |
| 通用异步视频 | `POST /v1/videos` → `GET /v1/videos/{id}` | 有任务提交、轮询及不同供应商适配器 | 存在字段不兼容，详见视频部分。 |
| 标准视频文件下载 | `GET /v1/videos/{id}/content`，带鉴权 | 有内容代理及鉴权 | Easel 当前没有这个下载分支，需补充。 |

New API 证据：[主转发路由](https://github.com/QuantumNous/new-api/blob/v0.13.2/router/relay-router.go#L86-L164)、[视频路由](https://github.com/QuantumNous/new-api/blob/v0.13.2/router/video-router.go#L10-L42)、[OpenAI 图片和音频适配器](https://github.com/QuantumNous/new-api/blob/v0.13.2/relay/channel/openai/adaptor.go#L360-L550)。Easel 调用见 [direct_api.py](/Users/jiandan/Code/GitCode/Easel/easel/direct_api.py:83)、[runtime.py](/Users/jiandan/Code/GitCode/Easel/easel/harnesses/runtime.py:119)、[ai_image.py](/Users/jiandan/Code/GitCode/Easel/skills/shared/scripts/ai_image.py:540)、[voice_clone.py](/Users/jiandan/Code/GitCode/Easel/skills/shared/scripts/voice_clone.py:388)。

## Codex 工具与用量要求

Easel 将 Codex 自定义供应商固定为 `wire_api="responses"`。New API 的 Responses 请求结构使用原始 JSON 保存 `tools`、`tool_choice` 和 `input`，流式处理保留上游事件，不是只允许文本的白名单。因此，不能将该版本整体判为“不支持 Codex 工具”。是否正确完成 namespace、function、custom 工具调用，仍要按当前 Codex 版本跑完整往返。

另一方面，该版本 Gemini、Claude 等专用适配器的 `ConvertOpenAIResponsesRequest` 明确未实现。使用这些专用渠道并添加模型映射，不会自动把 Codex Responses 转成原生 Gemini 或 Claude 协议。第一批应选择实际提供 Responses 的 API 上游。

Easel 平台账本要求非流式响应有 `usage.input_tokens` 和 `usage.output_tokens`；SSE 必须包含携带 usage 的真实 `response.completed`，且状态为 `completed`。仅有 `[DONE]` 不足以结算。New API v0.13.2 在缺失完成事件时会退回基于文本 delta 的估算，工具或推理输出不能据此视为已精确计量。客户端取消、断流和上游错误要单独验收，不能按零消耗结束。

New API 的 Codex 专用渠道还会移除 `max_output_tokens` 等部分字段；Easel 平台的输出上限不能仅靠前置校验就推导为上游已执行限制。应优先验收普通 API Responses 渠道，若采用专用 Codex 渠道，再逐项检查参数转换。

证据：[Responses 请求结构](https://github.com/QuantumNous/new-api/blob/v0.13.2/dto/openai_request.go#L820-L855)、[流式事件及 usage](https://github.com/QuantumNous/new-api/blob/v0.13.2/relay/channel/openai/relay_responses.go#L46-L149)、[Gemini Responses 转换](https://github.com/QuantumNous/new-api/blob/v0.13.2/relay/channel/gemini/adaptor.go#L240-L242)、[Claude Responses 转换](https://github.com/QuantumNous/new-api/blob/v0.13.2/relay/channel/claude/adaptor.go#L110-L112)、[Codex 渠道转换](https://github.com/QuantumNous/new-api/blob/v0.13.2/relay/channel/codex/adaptor.go#L30-L126)、[Easel 平台网关](/Users/jiandan/Code/GitCode/Easel/easel_platform/gateway.py:108)。

## 视频请求与下载差异

视频不是整体不可用。Wan、Ark 适配器将完成结果转换为顶层 `status="completed"` 和 `metadata.url`；Easel 递归查找 URL，能够识别这些结果。简单文生视频在参数与渠道配置匹配时具有直接使用的协议基础。

需要修正的请求和下载差异如下：

| 交叉点 | 当前 Easel | New API v0.13.2 的要求 | 结果 |
| --- | --- | --- | --- |
| 秒数 | 指定 `--duration 5` 时发送数字 `seconds: 5` | `seconds` 为字符串；`duration` 另有数字或字符串兼容处理 | 数字 seconds 在 JSON 绑定时出错。省略 duration 时不触发此问题。 |
| 尺寸 | 将比例 `9:16` 原样写入 `size` | Sora 使用 `720x1280` 等尺寸；Wan t2v 显式尺寸使用 `1280*720` 等格式 | 需要按模型转换比例和分辨率。 |
| 首帧图 | 通用分支发送 `image` | Ark 会归一化该字段；Wan 的相关分支读取 `input_reference`，Sora 也不自动把通用 image 改成标准输入 | 不能保证所有图生视频分支生效。 |
| Wan 或 Ark 完成结果 | 轮询 status，递归查找 URL | `metadata.url` 包含直链 | 结果提取匹配。 |
| 标准 Sora 完成结果 | 要求查询 JSON 中含视频 URL | 标准响应可只有完成状态；视频需单独请求 `/content` | 当前报“任务完成但未找到视频 URL”。若特定上游额外提供 URL，则可能直接成功。 |
| 内容代理鉴权 | 视频 URL 下载只发 User-Agent | New API `/content` 要求令牌或用户会话 | 需增加带业务令牌的专用下载处理，不能将令牌发送给任意结果 URL。 |

证据：[任务字段与 JSON 解析](https://github.com/QuantumNous/new-api/blob/v0.13.2/relay/common/relay_info.go#L676-L741)、[尺寸和输入归一化](https://github.com/QuantumNous/new-api/blob/v0.13.2/relay/common/relay_utils.go#L128-L220)、[Wan 转换](https://github.com/QuantumNous/new-api/blob/v0.13.2/relay/channel/task/ali/adaptor.go#L489-L519)、[Ark 转换](https://github.com/QuantumNous/new-api/blob/v0.13.2/relay/channel/task/doubao/adaptor.go#L344-L367)、[Sora 转换](https://github.com/QuantumNous/new-api/blob/v0.13.2/relay/channel/task/sora/adaptor.go#L300-L330)、[Easel 视频请求](/Users/jiandan/Code/GitCode/Easel/skills/shared/scripts/ai_video.py:532)、[URL 提取](/Users/jiandan/Code/GitCode/Easel/skills/shared/scripts/ai_video.py:363)、[下载](/Users/jiandan/Code/GitCode/Easel/skills/shared/scripts/ai_video.py:345)。

## 供应商专用接口

New API 可在服务端将通用请求转换给部分供应商，但它不是可转发任意私有 URL 的通用反向代理。下表比较的是 Easel 现有供应商分支能否原样调用。

| Easel 分支 | 当前客户端协议 | 差异及处理方向 |
| --- | --- | --- |
| apimart 异步图片 | `/images/generations` → `/tasks/{id}`，特定 `data.result.images` 格式 | 不能用同步图片支持推导这个异步任务格式也可用；保留原供应商或新增网关适配器。 |
| Agnes 图片 | 特定 `ratio`、`size`、`extra_body` 字段，按域名选择分支 | 换成自定义网关域名后默认转入普通同步分支，需要显式 provider 和字段映射。 |
| DashScope 视频 | `/services/aigc/text2video/video-synthesis` 或 `/image2video/video-synthesis` → `/tasks/{id}` | New API 的 Wan 是内部任务适配器，未将这些客户端原生路径整体开放。可改走统一视频客户端。 |
| Ark 视频 | `/contents/generations/tasks` → 同路径 `/{id}` | 现有原生路径不能直接视为 New API 公共路径；可改走统一视频客户端。 |
| Kling 视频 | `/v1/videos/text2video` 等，本机用 AK/SK 签发 JWT | New API 对外使用 `/kling/v1/videos/...` 和业务令牌体系；客户端与服务端凭证职责需调整。 |
| MaaS 视频 | 专用路径、原始 `api-key`，GET 轮询带 model JSON | 未确认有对应原生协议；保留自定义直连。 |
| Agnes 视频 | `/videos` 创建，独立 `/agnesapi` 查询及特定字段 | 需要专用映射，不能只修改域名。 |
| Suno 音乐 | `POST /generate` → `GET /feed/{id}` | New API 是 `POST /suno/submit/music` → `GET /suno/fetch/{id}`。提交 ID 在字符串 `data`，Easel 当前不识别。还需映射 `mv`、歌词和描述等字段。 |
| DashScope 音乐 | `/services/aigc/text2audio/generation` → `/tasks/{id}` | 没有确认对应的公共原生处理链；不能因支持 Suno 就推导通义音乐可用。 |
| DashScope 声音克隆 | `/api/v1/services/audio/tts/customization` 登记 → 原生 TTS generation | 标准 `/audio/speech` 不提供该登记流程，需要保留直连或增加专门服务。 |
| MiniMax 声音克隆 | `/v1/files/upload` → `/v1/voice_clone` → `/v1/t2a_v2` | New API 的 MiniMax TTS 转换不包含该完整上传与登记生命周期。已登记 voice ID 合成与新建克隆声音要分别判断。 |
| Fish Audio | `/v1/tts`，reference_id 或参考音频 | 未找到该原生路径和对应 Fish 渠道适配器；不能声称可直接接入。 |
| Gemini TTS | `/interactions`，音频响应及 speech_config | v0.13.2 原生 Gemini 模型路径不等于 Interactions API，当前分支不匹配。 |

Suno fetch 的嵌套状态与音频 URL 可以被 Easel 现有递归提取识别；明确缺口是提交和查询路径、提交 ID 及输入字段，无需将整个解析器推倒重写。该版本音乐 action 只接受 MUSIC 和 LYRICS，`/suno/submit/generate` 也不是有效替代路径。

另外，`DASHSCOPE_BASE_URL` 在 Easel 不同脚本中的含义不统一：视频、音乐把它当已经包含 `/api/v1` 的 API Base，声音克隆则自行追加 `/api/v1`。应统一配置语义或按能力使用适配器，避免重复路径。

证据：[Easel 音乐脚本](/Users/jiandan/Code/GitCode/Easel/skills/shared/scripts/ai_music.py:249)、[Easel Suno 提交与解析](/Users/jiandan/Code/GitCode/Easel/skills/shared/scripts/ai_music.py:323)、[New API Suno 实现](https://github.com/QuantumNous/new-api/blob/v0.13.2/relay/channel/task/suno/adaptor.go#L38-L165)、[Suno 请求字段](https://github.com/QuantumNous/new-api/blob/v0.13.2/dto/suno.go#L7-L17)、[Easel 声音克隆](/Users/jiandan/Code/GitCode/Easel/skills/shared/scripts/voice_clone.py:258)、[MiniMax 渠道](https://github.com/QuantumNous/new-api/blob/v0.13.2/relay/channel/minimax/adaptor.go#L35-L77)、[Custom URL 处理](https://github.com/QuantumNous/new-api/blob/v0.13.2/relay/channel/openai/adaptor.go#L161-L164)。

## 账号会员与接入位置

Easel 账号平台需要 `POST /v1/auth/{register,login,refresh,logout}`，`GET /v1/{account,sessions,plans,entitlements,models,usage}`，`DELETE /v1/sessions/{id}`，以及 `POST /v1/task-tokens`。New API 管理员登录、配额和令牌接口不能原样替代这些产品协议。Logto、中央会员和共享额度服务的既定职责仍需要保留。

适合第一批接入的调用关系是：

```text
Easel 订阅任务
    → Easel 平台的身份校验、任务授权与共享额度账本
    → New API /v1/responses
    → 已配置且真实支持 Responses 的上游

Easel 自定义接口模式
    → 用户配置的原供应商，或经适配后的 New API
```

Easel 平台服务端已有以下上游配置入口，无需把 New API 管理员账号交给客户端：

```dotenv
EASEL_PLATFORM_UPSTREAM_URL=https://api.drawbookai.cn/v1
EASEL_PLATFORM_UPSTREAM_KEY=<New API 业务令牌>
EASEL_PLATFORM_UPSTREAM_MODEL=<渠道中实际可用的 Responses 模型>
EASEL_PLATFORM_PUBLIC_MODEL=<Easel 对用户展示的模型别名>
```

以上只是配置示例，未应用到现有服务。Easel 设置中的“账号与订阅平台地址”应填写真正的账号平台根地址，不能填写 New API 的 `/v1` Base URL。当前订阅入口只允许本机 Codex 文本和客户端工具，云端执行与订阅媒体网关仍未开放；媒体上游即使可用，也不会自动获得 Easel 订阅权益。

New API 不替代本机 Codex、ACP、OpenClaw 执行器，也不接管本地 ASR、FFmpeg 或社交平台发布接口。

证据：[账号平台路由](/Users/jiandan/Code/GitCode/Easel/easel_platform/api.py:52)、[平台地址校验](/Users/jiandan/Code/GitCode/Easel/easel/platform/client.py:54)、[服务端配置](/Users/jiandan/Code/GitCode/Easel/easel_platform/__main__.py:1)、[订阅执行约束](/Users/jiandan/Code/GitCode/Easel/easel/service_contracts.py:21)、[平台请求范围](/Users/jiandan/Code/GitCode/Easel/easel_platform/gateway.py:79)。

## 已完成验证与待执行验收

本次完成源码及离线协议核对，没有使用真实模型、生成收费任务或更改运行配置。合成响应验证只证明解析行为，不证明供应商真实可用：

| 离线检查 | 结果 |
| --- | --- |
| 抽取 v0.13.2 真实 TaskSubmitReq 与 UnmarshalJSON，用 Go 解析数字 seconds | `seconds: 5` 报字符串类型错误；`seconds: "5"` 和 `duration: 5` 解析成功。 |
| Easel 轮询与 URL 提取接收 Wan、Ark 合成完成结果 | 能识别 completed 与 metadata.url。 |
| Easel 接收标准 Sora 完成结果 | 没有 URL 时无法提取；增加上游额外 URL 后能够提取。 |
| Easel Suno ID 解析接收字符串 data | 返回空，确认需要支持该提交结果。 |
| Easel Suno 状态和音频提取接收 New API fetch 包装 | 能识别 SUCCESS 和嵌套 audio_url。 |

恢复当前检查环境到网关的连接后，应按以下顺序验收，不能用目录或 HTTP 200 代替生成结果：

1. 只读确认线上版本、健康状态、渠道类型、模型映射和业务令牌权限。
2. 用有预算限制的测试令牌验证一个真实 Responses 模型：普通回复、SSE 完成与 usage、本机工具往返、错误和取消后的账本状态。
3. 验证同步图片、multipart 编辑与蒙版、TTS 二进制音频及实际可播放的下载文件。
4. 完成视频字段适配后，验证文生、图生、轮询、失败、超时和带鉴权 content 下载；按模型分别验收。
5. 完成 Suno 适配后，验证创建、任务 ID、轮询、失败及可播放音频；专用克隆流程仍走保留的自定义渠道或后续服务。

当前结论为“主要模型能力具备接入基础，存在明确适配项和缺口，线上端到端待验收”。本次仅新增这份本地核对记录，未修改 Easel 运行代码、服务器或在线运维手册，未提交、推送或合并分支。
