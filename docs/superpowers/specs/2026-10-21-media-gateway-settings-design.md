# 统一媒体网关设置设计

用户已批准第一批开发：补齐设置页面与统一配置入口，保留自定义接口，在当前 `codex/ui-redesign` 分支实施并验证，不合并主分支。

## 配置与交互

模型设置增加“统一媒体网关”卡片。网关根地址和业务密钥填写一次；图片、视频、音乐、配音分别选择“自定义接口”或“统一网关”。旧项目默认自定义，并保留已有供应商、模型、地址与密钥。网关模型、音乐/语音供应商、视频映射及旁白音色另存为 `NEW_API_*` 配置，切换模式不会改写自定义凭据。

地址是保留反向代理前缀的网关根，不自动删除 `/v1`。标准 OpenAI、Gemini 路径分别按需增加 `/v1`、`/v1beta`；私有插件使用自己的根路由。更换已有网关地址必须重新填写对应密钥。GET 只返回密钥是否存在及掩码；空密钥保存代表保留，明确清除只允许全部能力切回自定义后执行。页面不把密钥写入本地存储。

## 模块合同

新增共享 `skills/shared/scripts/media_gateway.py`，提供：

- `gateway_settings(env) -> dict`：返回 `baseUrl/keyConfigured/keyMasked/modes/image/video/music/voice/geminiTtsApi/configured/managed`；`configured` 与 `managed` 均为四能力布尔映射。
- `gateway_updates(payload, current) -> dict[str,str]`：验证完整设置并生成独立保存的环境变量增量。payload 的 `key` 空串保留，`clearKey` 显式清除，可选 `adopt` 列表表示明确接管旧手工能力。
- `validate_gateway_env(env, previous=None)`：校验直接 `/api/env` 写入也遵守模式、根地址、模型与密钥规则。
- `resolve_media_environment(group, env) -> dict[str,str]`：仅为当前能力派生执行环境，不修改输入字典或持久配置。
- `gateway_enabled(group, env)`、`gateway_managed(group, env)`、`gateway_readiness(group, env)`、`GATEWAY_ENV_KEYS`、`GATEWAY_SPEC` 供后端与注册表复用。
- `refresh_gateway_environment(env, saved)`：只刷新已保存的网关字段，覆盖常驻进程的旧值并处理清除；不全局派生供应商别名。

设置接口 `GET/PUT /api/settings/media-gateway`。PUT body 为嵌套完整设置：`baseUrl/key?/clearKey?`，`modes:{image,video,music,voice}`（值 custom/new-api），`image:{model}`、`video:{model,profile}`、`music:{provider,model}`、`voice:{provider,model,voiceId}`、`geminiTtsApi`。PUT 返回同 GET，并含 `ok:true`。缺少有效模型、地址或密钥时启用能力会原子拒绝；不发送模型请求。

模式环境沿用 `IMAGE_API_MODE/VIDEO_API_MODE/VOICE_API_MODE` 的 custom/new-api 和音乐 legacy/new-api。专用字段为 `NEW_API_IMAGE_MODEL/NEW_API_VIDEO_MODEL/NEW_API_VIDEO_PROFILE/NEW_API_MUSIC_PROVIDER/NEW_API_MUSIC_MODEL/NEW_API_VOICE_PROVIDER/NEW_API_VOICE_MODEL/NEW_API_VOICE_ID`；`GEMINI_TTS_API` 可配置自定义直连协议，统一网关 Gemini 固定 Generate Content。

旧手工适配配置的 mode 虽为 new-api，但没有本能力的统一选择字段，`managed` 为 false。页面显示临时选项“保留旧版 New API”，保留旧表编辑；明确选择“统一网关”才提交 `adopt` 并校验新配置。未修改旧能力的 mode 和设置、也未明确 adopt 时，保存其他能力不会给旧能力写入统一字段或改变其协议。这个临时选项不是新的持久化执行模式。

## 运行与隔离

每个媒体脚本加载环境后，只解析本能力的统一配置。音乐与配音虽然可能使用同名 DashScope 变量，也不会将临时派生值写入父进程或其他能力。图片生成/编辑同时使用统一地址、模型与密钥，变体仍使用现有私有插件；视频使用 OpenAI 兼容入口及已实现的 profile；音乐按选择的 native 路由；配音使用现有私有插件或核心 OpenAI/Gemini 路由。显式请求与所选统一供应商冲突时，提交前拒绝。

后端配置状态、技能可用判断与脚本配置查询使用同一解析合同。统一模式生效时旧供应商表显示为保留配置，编辑仍可保存，不将旧表误当成当前执行路由。任务启动读取最新保存的网关字段；不把按能力派生的旧供应商 key 全局注入 Agent，避免跨能力串用。

## 范围与验证

本批不接订阅媒体额度、不开发新供应商协议、不迁移旧音色、不部署服务器组件。配置完整与真实模型可用分开显示。保存成功后下一次脚本执行生效。

先写失败用例，再实现：配置往返与原子拒绝、密钥留存/替换/清除、地址变更保护、前缀派生、四类脚本执行环境、跨能力隔离、自定义行为保留、配置状态一致。完成后执行后端与客户端回归、前端测试/构建/lint，并在隔离工作区的真实浏览器验证保存、刷新、切回自定义、窄屏和主题。真实项目 `.env`、原有UI修改和 main ref 应保存并核对。
