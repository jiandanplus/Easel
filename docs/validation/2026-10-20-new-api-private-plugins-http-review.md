# New API 私有插件：独立审查与本地 HTTP 验收

日期：2026-10-20。审查对象为当前 `codex/ui-redesign` 工作区中的四个私有插件，以及 `voice_clone.py` 本轮 Gemini diff 和 `tests/test_gemini_tts.py`。未合并、未提交、未连接生产服务器，也未发送真实供应商请求。

本报告把源码审查、真实宿主的本地 HTTP/SQLite 验证、真实供应商验收分开。**已完成的是固定 rc.45 源码宿主配合假上游的 HTTP 验证；未证明任何真实账号的模型、费用或音频可播放。**

## 验证环境与可复现材料

- 宿主源码：`/private/tmp/new-api-v1.0.0-rc.45`，目标合同为 New API `v1.0.0-rc.45` Task Plugin API v1。
- 工具链：Go `1.26.0 darwin/arm64`；根模块构建得到 `/private/tmp/new-api-rc45-local-host`。下载包中的 `VERSION` 为空，运行日志显示 `v0.0.0`；版本依据是固定的 rc.45 源码目录，不能用运行日志中的版本字符串反推发布版本。
- 构建仅在该临时源码的 `web/dist/index.html` 添加了占位静态页，用于满足根模块 embed；未构建或验收 New API Web 控制台。
- 使用独立临时 SQLite、独立工作目录、随机测试端口、假渠道 key、假音色 ID 和保留测试域名。所有测试宿主和假上游进程均在脚本 `finally` 中停止并等待退出。
- HTTP 闭环脚本：`/private/tmp/easel-newapi-http-review.py`。
- 普通账号任务列表核查脚本：`/private/tmp/easel-newapi-taskview-review.py`。
- 最新闭环凭据目录：`/private/tmp/easel-newapi-http-review-1792486517`，含 `results.json`、`task-summary.json`、`db-snapshot.json`、`host.log`、测试数据库。
- 首轮任务列表核查证据：`/private/tmp/easel-newapi-http-review-1792486302/taskview-proof.json`。

以上 `/private/tmp` 文件是本机复现辅助材料，可能被系统清理。`results.json` 中登录 access token 和 artifact 下载授权参数已脱敏。数据库和渠道均为本次新建的假数据，不含生产密钥；不要把测试数据库迁入生产。

构建和复跑命令：

```sh
cd /private/tmp/new-api-v1.0.0-rc.45
/Users/jiandan/.cargo/bin/rtk proxy env GOCACHE=/private/tmp/easel-review-go-cache go build -o /private/tmp/new-api-rc45-local-host .
/Users/jiandan/.cargo/bin/rtk proxy python3 /private/tmp/easel-newapi-http-review.py

cd /Users/jiandan/Code/GitCode/Easel
/Users/jiandan/.cargo/bin/rtk proxy python3 -m pytest -q tests/test_gemini_tts.py
```

最后一次完整 HTTP 验证实际安装的插件 source SHA-256：

| 插件 | SHA-256 |
| --- | --- |
| `easel-dashscope-music@1.0.0` | `65b2d8434d171837c8d4bb1f63d4583d4f155c1c8d175894c7593bf3344ac241` |
| `easel-dashscope-voice@1.0.0` | `0a33cc6da2590fb7b5fa4318ac5d09970f7230694d46e5badf9576ac7135b3a7` |

若文件后来修改，应重新比对 hash 并复跑，不能把此报告自动套用到新字节上。

## 真实宿主 HTTP 结果

宿主通过管理 API 初始化、安装插件并创建专用 Task Plugin 渠道；创建 Alice、Bob 两个普通用户，各自生成业务 token。假上游始终由本机测试 HTTP 服务应答。查询与 origin 拒绝均通过真实认证、中间件、渠道选择、数据库与 driver 执行链。

