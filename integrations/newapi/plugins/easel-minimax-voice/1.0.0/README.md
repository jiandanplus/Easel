# Easel 私有 MiniMax 声音插件 1.0.0

本插件补充 Easel 当前 `voice_clone.py` 使用的样本上传、音色登记、克隆音色合成三个操作。目标宿主锁定 New API `v1.0.0-rc.45`，不替代标准系统音色 TTS。每个客户端 POST 只描述一次上游 POST；不会在 hook 中隐式上传后继续登记，或登记后自动合成。

**当前验收是本地 JavaScript 单元测试与真实宿主 JS 引擎的 fixture 重放。没有安装到服务器，没有使用真实供应商账号，未证明实际声音克隆、费用或跨账号 HTTP 隔离通过。**

## 公共接口与 ID 映射

所有路径以 `/easel/minimax` 为前缀，避免覆盖已有模型和标准 `/v1/audio/speech`。调用使用普通用户的模型网关令牌；安装和渠道配置使用 root 管理权限。

| 调用 | 请求 | 完成响应中的公共标识 |
| --- | --- | --- |
| `POST /easel/minimax/v1/files/upload` | multipart；一个 `file`，可选 `purpose=voice_clone` | `task_id`、`file_task_id` |
| `POST /easel/minimax/v1/voice_clone` | JSON：`file_task_id`，可选 `need_noise_reduction`、`need_volume_normalization` | `task_id`、`voice_task_id` |
| `POST /easel/minimax/v1/t2a_v2` | JSON：命名空间模型、`voice_task_id`、`text`；可选 `speed`、`emotion`、`stream=false` | `task_id`、`data.audio`（hex）、`extra_info` |
| `GET /easel/minimax/v1/tasks/:task_id` | 公共 task ID | 宿主鉴权后的已保存结果 |

示例登记请求（ID 为格式示例）：

```json
{
  "file_task_id": "task_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "need_noise_reduction": true
}
```

示例合成请求：

```json
{
  "model": "easel-minimax-speech-2.8-hd",
  "voice_task_id": "task_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "text": "你好，欢迎使用 Easel。",
  "speed": 1,
  "emotion": "happy"
}
```

`file_task_id` 和 `voice_task_id` 都是 New API 公共任务 ID，不是 MiniMax `file_id` 或 `voice_id`。解码器通过 `originTaskIds` 声明依赖，由宿主查询同一用户的任务、校验插件归属并固定同一渠道；驱动还要求依赖状态和操作类型正确。MiniMax ID 保存在宿主私有 upstream task 字段，公共 presenter 不返回该 ID。客户端不能提交原始供应商 ID，不能绕过归属校验使用系统或其他用户音色。

Easel 原有客户端目前仍直接调用 MiniMax 路由，并要求原始 `file_id`/`voice_id`；**仅替换域名不兼容**。网关模式需把上传响应保存成 `file_task_id`，登记响应保存成 `voice_task_id`，合成时用命名空间模型和新字段。自定义供应商模式维持原有客户端，不能将这个公共 ID 用到直连接口。本目录没有更改 `voice_clone.py`。

## root 配置边界

- 建立专用的 Task Plugin 渠道，绑定 `easel-minimax-voice`，配置供应商 API key。插件只接受 root 配置的 `https://api.minimax.cn`（国内）或 `https://api.minimax.io`（国际），不接受客户提交的 URL、凭据、路径或 GroupId。
- 新版官方示例不要求 GroupId。若遗留账号仍需该参数，插件可以从 root 的 base URL 中接收唯一数字查询项，例如 `https://api.minimax.cn?GroupId=123456`，并向三个固定上游路径添加该参数。此兼容配置只通过本地描述符测试，未验证渠道 UI 是否接受以及实际账号是否仍需它；不要将它当作已成功迁移的证据。
- 不默认沿用旧客户端的 `api.minimax.chat`。其他地域入口、自建代理和北方地区专用入口需核对官方合同后另行开放，当前会拒绝。
- 上传和登记模型分别为 `easel-minimax-upload`、`easel-minimax-clone`。合成模型包括 `easel-minimax-speech-{2.8,2.6,02,01}-{hd,turbo}`，只允许固定集合。渠道可将该命名空间别名映射到同集合内真实 MiniMax 模型；任意路径或其他模型会被拒绝。账号是否拥有模型权限仍需真实验证。
- 模型列表和 API key 属于该供应商地域及账号。依赖任务固定原渠道，不能在上传后切换到另一个供应商账号。切换地域或撤销账号前应处理已登记音色。

## 文件、音频与宿主限制

