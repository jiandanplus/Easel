# Easel DashScope CosyVoice 插件

日期：2026-10-20。版本：`1.0.0`。目标宿主合同：New API `v1.0.0-rc.45` Task Plugin API v1。此目录是本地源码和离线验证材料，尚未部署，也没有真实音色、真实合成、计费或多用户端到端验收记录。

插件实现当前阿里官方的 HTTP 链路：**创建音色 → 查询音色审核状态 → 使用已归属且审核通过的音色合成音频**。只支持本文件列出的 CosyVoice 模型与非流式 HTTP，不将旧客户端的假设路径当作实际 API。

## 官方合同与范围

- [声音复刻 HTTP API](https://help.aliyun.com/zh/model-studio/voice-clone-design-http-api)：`POST /api/v1/services/audio/tts/customization`，创建使用 `create_voice`，查询使用 `query_voice`；官方另有 list/update/delete，本插件不开放这些动作。
- [CosyVoice HTTP 合成 API](https://help.aliyun.com/zh/model-studio/cosyvoice-tts-http-api)：`POST /api/v1/services/audio/tts/SpeechSynthesizer`，非流式返回音频 URL 和 `usage.characters`。北京地域有该 HTTP 合成入口，无需伪造 `/tts/generation`。
- [声音复刻说明](https://help.aliyun.com/zh/model-studio/voice-cloning-user-guide)：音色绑定 `target_model`，不能跨模型使用；创建后的审核状态包括 `DEPLOYING`、`OK`、`UNDEPLOYED`。
- [New API origin tasks 合同](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.45/docs/plugin-api/v1.md#origin-tasks)：宿主校验引用任务的所有权和渠道一致性，driver 才能得到私有上游 ID。

支持模型为 `cosyvoice-v3.5-plus`、`cosyvoice-v3.5-flash`、`cosyvoice-v3-plus`、`cosyvoice-v3-flash`、`cosyvoice-v2`。登记、查询的模型固定为 `voice-enrollment`。这不代表所有用户均已开通这些模型，渠道与地域仍需现场验证。

## 私有路由和操作顺序

| 方法 | 路由 | 行为 |
| --- | --- | --- |
| POST | `/easel/dashscope/voice/enroll` | 创建音色，返回公开 `voice_task_id`；创建成功不代表审核已经通过 |
| POST | `/easel/dashscope/voice/verify` | 引用创建任务，向官方 `query_voice` 查询审核状态，返回本次查询任务的公开 `voice_task_id` |
| POST | `/easel/dashscope/voice/synthesize` | 引用审核状态为 OK 的查询任务，并使用相同目标模型合成 |
| GET | `/easel/dashscope/voice/tasks/{task_id}` | 读取宿主已保存记录，不重新调用上游 |

创建请求：

```json
{
  "model": "voice-enrollment",
  "input": {
    "action": "create_voice",
    "target_model": "cosyvoice-v2",
    "prefix": "myvoice",
    "url": "https://sample.example.test/my-voice.wav"
  }
}
```

以上音频地址仅用于展示形状，必须替换为自己符合官方要求、可公网访问的音频 URL。得到公开创建任务 ID 后查询：

```json
{
  "model": "voice-enrollment",
  "input": { "action": "query_voice", "voice_task_id": "创建任务的公开ID" }
}
```

如果响应 `output.voice_status` 为 `DEPLOYING`，可以稍后再次用原创建任务 ID 查询；`UNDEPLOYED` 表示审核未通过。只有 `OK` 可以用于合成。合成必须引用**状态为 OK 的查询任务**返回的公开 ID：

```json
{
  "model": "cosyvoice-v2",
  "input": {
    "voice_task_id": "审核通过的查询任务公开ID",
    "text": "你好，欢迎使用。",
    "format": "wav",
    "rate": 1
  }
}
```

合成输入支持 `text`、`voice_task_id`、`format`、`sample_rate`、`volume`、`rate`、`pitch`、`instruction`。插件拒绝用户直接填写供应商 `voice` 或 `voice_id`；也不接受其他操作或把未知字段转发给供应商。

## 音色归属与数据保留

登记和查询都是即时完成的 HTTP 操作，但声音资源生命周期独立于该操作的任务状态。公开响应因此分别报告 `task_status` 和 `voice_status`；`task_status=SUCCEEDED` 的登记只表示官方创建操作成功，返回的音色仍为 `UNVERIFIED`。

真实供应商 `voice_id` 只通过 `parseSubmitResponse.taskId` 交给宿主的私有上游任务 ID，**不写入公开 `task.data`**。这是必要的边界：rc.45 的 `BuildTaskPluginView` 只替换 `id/task_id/taskId` 已知字段，不会自动隐藏任意名为 `voice_id` 的字段。查询和合成从宿主注入的 `originTasks.upstreamTaskId` 取私有 ID，并检查前置操作、状态和目标模型。

`originTaskIds` 让宿主执行用户归属、插件平台和同一渠道检查；插件也验证传入引用与 origin 一致。查询记录只保留目标模型、审核状态和实际用量，省略官方的样本 `resource_link`。不能把本目录中的纯函数测试描述为已经证明真实多用户数据库隔离；上线前仍需运行跨账号和跨渠道拒绝测试。

上述引用链依赖创建和审核任务记录持续保留。不能随意清除这些记录后继续使用公开音色引用。后续若迁移为独立中央声音资产表，应在保留所有权与渠道约束的前提下迁移，而不是让客户端重新传任意供应商 voice ID。

## 计量、渠道和产物

创建 Task Plugin 渠道并绑定 `easel-dashscope-voice`，配置同一地域的官方 Base URL、凭据及允许模型。Base URL 为根域名或以 `/api/v1` 结尾均可。声音登记和合成必须落在同一条可用渠道上；不能把登记和合成拆到使用不同凭据的渠道，再期望引用链成立。

登记和审核查询用量取自供应商的 `usage.count=1`，另报告操作类型 `create_voice` 或 `query_voice`；合成模型使用独立 usage profile，报告实际 `usage.characters`，不将字符伪装成 token。价格由宿主配置，报告一次查询事实也不表示查询必然收费。

请求侧用量返回 `null`，不以请求字符数冒充供应商最终计费字符，也不把未知量当作零。缺失、零值或非法的完成用量会被拒绝。上线前需验证宿主预扣与实际结算；上游已执行后的异常仍可能产生费用，中央钱包应保留 pending/对账语义，不直接复制宿主退款结果。

只有合成任务提供 `audio` artifact。音频 CDN 下载使用 `credentialless: true`，不携带渠道密钥；宿主负责 SSRF、重定向、安全 Range/条件头和访问控制。下载入口为 `/v1/tasks/{id}/artifacts/audio/content`，音频链接通常只有 24 小时有效期，不会自动重新合成收费任务。

本插件没有把私有音色映射交给核心 `/v1/audio/speech`，因为该标准入口不能仅凭用户随填 voice ID 推导本插件的资源归属。实时 WebSocket 和 SSE 合成也未实现；若以后需要，应增加宿主或专用执行层支持并单独验收。

## Easel 客户端迁移

本次未修改 Easel 的 `voice_clone.py` 或设置界面。现有脚本需要后续显式适配：

- 登记后保存公开任务引用，不能继续把真实供应商 voice ID 当作客户端配置。
- 合成前完成有归属的审核查询，将查询任务的公开 ID 放入 `voice_task_id`。
- 将旧 `/api/v1/services/audio/tts/generation` 改成插件私有合成路由；供应商真实路径是大小写敏感的 `/SpeechSynthesizer`。
- 将原来的 `input.instruct` 明确映射为官方 `input.instruction`，并按模型检查支持范围。
- 供应商直连、自定义接口模式仍需保留；不同能力的 Base URL 前缀不能共用旧脚本中含义不一致的配置。

## 离线验证命令

从 Easel 根目录执行：

```sh
node --test integrations/newapi/plugins/easel-dashscope-voice/1.0.0/tests/plugin.test.mjs
new-api plugin lint integrations/newapi/plugins/easel-dashscope-voice/1.0.0/plugin.js
new-api plugin test integrations/newapi/plugins/easel-dashscope-voice/1.0.0/plugin.js --fixture integrations/newapi/plugins/easel-dashscope-voice/1.0.0/fixtures/golden.json
```

fixture 均为假凭据和保留测试域名，不会执行网络描述符。覆盖创建、审核、合成、origin 拒绝、模型不符、私有 ID 不进入公开数据、用量、产物与凭据边界。真实模型开通、音色审核、跨用户归属拒绝、音频可播放和生产结算仍需使用目标部署单独验收。