| 检查 | HTTP 结果 | 上游行为/证据 |
| --- | --- | --- |
| Fun-Music 同步提交 | `200`、`SUCCEEDED` | 一次 POST；保存 `SUCCESS`、供应商 `duration=123` |
| Alice 查询自己的音乐任务 | `200` | 未新增上游请求 |
| Bob 查询 Alice 音乐任务 | `404 task_not_found` | 未新增上游请求 |
| 音乐 artifact 列表 | `200` | `audio`、`audio/mpeg`；未请求 CDN 内容 |
| CosyVoice 登记 | `200`、`SUCCEEDED`、`UNVERIFIED` | 一次 `create_voice` POST；公开响应不含供应商 voice ID |
| Bob 引用 Alice 登记任务进行审核查询 | `400`、origin 不存在或不属于当前用户 | 在上游请求前拒绝 |
| Alice 审核查询 | `200`、`voice_status=OK` | 一次 `query_voice` POST |
| Alice 引用审核通过任务合成 | `200`、`SUCCEEDED` | 一次合成 POST；`characters=6` |
| 创建更高优先级备用声音渠道后继续原链 | verify/synthesize 均成功 | 仍使用原渠道 `id=2`；实际收到的假凭据标签也属于原渠道 |

artifact 检查只证明列表投影成功。测试环境没有设置真实 `ServerAddress`，因此其下载 URL 使用宿主默认 `http://localhost:3000`；没有验证 CDN 下载、重定向、Range、下载 URL 的部署域名或音频播放。

## 用量、预留与实际结算

测试价格是故意设置的假价格：每秒/字符/次 `0.01` 个表达式货币单位，宿主 `QuotaPerUnit=500000`。它们只用于核对算术，不是供应商报价或建议零售价。

`extractUsage=null` **没有被宿主自动解释成零消费**。直接配置 `tier("music", u("duration_seconds") * 0.01)` 时，原生提交在预估阶段得到 HTTP `400`：`invalid operation: <nil> * float64`，假上游 POST 次数保持为零。

为了完成闭环，测试明确给未知字段设置预留 fallback：

```text
音乐：tier("music", (u("duration_seconds") ?? 100) * 0.01)
登记/审核：tier("voice", (u("count") ?? 1) * 0.01)
合成：tier("voice", (u("characters") ?? 100) * 0.01)
```

这里的 `100` 是测试预留值，不是供应商提供的时长上限，也不是实际用量。完成阶段读取到的真实假上游事实覆盖预留后，宿主结果为：

| 操作 | 请求时预留 quota | 最终事实 | 最终 quota |
| --- | ---: | --- | ---: |
| 音乐 | 500000 | `duration_seconds=123` | 615000 |
| 声音登记 | 5000 | `count=1, operation=create_voice` | 5000 |
| 审核查询 | 5000 | `count=1, operation=query_voice` | 5000 |
| 语音合成 | 500000 | `characters=6` | 30000 |

Alice 初始 quota `10000000`，结束为 `9345000`，`used_quota=655000`、`request_count=4`，与四笔成功任务合计 `655000` 一致。Bob 保持 `10000000`、`used_quota=0`。失败请求未被当作成功实耗计入余额。

这证明 **配置了显式预留规则时**，当前宿主可按完成事实结算。它没有证明音乐生成的预留额度在真实供应商上总是足够；此次核对的 Fun-Music 请求没有已知的强制时长上限。上线仍应明确准入预算、并发与异常结果的对账策略。

若希望消除 `null` 对管理员表达式的隐含要求，可采用下列尚未在本报告中实施的最小合同：音乐 `usageSchema` 增加含义明确的请求 `count`，请求 hook 只返回 `{count: 1}`，完成 hook 保持供应商 `duration_seconds`；表达式按是否已有完成时长选择实际价格或请求预留预算，例如：

```text
tier("music", u("duration_seconds") != nil ? u("duration_seconds") * 0.01 : u("count") * 1.00)
```

`count=1` 仅表示发起一次请求，不冒充实际音乐时长。CosyVoice 的 create/query 本来就是明确的一次操作，可预估 `{count:1, operation:...}`；合成也可明确标注请求字符数为预留估算，最终仍以供应商 `usage.characters` 覆盖。这些更改需要补用量 schema、fixture 和 HTTP 结算复跑，不能仅改文案。

## 已发送 POST 后的不确定结果与重试

假上游返回 HTTP `200`、可用音乐结果，但故意省略 `usage`。插件拒绝成功，宿主返回 `502`。这部分行为通过真实 HTTP 复现：

| 设置 | 单个客户端 POST 实际触发的上游 POST 次数 | 任务/余额结果 |
| --- | ---: | --- |
| 默认 `RetryTimes=0` | 1 | 无成功任务；宿主释放该次预留 |
| 测试明确改成 `RetryTimes=2` | 3 | 最终仍 `502`；没有可供客户端对账的成功任务 |

