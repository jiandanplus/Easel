# Easel Gemini TTS 与 New API 原生接口

日期：2026-10-20。已核对当前 Google 官方文档、New API `v1.0.0-rc.45` 源码并完成本地模拟响应回归；没有配置或调用线上 Gemini 渠道，不能作为真实生成、音频播放或计费验收。

Easel 的 `voice_clone.py` 现在默认使用 Gemini Generate Content TTS。该协议可以复用 New API 核心原生 Gemini 路由，无需为了预置声音 TTS 增加一个重复的 task 插件。用户自定义直连和显式 Interactions 模式仍保留。

## New API 接入

```dotenv
VOICE_PROVIDER=gemini
GEMINI_TTS_API=generate-content
GEMINI_BASE_URL=https://api.drawbookai.cn/v1beta
GEMINI_API_KEY=<有权限的New API业务令牌>
GEMINI_TTS_MODEL=<该渠道实际允许的Gemini TTS模型或映射>
GEMINI_VOICE=Kore
```

`GEMINI_BASE_URL` 保持原来的 API 前缀语义。程序在其后追加 `/models/{model}:generateContent`；自定义 `/custom/v1beta` 前缀也原样保留，不会按域名擅自替换路径。不能把普通 OpenAI `/v1` Base URL 当作 Gemini 的 `/v1beta` 入口。

请求使用 `contents[].parts[].text`、`generationConfig.responseModalities=["AUDIO"]` 和 `speechConfig`。用户填入的是网关业务令牌，真实上游凭据仍在 New API 渠道内。需要先配置实际支持 TTS 的 Gemini 渠道、模型权限和价格；目录返回或安装其他媒体插件不等于这个渠道已经可用。

源码依据：

- [原生路由](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.45/router/relay-router.go)注册 `/v1beta/models/*path`。
- [Gemini 配置结构](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.45/relaykit/dto/gemini.go)保留 `responseModalities`，`speechConfig` 为原始 JSON。
- [原生响应处理](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.45/relay/channel/gemini/relay-gemini.go)在 Gemini 输出协议下保留响应 body，包括音频 `inlineData`。

这些是协议可行性证据，没有代替真实模型和该部署计量的验收。

## Interactions 与模型差异

Google 当前确有 [Interactions TTS](https://ai.google.dev/gemini-api/docs/speech-generation)；`response_format` 音频请求和 `generation_config.speech_config` 不是虚构协议。已有直连用户可以显式设置：

```dotenv
GEMINI_TTS_API=interactions
GEMINI_BASE_URL=https://generativelanguage.googleapis.com/v1beta
```

此时程序追加 `/interactions`，请求 WAV，并识别 SDK 风格的 `output_audio` 或 REST `steps[].content[]` 音频。New API rc.45 未注册该原生路径，不能将这个模式直接指向 New API 后宣称可用。两个模式之间不自动重试，避免未知失败后重复生成收费请求。

原默认模型 `gemini-2.5-flash-preview-tts` 保持不变，没有擅自切换用户模型或声线；该模型当前是否仍向特定账户开放，需要目标供应商现场确认。模型可通过参数或 `GEMINI_TTS_MODEL` 覆盖。

旧一代 Generate Content 使用 `prebuiltVoiceConfig.voiceName`；当前 Gemini 3.8 单声线示例使用 `voiceConfig.voice`。3.8 把文本当逐字转录，所以客户端把情绪放入 `speech_metadata.style`，不会把“请用温柔的语气”拼进要读出的正文。

**New API rc.45 的 GeminiPart 没有 `speech_metadata` 字段，当前核心转换可能丢失该元数据。** 这影响通过该宿主使用 3.8 的情绪控制，不能宣称已完整支持；本次没有修改网关核心。需要该能力时，应升级/扩展宿主并验证字段保留，或使用明确支持的直连路径。自定义声音创建、扩展声线目录和声音复制也不属于这次预置声线改造。

参考：[Generate Content TTS](https://ai.google.dev/gemini-api/docs/generate-content/speech-generation)、[Interactions 迁移说明](https://ai.google.dev/gemini-api/docs/interactions-breaking-changes-may-2026)。

## 音频处理

客户端严格校验 Base64，跳过非音频 inlineData。已有合法 RIFF/WAV 文件保持原容器；明确为 PCM 的音频才添加 WAV 头。PCM 优先使用 MIME 中的 `rate` 和 `channels`，缺失采样率时才使用 `GEMINI_TTS_RATE`（默认 24000）；16 位采样的字节长度也会检查。

MP3、OGG/Opus 等已编码音频不会被当作 PCM 重复包装。目标扩展名与源格式不同时使用本地 FFmpeg 转换；无法转换时保留真实源格式并返回实际文件路径。未知 MIME、非法或空 Base64、损坏 WAV、非法 PCM 会明确失败，不输出伪装成音频的文件。

## 验证

```sh
python3 -m pytest -q tests/test_gemini_tts.py
python3 skills/shared/scripts/voice_clone.py selftest
```

回归使用假 HTTP 响应检查路径、请求字段、用户 Base URL、显式 Interactions、MIME/采样率、WAV 不重复包装、非音频跳过和 Base64 拒绝。真实验收仍需受限令牌、已配置渠道、供应商生成结果、可播放音频、用量与账单核对。当前没有这项线上证据。
