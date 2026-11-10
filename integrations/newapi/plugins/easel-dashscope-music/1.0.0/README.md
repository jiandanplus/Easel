# Easel DashScope Fun-Music 插件

日期：2026-10-20。版本：`1.0.0`。目标合同：New API `v1.0.0-rc.45` Task Plugin API v1。此目录为私有插件源码和离线验证材料；没有部署记录，没有真实供应商调用或计费验收记录。

本插件调用阿里云当前公开的 **Fun-Music 非流式同步接口**，支持 `fun-music-v1` 和 `fun-music-preview`。成功结果作为即时完成任务交给 New API 持久化，后续查询读取宿主任务记录，音频可通过宿主 artifact/content 接口下载。

## 已核对的上游合同

- [Fun-Music API 参考](https://help.aliyun.com/zh/model-studio/fun-music-api)：`POST /api/v1/services/audio/music/generation`，非流式返回 `output.audio.url`、`output.finish_reason="stop"` 和 `usage.duration`。
- [Fun-Music 使用说明](https://help.aliyun.com/zh/model-studio/fun-music)：北京地域，使用前需申请开通；下载 URL 有效期为 24 小时。可在渠道中配置业务空间专属域名，也可使用仍受支持的旧北京域名。
- [New API rc.45 插件合同](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.45/docs/plugin-api/v1.md)：身份、任务归属、渠道、网络、持久化及结算属于宿主职责。

本插件没有实现供应商 SSE，也没有虚构异步供应商查询接口。它不接受旧 `audio-generation` 模型、`parameters.duration` 或 `X-DashScope-Async` 工作流。`stream`、`background` 请求被明确拒绝。

## 原生路由

| 方法 | 插件路由 | 行为 |
| --- | --- | --- |
| POST | `/easel/dashscope/api/v1/services/audio/music/generation` | 生成音乐，成功时立即持久化并返回公开任务 ID、音频 URL 和供应商时长 |
| GET | `/easel/dashscope/api/v1/tasks/{task_id}` | 读取宿主已保存状态，不重新向供应商提交或查询 |

路由使用私有前缀，不占用官方 Alibaba、其他工厂插件或核心协议的路径。任务访问使用 New API 业务令牌；上游渠道密钥仅由宿主注入 driver，不能放进产品客户端。

请求示例：

```json
{
  "model": "fun-music-v1",
  "input": {
    "prompt": "轻快的钢琴与吉他纯音乐",
    "is_instrumental": true,
    "format": "mp3"
  }
}
```

`input` 可包含官方字段 `prompt`、`lyrics`、`is_instrumental`、`gender`、`format`、`enable_aigc_watermark`。插件检查基础类型、模型及明显非法输入；歌词的具体语言限制仍由供应商检查。不会静默把不支持的参数转成另一种生成语义。

## 渠道配置与计量边界

管理员安装源码后，创建 **Task Plugin** 渠道并绑定 `easel-dashscope-music`。配置北京地域的渠道凭据、明确的上游 Base URL 及已开通的模型。Base URL 可以是域名根地址，也可以以 `/api/v1` 结尾；插件只追加一次 API 前缀。

完成用量字段为 `duration_seconds`，取自供应商实际返回的正数 `usage.duration`。插件不从用户请求推算时长，不写价格、额度或退款金额。缺少时长、时长为零、结果未自然完成或没有音频 URL，都不会被标为可验证的成功。

供应商没有在本次核对的请求合同中提供可执行的时长上限，因此请求侧 `extractUsage` 返回 `null`，表示尚不知道用量，不能解释成零消耗。上线前必须为每个模型配置并验证宿主的预扣、结算和限流策略，确认完成时按实际秒数结算；本插件的离线测试没有验证账户余额或支付账本。未证明额度预留足够之前，不应开放无限额生产调用。

返回异常发生在上游已执行之后时，供应商可能已产生费用。New API 的错误或退款行为不能自动作为中央会员钱包释放预留的证据；中央平台仍需按既定 pending/对账规则处理。本插件不修改宿主结算逻辑。

## 产物、安全与恢复

`listArtifacts` 返回稳定的 `audio` 产物键。宿主的通用路径为：

```text
GET /v1/tasks/{task_id}/artifacts
GET|HEAD /v1/tasks/{task_id}/artifacts/audio/content
```

内容描述符使用 `credentialless: true`，不把渠道 Authorization 或其他供应商凭据发给音频 CDN。公共 URL、重定向、SSRF、Range、安全条件头和下载访问控制由宿主执行。原生成功响应含供应商音频 URL；需要统一下载审计的客户端应优先消费宿主 artifact/content。

公开响应只使用宿主 `task_id`，不暴露供应商音频 ID 或请求 ID。查询只能读取该宿主记录，没有以私有供应商 ID 绕过所有权检查的入口。所有权隔离依赖真正的宿主认证与数据库；纯函数测试不能证明多用户端到端隔离。

音频 URL 过期后，插件不会自动重新生成，因为重新生成可能再次收费。应及时下载归档；重新生成需要一次明确的新任务。升级插件前也要验证仍能读取已有 `task.data`。

## Easel 客户端待迁移差异

本次没有修改 Easel 的客户端或设置界面。现有 `skills/shared/scripts/ai_music.py` 使用未经真实验证的旧合同，不能仅更换域名后宣称兼容：

| 现有脚本 | 当前官方合同与本插件 |
| --- | --- |
| 默认模型 `audio-generation` | `fun-music-v1` / `fun-music-preview` |
| `/services/aigc/text2audio/generation` | `/services/audio/music/generation` |
| `parameters.instrumental` | `input.is_instrumental` |
| `parameters.duration` | 本次官方请求合同无该参数，需移除或向用户明确不支持 |
| 提交后供应商 `task_id`，再向上游 `/tasks/{id}` 轮询 | 非流式同步完成；本插件另外提供宿主已保存任务的查询 |

客户端还需要将网关地址与供应商直连地址分开，显式选择提供方，避免依赖域名判断。`DASHSCOPE_BASE_URL` 在现有声音、视频和音乐脚本中的前缀含义不一致，不能用一个全局地址同时覆盖所有分支。

## 离线验证

从 Easel 仓库根目录运行：

```sh
node --test integrations/newapi/plugins/easel-dashscope-music/1.0.0/tests/plugin.test.mjs
```

用目标版本的官方插件 CLI 验证：

```sh
new-api plugin lint integrations/newapi/plugins/easel-dashscope-music/1.0.0/plugin.js
new-api plugin test integrations/newapi/plugins/easel-dashscope-music/1.0.0/plugin.js --fixture integrations/newapi/plugins/easel-dashscope-music/1.0.0/fixtures/golden.json
```

测试覆盖私有路由、真实端点、输入拒绝、成功和缺失用量、未知状态、宿主查询呈现、音频 artifact 与下载凭据边界。fixture 中均为假令牌与保留测试域名；CLI 不执行 HTTP 描述符。测试通过只证明离线合同，不代表模型开通、渠道可达、真实音频可播放、预扣计费或生产部署已经验收。