- 上传仅转发宿主持有的一个 `request_file:file`，支持 mp3、m4a、wav，文件大小在 1 byte 到 20 MiB 之间。不支持样本 URL、额外文件、`clone_prompt` 或任意其他上传用途。音频时长与音质仍由 MiniMax 校验，插件不能读取音频字节测量时长。
- 登记不附带试听文本，避免一个登记操作同时发生试听合成。需要试听时另发合成任务。
- 合成只接受非空、最多 500 个 UTF-16 code units 的短文本，固定非流式、hex、mp3、16 kHz、32 kbps、单声道。速度限制 0.5–2。该限制用于缩小当前宿主 JSON 音频响应的范围，不保证所有文本都能在大小限制内。
- rc.45 宿主在解析提交响应前将正文限制为 1 MiB，插件另限制 hex 长度为 900,000 字符。不支持长音频、URL 输出、流式音频、字幕或二进制内容下载；应采用后续宿主内容存储或音频流接口补齐，不能静默截断或冒称完整覆盖。
- MiniMax 文件 ID 是 int64。字符串形式的完整 int64 可被原样保存；clone 描述符在需要时使用严格数字验证后的原始 JSON 数值字面量，避免再次浮点舍入。若上游以 JSON number 返回大于 `Number.MAX_SAFE_INTEGER` 的 ID，rc.45 已在 hook 前将其转成 float64，插件拒绝该值；要完整覆盖这种响应需宿主保留原始 JSON/int64，而非在插件中猜测丢失数字。
- HTTP 200 不代表操作成功：必须存在 `base_resp.status_code=0`。敏感输入、无最终音频、错误 hex、缺失实际用量都会失败。失败信息不透传上游可能包含的地址或凭据。
- 上游已经执行后发生超限、解析失败、断线等情况，不足以证明供应商没有收费。不要自动重放登记或合成 POST；启用正式流量前须验证宿主此类错误的重试和结算策略，并配置为不会因未知结果重复收费。当前插件没有跨 POST 的幂等恢复或上游任务查询能力。

## 用量与定价

插件不计算货币金额或伪造 token：

- 上传使用 `upload_requests=1`，登记使用 `registration_requests=1`，明确表示操作请求单位。它们不是 MiniMax token 或官方已收费用；root 应按实际服务政策配置价格（上传通常应显式配置为零）。
- 合成预留时的 `characters` 是请求文本长度估算；完成时必须读取真实 `extra_info.usage_characters`，并以响应事实覆盖估算。有效的零值保留。缺失事实不当作免费或成功。
- `audio_milliseconds` 来自供应商 `extra_info.audio_length`。由于宿主单位枚举受限，这两个字段在 manifest 中使用 `unit=count` 配合字符/毫秒标签；含义不变。
- 跨产品会员、共享余额、最终账务仍由中央服务负责。安装该插件没有实现中央账本衔接。

## 已执行验证

使用测试先行方式：插件文件创建前，单元测试因插件功能缺失失败；随后实现并完成宿主合同纠正。当前结果：

```text
node --test integrations/newapi/plugins/easel-minimax-voice/1.0.0/plugin.test.mjs
17 tests passed

/private/tmp/new-api-rc45-cli plugin lint integrations/newapi/plugins/easel-minimax-voice/1.0.0/plugin.js
plugin easel-minimax-voice@1.0.0 is valid

/private/tmp/new-api-rc45-cli plugin test integrations/newapi/plugins/easel-minimax-voice/1.0.0/plugin.js --fixture integrations/newapi/plugins/easel-minimax-voice/1.0.0/fixtures.json
29/29 fixture cases passed
```

CLI 来自固定 rc.45 的真实 `jsplugin.RunCLI`，验证实际 moejs 编译与 hook 行为；未启动完整 HTTP 宿主、数据库或外部模型调用。跨用户/跨渠道的数据库隔离、真实上传/登记/合成、计费、重试和过期音色恢复仍待现场验收。官方说明登记音色若 7 天内没有正式使用可能被删除，客户端需保留重新上传登记路径。

## 核对来源

- [国内文件上传](https://platform.minimax.cn/docs/api-reference/file-management-upload)、[国内音色快速复刻](https://platform.minimax.cn/docs/api-reference/voice-cloning-clone)、[国内同步语音合成](https://platform.minimax.cn/docs/api-reference/speech-t2a-http)。
- [国际文件上传](https://platform.minimax.io/docs/api-reference/file-management-upload)、[国际音色复刻](https://platform.minimax.io/docs/api-reference/voice-cloning-clone)。
- [rc.45 宿主 originTaskIds 校验](https://github.com/QuantumNous/new-api/blob/6cf0d90b5ff27f901697dddd61e0c28aba8efefc/middleware/task_plugin.go)、[driver context、JSON 正文和响应大小限制](https://github.com/QuantumNous/new-api/blob/6cf0d90b5ff27f901697dddd61e0c28aba8efefc/relay/channel/task/jsplugin/adaptor.go)。
- Easel 基线：[voice_clone.py](../../../../../skills/shared/scripts/voice_clone.py)。
