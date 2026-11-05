# Easel 音乐与语音客户端接入 New API

客户端位置：`skills/shared/scripts/ai_music.py`。只有显式设置 `MUSIC_API_MODE=new-api` 才选择本文协议。未设置、空值或 `legacy` 都保留既有 DashScope / Suno 自定义接口；客户端不根据域名猜测接口，也不会在提交失败后切换协议重新生成。

这里的验收是本地 HTTP mock 协议验证。安装了服务端插件、配置了客户端或通过 mock 测试，都不等于供应商凭据、渠道、计费和真实音频生成已经验证。服务端需要可调用的渠道、对应模型授权和有效供应商配置；客户端填写的是网关模型调用 key，不是控制台管理 PAT。

## 地址与模式

New API 模式下，base 的含义是**网关路由的根地址**，例如 `https://api.drawbookai.cn`，不是默认的 `/v1` OpenAI 根地址，也不是 DashScope 官方 `/api/v1` 根地址。

如果反向代理将整个 New API 挂在 `/custom/prefix`，则把该前缀写进 base，例如 `https://example.com/custom/prefix`。客户端只去掉末尾 `/` 并拼接对应插件路由，完整保留前缀。显式设置的 `/v1` 同样会保留，不会被自动删除；只有代理确实把插件路由挂在该前缀时才能这样设置。

| Provider | Base 变量 | New API 提交路径 | 返回与查询 |
| --- | --- | --- | --- |
| `dashscope` | 必填 `DASHSCOPE_BASE_URL` | `/easel/dashscope/api/v1/services/audio/music/generation` | 同步完成后直接下载 `output.audio.url` |
| `suno-compatible` | 必填 `MUSIC_BASE_URL` | `/suno/submit/music` | 字符串 `data` 是任务 ID；GET `/suno/fetch/{URL编码后的ID}` |

`check` 仍是离线配置检查。New API DashScope 的 base 为必填项，避免把私有插件路径误发到默认官方 DashScope 地址。非法 `MUSIC_API_MODE` 会在发请求之前报错。

## DashScope Fun-Music

使用本仓库私有插件 `integrations/newapi/plugins/easel-dashscope-music/1.0.0/plugin.js`，不是原来的通用异步音频示例。环境示例中的 key 由管理员在私密 `.env` 中填写：

```dotenv
MUSIC_API_MODE=new-api
MUSIC_PROVIDER=dashscope
DASHSCOPE_BASE_URL=https://api.drawbookai.cn
DASHSCOPE_API_KEY=在私密配置中填写网关模型调用key
DASHSCOPE_MUSIC_MODEL=fun-music-v1
```

未设置模型时默认 `fun-music-v1`。`--model` 优先于 `DASHSCOPE_MUSIC_MODEL`（兼容原 `DASHSCOPE_MODEL` 别名）。当前插件声明 `fun-music-v1` / `fun-music-preview`；不能把 legacy 的 `audio-generation` 默认值当作 Fun-Music 模型。

```bash
rtk proxy python3 skills/shared/scripts/ai_music.py check --provider dashscope
rtk proxy python3 skills/shared/scripts/ai_music.py generate --provider dashscope \
  --prompt '温暖的钢琴和轻鼓，适合旅行视频' --instrumental \
  -o outputs/ai-music/new-api-bgm.mp3
```

请求正文：

```json
{
  "model": "fun-music-v1",
  "input": {
    "prompt": "温暖的钢琴和轻鼓，适合旅行视频",
    "is_instrumental": true
  }
}
```

传入 `--lyrics` 时正文增加 `input.lyrics`，并把 `input.is_instrumental` 设为 `false`。客户端不发送 `parameters` 或 `X-DashScope-Async`。`--duration` 不属于该插件协议，提供后会在发送请求前拒绝，而不是静默忽略。

只接受同时满足 `output.task_status=SUCCEEDED`、`output.finish_reason=stop` 和有效 `output.audio.url` 的同步响应。顶层 `code` / `error` 或 `success:false` 是业务错误。响应中即使有音频 URL，只要仍是 `RUNNING`、`UNKNOWN`、失败或未验证完成态，也不会下载。不会构造上游 `/tasks/...` 轮询端点或再次提交。`--timeout` 在这里限制同步生成请求的等待时间，`--poll-interval` 不用于 Fun-Music。

## Suno native 路由

