# Easel 私有图片变体客户端

`skills/shared/scripts/ai_image.py` 原有 `variations` 命令已经支持 multipart 图片输入。只有显式设置 `IMAGE_API_MODE=new-api`，该命令才改走本仓库 `easel-image-variations/1.0.0` 插件；未设置、空值或 `custom` 保留原先 OpenAI 兼容/Agnes/apimart 的变体流程。

此接入仅修正私有变体入口。`text2img`、`img2img` 仍使用原有 `IMG_BASE_URL`、`IMG_MODEL`、`IMG_API_KEY` 配置和行为，不会因这个开关自动改接口。

## 能力与配置

宿主 New API rc.45 的核心 `/v1/images/variations` 已占用并返回 501，插件不能覆盖它。客户端现在使用独立入口：

```text
POST <NEW_API_BASE_URL>/easel/openai/v1/images/variations
```

```dotenv
IMAGE_API_MODE=new-api
NEW_API_BASE_URL=https://api.drawbookai.cn
NEW_API_KEY=在私密配置中填写网关模型调用key
```

base 是整个网关路由的根地址，而非 OpenAI `/v1` 根地址。若网关整体挂在 `/custom/prefix`，把该前缀保留在 base 中；客户端只去掉末尾斜线，不会删除前缀、自动添加 `/v1`、按域名猜测 provider 或复用 `IMG_*` / `OPENAI_*` 供应商 key。要求 HTTPS，回环地址可使用 HTTP 供本地开发。

客户端固定提交模型 `easel-image-variations`，由管理员渠道映射到**已经核实提供旧 variations 协议**的供应商模型。原来的 `IMG_MODEL` 不作为该私有入口的模型。key 是网关模型调用 key，不是控制台管理 PAT。

**OpenAI 官方旧 variations 服务已经退役。** 这不是让 GPT Image 增加旧 variations 能力，也不是自动改用 images/edits。该插件 README 记录了官方退役依据；只安装插件或把渠道指向 OpenAI 不能证明可调用。现代图像编辑应明确选择单独的 images/edits 工作流。依据见 [OpenAI 退役说明](https://developers.openai.com/api/docs/deprecations) 和 [旧 variations 接口说明](https://developers.openai.com/api/reference/resources/images/methods/create_variation)。

## 命令和请求

```bash
rtk proxy python3 skills/shared/scripts/ai_image.py check
rtk proxy python3 skills/shared/scripts/ai_image.py variations \
  --image reference.png --n 1 --size 1024x1024 \
  -o outputs/image-variations/result.png
```

新模式的 `check` 只离线检查上述两个网关变量，不打印 key 或联网；同时提示普通生成/编辑仍使用 `IMG_*`。客户端没有新增不存在的 variations 命令，也不改变其他图像能力。

请求为一次 multipart POST：

| 字段 | 行为 |
| --- | --- |
| `model` | 固定 `easel-image-variations` |
| `image` | 一张本地 PNG，1 字节到 4 MiB，安全文件名，检查 PNG 签名/IHDR |
| `n` | 1..10 |
| `size` | `256x256`、`512x512` 或 `1024x1024` |
| `response_format` | 固定 `url`，避免较大 base64 超出宿主 JSON 限制 |

`--prompt`、`--mode async` 会在发请求前明确拒绝，不会静默改成生成/编辑。`--resolution` 属于原异步生成流程，不是该插件的请求字段；私有入口只使用方图 `--size`。`--timeout` 在该分支是每次请求的有界等待，允许 1..600 秒。其他 CLI 参数与 PNG/尺寸限制不能替代供应商自己的模型校验。

## 成功结果与下载

当前私有插件立即完成，原生返回 `data:[{url|b64_json}]`。它**没有声明 listArtifacts/content hooks**，客户端不会伪造 artifact CDN、构造任务轮询或把未完成的任务 ID 当作图片。服务端只返回 `task_id/status` 而无 `data` 时会失败，不再次提交。

客户端同时处理实际合同中的直接 base64 和 URL 结果。base64 严格解码；URL 原样读取，支持带签名的 HTTPS CDN 地址。如果返回的 URL 本身就是同一网关上可用的公共 artifact 内容地址，也原样保留，不猜测或重写 artifact 路径。

每次网络请求都拒绝 HTTP 重定向：3xx 直接失败，不向 Location 转发 key，也不重放 POST。只有与 `NEW_API_BASE_URL` 同 origin 的内容请求才携带 Bearer key；外部 CDN URL 和其他端口均不携带网关 key。不会在业务错误、501、网络失败或解析失败后回退到核心 `/images/variations`、`/images/edits` 或 `/images/generations`。

HTTP 必须为 200；响应有 Content-Length 时要求与实际字节长度一致，阻止短读被当作完整图片。单次响应上限 16 MiB，识别 PNG/JPEG/WebP 签名，拒绝 JSON 错误体和不合法图片。全部结果验证/下载完成后才原子写文件；结果无效时保留已有输出。输出后缀按实际图片签名调整，`--format` 不代表服务器转码。文件数量按实际返回的 `data` 数量保存，客户端不把请求 `n` 当作实耗声明。

插件现有宿主提交 JSON 响应上限约 1 MiB，因此请求优先 `url`；客户端能解析某个 base64 结果，并不代表宿主能承载任意大 base64。

## 验证边界

```bash
rtk proxy python3 -W error -m pytest \
  -o asyncio_default_fixture_loop_scope=function \
  tests/test_newapi_image.py tests/test_agnes_image.py -q
```

测试涵盖私有路径、multipart 模型/字段、自定义前缀、直接 base64/CDN/明确公共 artifact URL、同 origin 凭据边界、错误不回退、短读/非 200、输出保留、旧 custom 行为及原 Agnes 图像流程。真实回环双 origin 回归确认提交端只收到一次私有 POST，重定向目标收到零请求和零凭据；运行环境若禁止绑定本机端口，需要在获准环境执行这部分测试。

这些是本地协议与传输安全证据，没有调用真实图片供应商。生产已安装插件、客户端测试通过、实际供应商兼容、渠道价格额度已配置及真实图像生成成功是独立验收项，不能互相替代。

插件合同见 [版本 README](../../integrations/newapi/plugins/easel-image-variations/1.0.0/README.md) 和 [插件源码](../../integrations/newapi/plugins/easel-image-variations/1.0.0/plugin.js)。
