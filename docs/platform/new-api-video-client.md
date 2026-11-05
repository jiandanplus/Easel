# Easel 显式 New API 视频模式

2026-10-20：本轮仅在 `skills/shared/scripts/ai_video.py` 的 `openai-compatible` 分支增加显式 `VIDEO_API_MODE=new-api`。没有修改 DashScope、ARK、Kling、MaaS、Agnes 的专用 custom 流程，也没有替用户写入 `.env`、创建供应商渠道或发起付费视频请求。

此模式已通过本地模拟 HTTP 和 loopback HTTP 契约测试；真实供应商视频、实际播放、音画质量、计费和生产渠道可用性仍待配置供应商后验收。OpenAI 官方 Videos API、`sora-2` 和 `sora-2-pro` 已在 **2026-09-24** 关闭；保留 OpenAI Video 协议格式不能恢复已关闭的服务。本模式用于管理员实际配置、仍提供相应视频协议的供应商，以及 New API 其他视频 factory 的已确认子集。参见 [OpenAI 官方弃用记录](https://developers.openai.com/api/docs/deprecations#2026-03-24-sora-2-video-generation-models-and-videos-api)。

## 启用与回退

在用户自己的 `.env` 设置：

```dotenv
VIDEO_PROVIDER=openai-compatible
VIDEO_API_MODE=new-api
VIDEO_BASE_URL=https://your-gateway.example/prefix/v1
VIDEO_API_KEY=<该用户的 New API 业务令牌，不是管理员令牌或供应商密钥>
VIDEO_MODEL=<New API 已配置且有权限的模型名或渠道别名>
VIDEO_NEWAPI_PROFILE=veo
```

`VIDEO_MODEL` 或 `--model` 必须显式提供；新模式不使用旧流程的 `sora-1` 默认值。`VIDEO_NEWAPI_PROFILE` 默认是 `standard`，由管理员根据实际渠道选择，客户端不会根据域名猜供应商。模型别名的实际供应商、地域、文本／图片模型以及 profile 必须匹配；客户端无法读取管理员渠道映射，设置 profile 也不能证明该供应商已开通。

```bash
python skills/shared/scripts/ai_video.py text2video \
  --provider openai-compatible --prompt '一艘纸船沿河漂流' \
  --duration 6 --ratio 16:9 --audio auto -o outputs/paper-boat/boat.mp4

python skills/shared/scripts/ai_video.py image2video \
  --provider openai-compatible --image ./first-frame.png \
  --prompt '镜头缓慢推进' --duration 6 --ratio 9:16 --audio auto \
  -o outputs/paper-boat/first-frame.mp4
```

取消设置 `VIDEO_API_MODE`，或设为 `custom` / `legacy`，恢复原有通用接口流程。设置未知 mode 会在提交前失败。其他 provider 的专用分支继续使用原有配置，`VIDEO_API_MODE` 不改变它们的行为。现有 `check` 子命令仍是旧有必填变量检查；新模式的模型、profile、时长、图片和音频参数会在生成调用发出任何 POST 之前校验。

## 本轮支持的 profile

所有 profile 的 `seconds` 都是 JSON **字符串**，默认横屏；`size` 使用已核对的像素尺寸。比例不能直接放进 `size`。下表仅是已确认映射的保守子集，不是所有供应商能力的完整声明。

| `VIDEO_NEWAPI_PROFILE` | 时长（秒；粗体为默认） | 16:9 / 9:16 / 1:1 的 `size` | 图片输入 | 渠道约束 |
| --- | --- | --- | --- | --- |
| `standard` | **4**、8、12 | `1280x720` / `720x1280` / 不支持 | 本地 PNG、JPEG、WebP，经 multipart `input_reference` | 仅适用于仍实际提供该兼容协议及参数的已配置渠道；不代表官方 Sora 仍可用 |
| `veo` | **4**、6、8 | `1280x720` / `720x1280` / 不支持 | 本地 PNG、JPEG、WebP，经 multipart `input_reference`；URL 在提交前拒绝 | Google／Vertex Veo factory 的相应视频模型；该 factory 将文件转换成 inline image，不会替客户端下载图片 URL |
| `kling` | **5**、10 | `1280x720` / `720x1280` / `1024x1024` | 本地文件或 HTTP(S) URL；URL 为 `input_reference` | Kling factory；实际模型须支持所选时长、比例及图生能力 |
| `wan26-t2v` | **5**、10、15 | `1280x720` / `720x1280` / `960x960` | 不支持，提交前拒绝 | Alibaba factory 中 `wan2.6-t2v` / `wan2.6-t2v-us` 文生视频的 720P 子集；不能指向 i2v 模型 |
| `seedance1` | 2–12，默认 **5** | `1280x720` / `720x1280` / `960x960` | HTTP(S) URL；本地文件在提交前拒绝 | Doubao Seedance 1.0 对应模型；t2v／i2v 模型须与输入相符 |

`seedance1` 必须额外发送 `metadata.ratio` 和 `metadata.resolution="720p"`；尺寸本身不足以让该 factory 把比例传到供应商。参考图放在 `metadata.content` 的 `image_url` 元素中。Veo 和 Kling 的本地图片作为 multipart 文件发送，不把大文件塞进 JSON。图片最大 10 MiB，prompt 的 UTF-8 最大 128 KiB。

本轮所有 profile 仅允许 `--audio auto`，保留渠道自身的音频默认行为。显式 `on` / `off`、其他分辨率、未列比例／时长、Veo URL 参考图、Wan 图生、多参考图、首尾帧、编辑、延长，以及 Hailuo、Vidu、Jimeng、xAI 等尚未单独核对的模型组合，没有对应的完整客户端 profile；不得用一个近似 profile 冒充已覆盖，需后续增加针对该组合的契约测试。`standard` 不是任意模型参数的万能映射。

## 提交、查询与下载合同

以配置的 `https://host/prefix/v1` 为例，客户端保持 `/prefix`：

1. 单次 `POST /prefix/v1/videos`，提交 model、prompt、字符串 seconds、像素 size 以及 profile 要求的 metadata 或文件。
2. 只接受返回的公共 `id`，查询 `GET /prefix/v1/videos/{public_id}`。不使用供应商私有 task ID，也不允许轮询中更换 public ID。
3. `status=completed` 后优先 `GET /prefix/v1/videos/{public_id}/content`，使用同一用户业务令牌。完成 JSON 没有 URL 是正常情况。
4. 仅在 content 路由返回 404／405／501，且完成对象根层显式提供 `video_url` 或 `url` 时，才尝试该直链。直链不附带业务令牌，不递归搜索输入参考图、文档链接或其他嵌套 URL。

如果 `VIDEO_BASE_URL` 没有尾部 `/v1`，客户端会在原路径后追加 `/v1`，例如 `/team/ai` → `/team/ai/v1/videos`；已含 `/v1` 不会重复追加。base 禁止查询参数、fragment 和 URL 用户凭据；业务令牌只放在请求头中。

POST／状态查询均禁用自动重定向。下载仅手动处理至多 3 次 GET 重定向，第一次跳转后永久去掉业务令牌；不同端口也属于不同 origin。拒绝 HTTPS 降级、非 HTTP(S) 地址、URL 内嵌凭据和无界跳转。不会把网关令牌转发给 CDN 或从 CDN 再跳回的地址。

下载必须得到完整 HTTP 200、非空且 MIME 为视频或 octet-stream 的内容，并检查 MP4/MOV、WebM 或 AVI 文件头。有 `Content-Length` 时必须是合法有界正整数，且实际字节数一致；不能把截断响应、空响应或 JSON／HTML 错误当作成功。上限为 512 MiB，先写同目录临时文件，成功后才原子替换目标；失败会删除临时文件并保留之前的输出。文件头与长度校验不等于完整解码或可播放验收。

`--timeout` 是提交、轮询及下载共享的预算，范围 `(0, 86400]` 秒；`--poll-interval` 范围 `(0, 3600]` 秒。客户端接收 queued／pending／in_progress／processing／running 等待状态，只在 completed 后下载。失败、取消、未知状态、JSON／HTTP 错误或超时直接退出，显示已取得的 public ID；不会切换另一家 provider，也不会重发可能收费的 POST。提交连接中断且未取得 ID 时，结果未知，应先在控制台查账／查任务，再决定是否另行提交。错误日志不回显供应商原始响应体或业务令牌。

## 验证记录与源码依据

从仓库根目录执行：

```bash
python3 -m pytest -q tests/test_newapi_video.py tests/test_ai_video_env.py
```

其中 loopback 测试会临时绑定本机 `127.0.0.1` 随机端口，验证真实 urllib 在两个不同 origin 间跳转时的令牌边界，以及 Content-Length 短读失败保护；受限沙盒需允许本机绑定。测试不需要供应商密钥，不请求外部模型，不修改运行中的 Easel 服务。

核对基线为 New API `v1.0.0-rc.45`（commit `6cf0d90b5ff27f901697dddd61e0c28aba8efefc`）：

- [TaskSubmitReq 的 seconds:string、size:string 与 metadata](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.45/relay/common/relay_info.go#L1038)。
- [宿主覆盖供应商 id/status 为公共任务生命周期](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.45/relay/channel/task/jsplugin/adaptor.go#L952)。
- [Sora／标准兼容协议 JSON 与 multipart 解码](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.45/plugins/tasks/sora/plugin.js#L260)，仅作为格式依据。
- [Google Veo 图片与尺寸转换](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.45/plugins/tasks/google/plugin.js#L139)、[openai_video 解码](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.45/plugins/tasks/google/plugin.js#L411)。
- [Kling 比例映射与时长](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.45/plugins/tasks/kling/plugin.js#L124)。
- [Alibaba Wan 模型能力表](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.45/plugins/tasks/alibaba/plugin.js#L3)、[size 转换](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.45/plugins/tasks/alibaba/plugin.js#L740)。
- [Doubao metadata 转发与参考图](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.45/plugins/tasks/doubao/plugin.js#L792)。
- [宿主视频 content 代理与任务归属检查](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.45/controller/video_proxy.go)。