使用 rc.45 内置 `sunoapi` 插件，来源是锁定的官方 [`plugins/tasks/sunoapi/plugin.js`](https://github.com/QuantumNous/new-api/blob/6cf0d90b5ff27f901697dddd61e0c28aba8efefc/plugins/tasks/sunoapi/plugin.js)。原生 `music` 路由在插件 `decodeNativeSubmit` 中固定网关模型为 `suno_music`。

```dotenv
MUSIC_API_MODE=new-api
MUSIC_PROVIDER=suno-compatible
MUSIC_BASE_URL=https://api.drawbookai.cn
MUSIC_API_KEY=在私密配置中填写网关模型调用key
```

在这个模式中，`--model` 或 `MUSIC_MODEL` 表示上游生成版本 `mv`。如果未指定，就不发送 `mv`，由当前插件使用源码中的默认值 `chirp-v3-0`。不会继承 legacy 的 `music-1` 默认值；切换旧配置时，应检查并删除或明确更正原有 `MUSIC_MODEL`。插件固定的计费模型 `suno_music` 不作为 `mv` 或正文 `model` 发送。

无歌词时，将 `--prompt` 转换为 `gpt_description_prompt`：

```json
{
  "gpt_description_prompt": "温暖的钢琴和轻鼓，适合旅行视频",
  "make_instrumental": true
}
```

有歌词时，将歌词放进原生 `prompt`、风格描述放进 `tags`，并关闭 `make_instrumental`。显式模型覆盖放进 `mv`。不发送原来通用接口的独立 `lyrics` 字段或 `model:music-1`。native 协议没有在此声明请求时长参数，因此 New API Suno 同样拒绝 `--duration`；legacy 的 duration 行为保持原样。

只接受提交响应 `code=success` 且 `data` 为非空字符串。任务 ID 作为单独路径段进行完整 URL 编码，不会把 `/`、`?`、`#` 当成查询结构。只查询 `/suno/fetch/{id}`，不回退到 `/feed`、`/tasks` 或 `/generate`。

轮询 `data.status` 的 `NOT_START`、`SUBMITTED`、`QUEUED`、`IN_PROGRESS`；只有 `SUCCESS` 才读取任务数据中的 `audio_url` 并下载第一首可用音频。`FAILURE`、`UNKNOWN`、空值或其他未声明状态都会明确停止。业务错误、超时或未知状态不会触发第二次生成；原任务可能仍在运行，应先查看网关任务记录再决定是否手工重试。封面 `image_url` 或其他任意 URL 不会被误当成音频。

## 保持旧接口与下载行为

| 模式 | DashScope | Suno |
| --- | --- | --- |
| 未设置或 `MUSIC_API_MODE=legacy` | 保留 `/services/aigc/text2audio/generation`、`audio-generation`、异步 header、`parameters` 与 `/tasks/{id}` | 保留 `/generate`、`music-1`、`lyrics` / `duration` 与 `/feed/{id}` |
| `MUSIC_API_MODE=new-api` | 私有同步 Fun-Music 路由与严格完成态 | 官方 `/suno/submit/music` / `/suno/fetch/{id}` native envelope |

两种模式的音频下载都只使用下载请求的 User-Agent，不携带网关 `Authorization`，也不把 key 拼接到下载 URL。生成和查询请求才使用 provider 对应的 Bearer key。New API 业务错误不打印完整响应，避免服务端回显敏感信息。

New API 模式的提交与查询请求拒绝所有 HTTP 重定向。3xx 会直接报错，不向 Location 发送下一次请求，也不会改走 legacy 协议。旧 custom/legacy 模式保留原有传输行为。本地双 origin 回归测试实际启动两个回环 HTTP 端口，验证 POST 与 GET 的重定向目标均收到零请求和零凭据。

## 本地验证

```bash
rtk proxy python3 -W error -m unittest discover -s tests -p test_newapi_music.py -v
```

测试替换标准库 HTTP 调用，覆盖实际方法/路径/正文、同步完成态、业务错误、未知状态、超时、字符串任务 ID、URL 编码、自定义前缀、默认模型分离、legacy 提交/轮询保持、下载无 key，以及两种模式都不会失败后 fallback 重发。测试不调用真实网关或供应商，不产生真实生成和费用。

## 语音：显式私有网关模式

入口为 `skills/shared/scripts/voice_clone.py` 的 `enroll` / `clone` / `check`，协议代码独立放在同目录 `newapi_voice.py`，仅使用标准库。`VOICE_API_MODE` 未设置、空值或 `custom` 时，保留原供应商接口和所需配置。只有显式 `new-api` 才为 `minimax`、`dashscope`、`fish-audio` 启用下列私有插件。

```dotenv
VOICE_API_MODE=new-api
VOICE_PROVIDER=minimax
NEW_API_BASE_URL=https://api.drawbookai.cn
NEW_API_KEY=在私密配置中填写网关模型调用key
NEW_API_VOICE_TIMEOUT=180
NEW_API_VOICE_POLL_INTERVAL=2
```

三个私有 provider 统一使用 `NEW_API_BASE_URL` / `NEW_API_KEY`。base 是网关根地址，可保留整个自定义代理路径前缀，不自动增加或删除 `/v1`。要求 HTTPS；回环地址允许 HTTP 供本地开发。不会把 `MINIMAX_API_KEY`、`DASHSCOPE_API_KEY`、`FISH_API_KEY` 或供应商 Group ID 当作网关凭据。缺少网关变量时沿用正常的 `require_env` 错误提示；`check` 只检查网关变量、打印缺项，不打印 key，不联网。

`openai-compatible` 与 `gemini` 继续使用已有核心接口、配置和传输实现，不经过这个私有包装层；设置 `VOICE_API_MODE=new-api` 不会替它们重写 base/key。本文私有包装拒绝所有 HTTP 重定向，包括提交、查询及 artifact 下载，避免向 Location 转发凭据或重放收费 POST。

### MiniMax：上传公共任务 → 音色公共任务 → MP3

对应 `integrations/newapi/plugins/easel-minimax-voice/1.0.0/plugin.js`：

1. multipart POST `/easel/minimax/v1/files/upload`，字段为 `purpose=voice_clone` 与 `file`。本地样本必须为 mp3/m4a/wav，大小 1 字节到 20 MiB。
2. 上传完成响应的 `file_task_id` 是公共 `task_...` ID。客户端立即在 stderr 输出并保留该 ID，再 POST `/easel/minimax/v1/voice_clone`，正文只引用 `file_task_id`。
3. 只有完成响应 `status=SUCCESS` 且 `voice_task_id` 与返回公共任务 ID 一致，才打印最终音色成功。`--name` 不覆盖服务器生成的归属音色，实际用户侧音色 ID 是返回的公共 `voice_task_id`。
4. clone 命令 POST `/easel/minimax/v1/t2a_v2`，引用公共 `voice_task_id`，不发送供应商原始 `voice_id` / `file_id` / GroupId。默认模型 `easel-minimax-speech-2.8-hd`，可用 `--model` / `MINIMAX_MODEL` 指定插件声明的 `easel-minimax-speech-*` 模型。

```bash
rtk proxy python3 skills/shared/scripts/voice_clone.py check --provider minimax
rtk proxy python3 skills/shared/scripts/voice_clone.py enroll --provider minimax --sample sample.wav
# 用本人 enroll 实际返回的公共 voice_task_id 替换以下示例 ID。
rtk proxy python3 skills/shared/scripts/voice_clone.py clone --provider minimax \
  --voice-id task_BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB \
  --text '大家好，欢迎来到我的频道。' --emotion 愤怒 -o outputs/voice-clone/new-api.mp3
```

私有 MiniMax 插件只支持非流式短文本与 MP3：文本上限 500 个 UTF-16 字符单位，speed 为 0.5..2，情感使用原有中文映射后的受支持枚举。客户端核对公共任务完成态、`data.status=2`、`extra_info.audio_format=mp3`、usage 字段和有界合法 hex 音频，再原子保存；不把不完整结果或任意 URL 当成成功音频，不自动改走直连 API。

如果上传成功、后续登记失败，stderr 的失败消息保留 `file_task_id`。确认网关不存在已成功登记的结果后，可以手工继续，跳过再次上传：

```bash
rtk proxy python3 skills/shared/scripts/voice_clone.py enroll --provider minimax \
  --resume-id task_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
```

恢复先 GET `/easel/minimax/v1/tasks/{id}`，核对成功的 upload 阶段公共 `file_task_id`，再发送一次 clone 登记。不能把已登记音色任务或供应商 ID 当作 upload 恢复 ID。

### DashScope：登记一次，审核通过后合成同一模型

对应 `integrations/newapi/plugins/easel-dashscope-voice/1.0.0/plugin.js`：

1. POST `/easel/dashscope/voice/enroll`，正文为 `model=voice-enrollment`，`input` 包含 `action=create_voice`、`target_model`、1..10 位字母数字 prefix 和公网样本 URL。默认 target 为 `cosyvoice-v2`，可用 `--model` / `DASHSCOPE_TTS_MODEL` 指定插件支持的 CosyVoice 模型。
2. 保存并输出 create 阶段的公共 `voice_task_id`，此 ID 标记为“待审核”，不会打印成最终可合成音色。POST `/easel/dashscope/voice/verify`，正文为 `model=voice-enrollment` 与 `input:{action:query_voice,voice_task_id:原create公共ID}`。
3. `voice_status=DEPLOYING` 时有界继续 verify，始终引用原 create ID，不重新创建声音。每次 verify 是插件的 `query_voice` 操作，其定价由管理员配置。只有 `OK` 才返回该次**验证任务的公共 ID**作为用户后续 `--voice-id`。
4. 合成前 GET `/easel/dashscope/voice/tasks/{验证ID}`，重查 `OK`、公共 ID 与 target model。未显式指定模型时使用已登记 target；显式模型不一致会在合成 POST 前拒绝。
5. POST `/easel/dashscope/voice/synthesize`，模型与登记 target 一致；`input` 使用 `text`、公共 `voice_task_id`、format、rate，以及原中文情感生成的 `instruction`。不发送旧版供应商 voice ID、`instruct` 或 `parameters`。

```bash
rtk proxy python3 skills/shared/scripts/voice_clone.py enroll --provider dashscope \
  --sample-url https://example.com/owned-sample.wav --name myvoice --model cosyvoice-v3-plus
rtk proxy python3 skills/shared/scripts/voice_clone.py clone --provider dashscope \
  --voice-id task_DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD \
  --text '这是一段配音。' --emotion 温柔 -o outputs/voice-clone/cosy.mp3
```

完成响应必须含 `output.task_status=SUCCEEDED`、`finish_reason=stop` 和明确 `output.audio.url`，才下载音频。审核错误、`UNDEPLOYED`、未知状态或超时都不触发第二次 create。

审核未完成时，使用 stderr 保存的**create ID**恢复：

```bash
rtk proxy python3 skills/shared/scripts/voice_clone.py enroll --provider dashscope \
  --resume-id task_BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB
```

恢复先 GET 原任务，要求 `UNVERIFIED` create 阶段及匹配 target model，然后继续 verify；不接受已经是 query/synthesis 阶段的 ID，不再次 POST enroll。成功后最终用于 clone 的 ID 仍是新返回的验证任务 ID。

`--resume-id` 只支持 new-api 的 MiniMax / DashScope enroll；custom、Fish、OpenAI、Gemini 会明确拒绝，避免把恢复操作误当作新的付费登记。若 HTTP 超时或断线导致“结果可能未知”，先核对网关任务记录；成功任务已存在时直接使用其最终公共 ID，确认前置/后续操作状态后再决定是否手工恢复。客户端不会自动重发收费 POST，也不会保证一次未知网络失败等同于供应商未执行。

### Fish：参考样本 JSON 与受保护的任务 artifact

对应 `integrations/newapi/plugins/easel-fish-tts/1.0.0/plugin.js` 及管理员私有桥。客户端只请求 New API，不接触桥 URL、桥 secret 或供应商 key，也不需要单独 enroll。

```bash
rtk proxy python3 skills/shared/scripts/voice_clone.py clone --provider fish-audio \
  --sample sample.wav --sample-text '样本音频中实际说出的文字' \
  --text '用这个声音朗读这段内容。' -o outputs/voice-clone/fish.mp3
```

POST `/easel/fish/v1/tts` 的 JSON 使用私有模型（默认 `easel-fish-s2.1-pro`）、text、format、speed 和 `references:[{audio:纯base64,text:参考音转写}]`。单份样本最多 1 MiB，转写必填；文本与转写各最多 2000 个 UTF-16 字符单位。客户端目前从 CLI 发送一份样本，插件可支持的多样本并未伪装成此 CLI 已提供的功能。

也可以仅用 `--voice-id admin-approved-preset` 传 `reference_id`，必须是管理员在桥白名单中明确允许的预置 ID；它不是任意其他用户共享的音色。`--voice-id` 与 `--sample` 必须二选一。白名单最终由服务器检查，客户端不会绕过或把它改成别的 provider 音色。

提交仅一次。pending 时 GET `/easel/fish/v1/tasks/{公共任务ID}`；失败、未知或超时不会重新合成。只有 `SUCCESS` 且 artifact 未过期、MIME/size/content 路径与预期一致，才构造**同一网关和原路径前缀**的 `/v1/tasks/{id}/artifacts/audio/content` 下载。实际字节长度与 MIME 还会再次校验。过期 artifact 直接报错，不产生新的付费生成。

语音私有包装的下载边界与音乐不同：同 gateway origin 的受保护内容请求携带 `NEW_API_KEY`；供应商外部音频 URL不携带网关 key。origin 包括 scheme、hostname 与有效端口，其他端口同样视为外部。所有下载拒绝重定向，校验通过前不覆盖已有输出文件。

### 语音与重定向验证

```bash
rtk proxy python3 -m pytest tests/test_newapi_music.py tests/test_newapi_voice.py tests/test_gemini_tts.py -q
```

音乐和语音测试包含实际本地双 origin 回环 HTTP 回归；如运行环境禁止绑定端口，需要在获准的本地运行环境执行，不能把未运行的用例标为通过。其余协议测试使用精确模拟 HTTP 响应。Gemini 原有 20 项协议/音频容器测试继续单独保留，私有语音接入不改其 helpers。本地通过不构成真实供应商语音克隆、Fish 桥部署或生产计费验收。
