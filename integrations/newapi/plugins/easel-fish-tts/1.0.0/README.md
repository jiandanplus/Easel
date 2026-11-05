# Easel Fish TTS 私有插件 1.0.0

配合 [Fish 私有二进制桥](../../../fish_bridge/README.md)使用，目标宿主 New API `v1.0.0-rc.45`。插件不直接接收 Fish 二进制响应，也不把音频 base64 塞入任务 JSON；私有桥保存有界 TTL 音频，插件只处理元信息与产物描述符。

## 客户端合同

`POST /easel/fish/v1/tts` 使用用户的 New API gateway key，JSON 示例：

```json
{
  "model": "easel-fish-s2.1-pro",
  "text": "你好，欢迎使用 Easel。",
  "format": "mp3",
  "references": [{"audio": "UklGRg==", "text": "参考音频的转写文本"}]
}
```

示例 base64 仅表达字段格式，不是可合成的有效音频。可以用管理员白名单的 `reference_id` 替代 `references`，两者不能同时提供。支持 1–2 段 inline 音频，每段最多 1 MiB；不支持参考 URL 或任意共享供应商 voice ID。

模型为 `easel-fish-s2.1-pro`、`easel-fish-s2.1-pro-free`、`easel-fish-s2-pro`、`easel-fish-drama-3`。文本最多 2000 个 UTF-16 code units，支持 mp3/wav/pcm/opus 和可选 `speed` 0.5–2。没有标准模型名称或标准音频路由覆盖。

成功响应：

```json
{
  "task_id": "task_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "status": "SUCCESS",
  "content_url": "/v1/tasks/task_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/artifacts/audio/content",
  "mime_type": "audio/mpeg",
  "size": 12345,
  "expires_at": 1234567890
}
```

客户端应携带同一 gateway key 通过同 origin 的通用 task artifact API 下载；不得访问内部桥或携 gateway key 跟随重定向。`GET /easel/fish/v1/tasks/{publicTaskId}` 返回相同的公共投影。桥发生未知消费时保留非终态，由私有 GET 查询现有 receipt；客户端不能自动创建新任务重试收费操作。产物过期后原任务仍然成功，但响应标记 `artifact_expired: true`，不自动再次合成。

## root 安装与配置

安装此 `plugin.js`，建立专用 Task Plugin 渠道。base URL 仅允许 `http://fish-bridge:8787`、`http://127.0.0.1:8787` 或 `http://localhost:8787`；channel API key 是桥认可的强随机内部 secret，而非 Fish 供应商 key。供应商 key 仅在桥环境中。

插件以宿主的 publicTaskId 作为桥幂等键。`listArtifacts` 仅为成功且未过期的音频声明 artifact；`buildContentRequest` 只返回私有 GET/HEAD 描述符、同 secret 鉴权和受限 Range。公开 content URL 由 New API 的通用 `/v1/tasks/{id}/artifacts/audio/content` 提供，须经过宿主用户归属检查。

用量 `synthesis_requests` 与 `input_characters` 是桥观察到的请求/输入事实，不是供应商精确 token 或账单。root 必须设置明确服务价格，中央会员与账本并未在本插件里实现。

本轮仅新增插件和私有桥，没有部署它们，也没有调用真实 Fish。Easel 现有直连 `voice_clone.py` 不能只换 base URL：网关模式需明确提交此任务合同、轮询公共任务并按受保护的 artifact 下载；自定义接口模式应保留。

## 已执行验证

Node 单元测试 10/10；真实 rc.45 moejs CLI lint 通过，`fixtures.json` 21/21 重放通过。fixture `unixNow` 固定为 1000，覆盖私有路由、模型白名单、inline base64、内部 secret、稳定幂等键、pending、GET 查询、完整元信息、TTL、Range、内容描述符与观察用量。

```text
node --test integrations/newapi/plugins/easel-fish-tts/1.0.0/plugin.test.mjs
/private/tmp/new-api-rc45-cli plugin lint integrations/newapi/plugins/easel-fish-tts/1.0.0/plugin.js
/private/tmp/new-api-rc45-cli plugin test integrations/newapi/plugins/easel-fish-tts/1.0.0/plugin.js --fixture integrations/newapi/plugins/easel-fish-tts/1.0.0/fixtures.json
```

New API 通用产物路由、供应商真实合成、价格、中央 pending、网络隔离和负载仍需独立现场验证。桥的二进制存储边界、部署模板及限制详见关联 README。