因此，**此类同步收费私有插件在 `RetryTimes>0` 时存在已证实的重复提交风险，应阻止该配置上线。** rc.45 默认值为 `0`，不能描述为默认必然重放。origin task 固定原渠道使用 `PinRetrySameChannel`，只能限制重试的渠道，不能阻止同渠道重复 POST。只有一个渠道也不等于只尝试一次。

证据源码路径：

- `relay/channel/task/jsplugin/adaptor.go`：`parseSubmitResponse` hook 错误包装为 `502`；普通 JSON 响应路径没有为此类 hook 错误设置 `NoRetry`。
- `controller/relay.go`：`decideTaskRetry` 在预算尚有剩余时重试 5xx；durable task barrier 前失败时释放预留。
- `middleware/task_plugin.go`：origin 引用使用 `PinRetrySameChannel`。
- `common/constants.go`：`RetryTimes` 默认 `0`。

单独保持 `RetryTimes=0` 能规避宿主自动重放，不能把“上游未返回可靠结果”变成“供应商没有消费”。现有插件与宿主没有为这种同步未知结果提供持久化的人工对账状态。生产接入前还需定义中央账本 pending/对账处理，不应直接沿用宿主退款作为供应商未扣费的证据。

不建议用伪造 `SUCCESS`、`FAILURE` 或 `immediate UNKNOWN` 掩盖该缺口。官方即时完成合同只承诺 `SUCCESS|FAILURE`；普通非终态任务会进入轮询，hook 连续失败/UNKNOWN 达默认 20 次后成为失败并进入退款链，另有 24 小时超时清扫。供应商没有真实查询入口时，返回一个本地假 task ID 并不能建立可恢复的上游状态机。

## 公开 ID 核查及一次审查判断更正

曾根据 `TaskModel2Dto` 的 `Data` 赋值推测普通任务列表可能暴露 Fun-Music 数据中的供应商 ID。真实 HTTP 检查否定了这个推测：`GET /api/task/self` 的音乐 `data=null`、`channel_id=0`、`root_info=null`，未出现假供应商音频 ID 或请求 ID。继续追到 `model/task.go` 的列表查询使用 `Omit("data")`。**该推测不作为安全发现，也不计 P2。**

主任务随后按最小数据原则移除了音乐 taskData 中无用的 `audio.id`、`request_id`，供应商 ID 仍交给宿主私有 upstream 字段。新源已经重新安装并跑完上述 HTTP 结算和 artifact 列表验证。该项是数据收紧，不声称修复了一个已证实的公开泄露漏洞。

## 其他审查范围与结论边界

| 范围 | 本次独立验证 | 仍未证明 |
| --- | --- | --- |
| DashScope music/voice | 完整源码审查；真实本地宿主 HTTP、SQLite、计量结算、普通用户隔离、origin 渠道绑定 | 真实供应商开通、音色审核、可播放音频、CDN、生产环境 |
| MiniMax 私有声音 | 源码与 rc.45 driver/origin/multipart/整数精度/即时计量合同审查 | 本次未跑完整 HTTP MiniMax 链；大 int64 数字在宿主预先转 float64 的缺口仍受 README 所述限制 |
| 私有 variations | 源码与宿主 multipart、同步完成和用量合同审查 | 本次未跑真实供应商；必须显式配置仍提供 legacy variations 合同的兼容服务，不把退役官方接口当作可用模型 |
| Gemini 客户端 | 读取完整本轮 diff 和测试；独立运行 `tests/test_gemini_tts.py` | 真实 Gemini/New API 模型请求、音频播放、真实费率 |

Gemini 独立复跑结果：`20 passed in 0.04s`。没有发现另一个可复现的 Gemini 客户端回归。

在上述四插件/Gemini 审查范围内，没有额外已证实的 P1/P2 代码发现。需要保留的上线条件是：为未知请求用量配置明确预留、私有同步任务保持 `RetryTimes=0`、处理已执行但结果不确定的对账状态、分别完成真实供应商验收。仅注册插件且没有启用渠道时，不构成已完成供应商接入或可安全收费调用的证据。

## 追加审查：Fish 桥与显式网关客户端

本节晚于上述 HTTP 闭环：追加审查 `fish_bridge/`、`easel-fish-tts`、`newapi_voice.py` 和 `ai_music.py` 新 gateway 分支。没有重复执行主任务已报告的 Fish ASGI/Node/CLI 测试，也没有将它们当作本审查独立完成的完整 HTTP 验收。

Fish 源码审查确认：SQLite pending 在 Fish POST 前 commit；`task_id` 有跨 owner 唯一索引；同任务换 secret 或输入在 POST 前拒绝；固定供应商 URL、`follow_redirects=False`、`trust_env=False`；TTL 只清音频并保留 expired 墓碑；未知消费保持 pending；文件采用私有权限和 opaque ID；独占目录锁阻止同目录多进程；Compose 模板无发布端口。JS 的 per-task 轮询只 GET 原 receipt。未发现该范围另一个可复现的 P1/P2。桥尚未部署，不能据此认定容器网络、真实供应商和 New API→桥→Fish 链路已经验收。

追加审查向主任务提交了两个客户端问题，修复验收状态如下：

1. **已解决的 P1：音乐 gateway transport 跨 origin 重定向带出 token。** `http_post(..., redact_errors=True)` 当时仍使用 urllib 默认 opener。用两个自建回环端点复现：源返回 `302 Location` 指向另一个端口，目标收到转换后的 `GET` 且保留完整假 `Authorization`。主任务随后让 new-api GET/POST 都使用 `_NewAPINoRedirect` opener，legacy 保持原合同。本审查读取新源，并用原双 origin 脚本独立复跑：客户端退出 `1`，固定报错为 HTTP `302`，目标 `foreign_origin_received=[]`，没有转发请求或凭据。复现脚本 `/private/tmp/easel-newapi-redirect-review.py`；原泄漏证据 `/private/tmp/easel-newapi-redirect-proof.json` 和修复后证据 `/private/tmp/easel-newapi-redirect-fixed-proof.json` 分别保留。脚本均已关闭两个自建服务器，未使用真实 key。
2. **已解决的 P2：语音登记的中间公共 ID 未向 CLI 使用者交付。** 新语音客户端在 MiniMax 上传完成后继续登记，在 DashScope 登记完成后循环审核；中间 `file_task_id`/创建 `voice_task_id` 当时没有打印或持久化。后半阶段失败时用户得不到已完成前半阶段的引用。主任务已在下一步之前向 stderr 输出带阶段标签的公共引用，失败提示保留恢复命令，最终 `voice_id=` 仍只输出已完成的可用结果；增加显式 new-api `enroll --resume-id`。本审查读取恢复实现并独立运行新增恢复相关用例：`python3 -m pytest -q tests/test_newapi_voice.py -k 'resume or recovery or verify_error'`，结果 `7 passed, 24 deselected in 0.12s`。MiniMax 恢复先 GET 并校验上传阶段，仅继续登记；DashScope 恢复先 GET 并校验创建阶段/target_model，仅继续审核；custom 与其他 provider 在旧接口请求前拒绝 resume。该选项跳过已完成的前半阶段，不代表能恢复供应商结果未知的同步收费 POST；此类结果仍须先对账，再决定是否明确新建后半阶段操作。

语音新客户端本身已经使用 `NoRedirect`；其下载路径按 origin 决定是否附加网关 token，Fish 下载还校验固定 artifact 路径、MIME、大小和 TTL。此次没有发现另一个经验证的凭据/公共资源隔离问题。

追加审查完成后，两个客户端发现均已关闭，没有剩余已证实 P1/P2。生产渠道、真实供应商、桥部署和未知消费对账的验收边界保持前述限制；生产升级或注册了插件本身不能替代这些验收。

## 追加审查：显式 New API 视频模式

继续只读审查 `ai_video.py` 的 `VIDEO_API_MODE=new-api` 分支与 `tests/test_newapi_video.py`，对照 rc.45 `openai_video` 的 create/retrieve/content 合同，重点核对公共 ID、请求字段、token 边界、无 URL 完成响应和旧 custom 路径。

在下载完整性检查发现一个独立可复现的 P2，现已修复并独立验收：原客户端通过有界分块 `response.read(amt)` 下载内容，却未核对 `Content-Length`。本地假 HTTP 端点返回 `200 video/mp4`、声明 `Content-Length: 100`，仅发送 20 字节后关闭连接。Python 在这条分块读取路径没有抛 `IncompleteRead`，旧代码把 20 字节原子替换为最终文件并返回成功。

原始证据：`/private/tmp/easel-newapi-video-short-read-proof.json`，结果为 `client_returned_success=true, content_length=100, received_bytes=20`。复现脚本：`/private/tmp/easel-newapi-video-short-read-review.py`；只使用假业务 token 和一个自建回环端点，脚本结束时已经关闭服务器和临时输出目录。新代码解析并限制合法 Content-Length，结束时核对收到字节数；只接受完整 HTTP 200，失败删除临时文件并保留既有输出。原脚本独立复跑结果为 `client_returned_success=false, client_exit=1, existing_output_unchanged=true`，修复后证据单独保留于 `/private/tmp/easel-newapi-video-short-read-fixed-proof.json`。此检查证明传输完整性，不代替真实视频解码或播放验收。

另用真实本地假网关和独立假 CDN 验证正向链路，脚本为 `/private/tmp/easel-newapi-video-client-http-review.py`，收据为 `/private/tmp/easel-newapi-video-client-http-proof.json`：

- 请求保留 `/team/v1` 前缀，仅一次 create POST；Kling 显式 profile 发送 `seconds="5"`、`size="1024x1024"`。
- create 为 queued，随后 GET 返回 completed 且没有 URL；客户端保留同一个公共 task ID，访问其 `/content`。
- 网关 submit/retrieve/content 首跳均带假业务 token；content 302 跳往另一个 origin 后，CDN 没有收到 Authorization。
- 正确的 31 字节测试内容保存为 31 字节，新增 Content-Length 防护后重新复跑仍成功。

源码对照确认 legacy/custom 分支保留原字段和 URL 流程；新模式的 POST/状态查询不自动重定向，content 手动跳转有上限且禁止 HTTPS 降级，fallback 仅在明确的 content 404/405/501 后使用根层显式视频 URL 并去掉 token。Veo profile 明确只允许本地 multipart，避免 URL 图被 factory 忽略。指南中的两个输出示例也已修正为项目允许的 `outputs/paper-boat/` 目录。

视频追加审查完成后没有剩余已证实 P1/P2。这里的假视频字节、profile 合同和本地 HTTP 结果不证明真实视频服务、解码播放、质量或供应商结算已经验收。

## 追加审查：私有 variations 的最小客户端接入

最后只读审查 `ai_image.py` 的 `IMAGE_API_MODE=new-api` variations 分支、`tests/test_newapi_image.py` 和独立图片指南，未扩大到其他图像流程。实际私有插件 `native.result` 返回同步 `{data:[{url|b64_json}], created?}`；客户端没有把它解释为 task envelope，也没有虚构 artifact hooks 或轮询。

本审查使用 Node 直接加载当前 `easel-image-variations/1.0.0/plugin.js`，调用真实 `native.result` 生成含测试 PNG 的响应，再由自建本地假网关应答客户端。独立 HTTP 脚本 `/private/tmp/easel-newapi-image-client-http-review.py` 和证据 `/private/tmp/easel-newapi-image-client-http-proof.json` 确认：

- 正确发送且只发送一次 multipart POST 到 `/tenant/prefix/easel/openai/v1/images/variations`，保留前缀；没有使用被核心占用的 `/v1/images/variations`。
- 请求固定模型 `easel-image-variations`、`response_format=url`，包含原测试 PNG；没有复用供应商别名 key。
- 实际 renderer 输出字段为 `data, created`；helper 返回单元素 `list[Path]`，保存字节与测试 PNG 完全一致。

另独立运行五项新模式针对性回归：

```text
python3 -m pytest -q tests/test_newapi_image.py \
  -k 'private_multipart or custom_keep or different_port or short_content or actual_submit_redirect'
5 passed, 10 deselected in 0.08s
```

覆盖私有 multipart 合同、custom 原路径/模型、不同端口不携带业务 token、非 200/长度不符保留旧输出，以及真实双 origin 302 后目标零请求。源码还确认所有新模式网络请求都禁用自动重定向，业务错误/解析错误/传输中断不会重发 POST 或回退核心接口，明确的外部 CDN 不带网关 key，同 origin 内容才使用业务鉴权；已有 `text2img/img2img` 分支没有路由改动。实现者已补 `http.client.IncompleteRead` 固定报错，未修改旧 HTTP helper。

新指南明确 OpenAI 官方旧 variations 接口已退役，私有入口只适用于管理员已核实仍支持该 legacy 合同的供应商，不会恢复旧官方服务或将 variations 暗中改为 edits。

图片追加审查没有发现剩余已证实 P1/P2。上述结果是实际插件 renderer + 假网关的本地协议证据；没有调用真实图像供应商，也没有完成生产渠道或费用验收。独立脚本与测试均已关闭自己创建的 HTTP 服务器。
