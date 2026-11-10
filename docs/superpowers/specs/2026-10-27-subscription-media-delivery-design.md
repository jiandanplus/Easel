# 订阅媒体统一交付合同与实施设计

日期：2026-10-27。分支：`codex/ui-redesign`。

状态：根据“完成剩余部分开发”的授权拆分，已完成当前代码只读核对，交根任务审阅接口后实施。本文件不是媒体供应商已开放、已经计费或已经生成真实产物的声明。正式支付目前按用户要求暂不配置。

## 1. 交付目标和强制边界

订阅用户使用已发布能力时，无需填写供应商地址、供应商 Key 或 New API 业务令牌。Easel 只持有产品会话；中央是会员、共享点数和最终账本的唯一权威。自定义接口继续保留，原有供应商配置不上传、覆盖或回退使用。

第一批先实现同步图片和可持久恢复的异步媒体共同合同，随后依同一合同完成视频、音乐、转写、配音、音色登记与声音合成。完整媒体范围不因图片和视频两个范式通过而缩小。

生产现有目录只开放两款 DeepSeek 文本模型。媒体能力默认 `closed`；只有不可变定义、服务器私有适配器绑定、明确费率、会员授权、产物及计量真实验收都具备时才能 `enabled`。不为缺少的媒体配置填默认模型、商业价格或假 usage。

前端只展示功能名称、模型、必要状态、文件和操作；不展示开发说明、架构解释、适配进度说明段落。未开放能力不出现在可执行模型菜单。

## 2. 当前代码事实与复用边界

| 已有代码 | 可复用内容 | 必须补齐的差距 |
| --- | --- | --- |
| `services/platform/src/platform_service/execution/core.py` | 完整 Scope、模型/会员校验、任务凭据、一次派发、冻结绑定、可信终态先持久化 | `validate_definition` 明确只允许 `capability=text`；任务和结果表有文本模型 FK、token 计量和 JSON/SSE 类型限制，不能把图片数伪装成 tokens |
| `execution/rates.py` | 有理数费率，合计只向上取整一次 | 只有 input/output tokens，需独立媒体计量单位 |
| `credits.py` | 同钱包的 `authorize` 锁、`reserve_in_transaction`、lot/hold/journal、settle/release、待核算上限 | 媒体作业与 reservation 的一致性约束、媒体可信证据和结算事务还不存在 |
| `execution_evidence.py`、迁移013 | 出站前 attempt、响应关联和不可变终态设计 | 表 FK 指向文本 job，媒体需独立关联表，不向旧证据伪造文本 job |
| `execution_runtime.py` | 有界连接池、禁重定向、固定上游、一次 POST、故障安全错误 | 明确 `media_execution_enabled=false`；现有 recover 只恢复文本财务，不是媒体轮询工作器 |
| `skills/shared/scripts/media_gateway.py` | 自定义/统一网关配置隔离、逐能力环境派生 | `require_media_available` 当前拒绝中央订阅媒体；不能简单删除守卫或把中央任务凭据写进 `NEW_API_KEY` |
| `ai_image.py` | generations/edits/variations 请求结构、格式识别及本机合成协议测试 | CLI 保存文件不等于中央有权确认费用、产物归属和可下载性；有些旧自定义路径自动检测协议，只能保留在自定义模式 |
| `ai_video.py` | New API `/v1/videos` 提交/查询/内容下载、显式 profile、未知提交不重派 | 长任务公共 job、重启续查、中央计量/账本/存储还未接通 |
| `ai_music.py` | DashScope 私有插件和 Suno submit/fetch 合同、轮询/下载结构 | 不同上游的任务 envelope 和单位不同，不能把 CLI 成功退出作为可信计费凭据 |
| `tts.py`、`voice_clone.py`、`newapi_voice.py` | 配音、音色登记/合成、DashScope/MiniMax/Fish 私有桥合同 | `tts.py` 的 Edge 路径是自定义/本地工具；中央不能用免费回退冒充已购买的指定配音服务。音色引用需绑定归属 |
| `asr.py` | 本地 Whisper、字幕序列化和音视频预处理 | 当前不是中央远程 ASR 供应商适配器；不能将本地模型下载/转写称为已接通的订阅 API |
| `easel/harnesses/pi_tools.py`、`runtime/pi/worker.mjs` | 固定工具定义、host 执行、私有路径保护和 shell 禁网络 | Pi 工具环境会清除凭据且 shell 禁网络，不能靠注入网关 Key 让任意 Python 脚本直接联网；需新增受限 host 媒体工具 |

本次核对没有发起供应商、模型、支付或生产写入。

## 3. 能力、定义和费率

`MediaCapability` 是闭合集合：

```text
image.generate       同步文生图
image.edit           带已授权素材的编辑
image.variation      已有变体工作流
video.generate       文/图生视频，异步
music.generate       音乐/BGM，异步或适配器明确的同步
audio.transcribe     音频/视频转写
audio.speech         文本配音
voice.enroll         上传样本登记音色
voice.synthesize     已登记音色或已授权样本合成
```

新增不可变 `MediaDefinition`：

```python
{
  "id": public_model_id, "version": definition_version,
  "name": display_name, "capability": capability, "feature": entitlement_feature,
  "products": product_ids, "test_only": bool,
  "provider_id": provider_id, "provider_version": provider_version,
  "adapter": explicit_adapter_id, "protocol_version": "1",
  "verification": "fixture" | "live", "verification_ref": evidence_reference,
  "rate": {"version": rate_version, "meters": [
    {"unit": unit, "basis": trusted_basis,
     "numerator": integer_string, "denominator": positive_integer_string}
  ]},
  "limits": bounded_capability_limits,
  "parameters": published_parameter_schema
}
```

金额单位沿用 microcredits；数量、时长和字符数均为非负整数。`unit` 支持 `image`、`video_ms`、`audio_ms`、`unicode_scalar`、`registration`，需要 token 计价的媒体适配器另显式发布 `input_token`/`output_token`，不能按名称猜测。费率以精确有理数求和，每个收费操作向上取整一次。

`basis` 明确三种可信来源：

- `provider_usage_v1`：经该适配器验证、精确绑定本次 attempt/response 的供应商计量；原始数值、转换规则和证据摘要留在服务器。
- `verified_delivery_v1`：已发布的固定交付价格，按服务端实际验证的图片/音轨/音色登记数收费；这确定的是用户交付费用，不宣称供应商成本已知。
- `verified_input_v1`：固定按真实输入音频时长或规范化文字量收费；输入必须已由中央校验，不能信任客户端报时长/字符数。

音视频时长通过受限服务端探测/解码取得整数毫秒；供应商十进制秒按定义中的明确舍入规则用 Decimal 转换，不经浮点累积。文字计数固定到实际发送的 NFC/非NFC 规则版本，默认 `unicode_scalar_v1` 不改变原文、不按 UTF-8 字节计数。适配器不能在同一已发布费率下偷换 basis。

固定交付价格缺少商业费率时仍保持 closed。供应商费用未知与用户按已验证交付规则可确定的费用分别记录；没有发布这种定价规则时，不以下载成功推导收费。

## 4. 中央 API 合同

以下新路由与原文本 `/v2/responses` 并行，不改变既有文本合同。

| 路由 | 权限和语义 |
| --- | --- |
| `GET /v2/products/{product_id}/media-models` | 当前产品会话；仅返回已启用、绑定验证和会员允许的目录，未配置返回空目录 |
| `POST /v2/media/task-tokens` | 当前产品会话，body 为 `{model, capability, invocation_key}`；中央重新验证完整 Scope，签发仅本次媒体调用、模型/能力/预算/期限受限的短期凭据 |
| `POST /v2/media/assets` | 媒体短期凭据；流式上传原始 bytes，受内容类型、长度、hash、文件格式/解码限制；返回不透明 `asset_id`。不接收用户提供的服务器文件路径或任意下载 URL |
| `POST /v2/media/jobs` | 媒体短期凭据＋`Idempotency-Key`；body 为 `MediaRequest`；原子准入和预留后返回公共 `job_id`。同步图也建立持久 job，客户端可等待或查询 |
| `GET /v2/media/operations?invocation_id=&operation_key=` | 产品会话，只读查询原操作；不存在/未知都不表示安全重派 |
| `GET /v2/media/jobs/{job_id}` | 产品会话＋当前 Scope 复验；分别返回执行、交付、财务状态和可用产物 |
| `POST /v2/media/jobs/{job_id}/cancel` | 产品会话，幂等停止请求；发送前可释放，发送后仅请求供应商取消或转待核算，不提前返回已退款 |
| `GET /v2/media/artifacts/{artifact_id}` | 产品会话，metadata；返回 MIME、尺寸/时长、长度、SHA256、期限和可下载状态 |
| `GET /v2/media/artifacts/{artifact_id}/content` | 产品会话，归属/撤销/到期复验后流式输出原文件，不再扣生成点数；无公共 blob 路径 |
| `GET /v2/media/voices/{voice_id}` | 产品会话，仅返回有权音色的公开标签/状态，不返回供应商私有 voice ID |

上传走独立流式路径，不把大文件加入现有 `app.py` 的64KiB通用 JSON body 缓冲。JSON仍严格限制。允许的 upstream、轮询和下载 origin 都由私有配置显式固定；不接受浏览器/模型自带 URL 替换这些端点。

请求体最小合同：

```python
MediaRequest = {
  "model": public_model_id,
  "capability": MediaCapability,
  "prompt": optional_text,
  "assets": [opaque_asset_id],
  "voice_id": optional_owned_voice_id,
  "parameters": capability_specific_bounded_object
}
```

模型、能力、参数、输入素材 SHA256、费率版本和实际供应商绑定一起冻结为 body hash。同一 `(invocation_id, operation_key)` 同内容返回原 job，不再 POST；异内容409。客户端不提交可信金额、实际用量、钱包ID、供应商模型或供应商任务ID。

公开作业投影：

```python
{
  "id": job_id, "capability": capability, "model": public_model_id,
  "execution_state": "queued" | "running" | "completed" | "failed" |
                     "cancel_requested" | "cancelled" | "unknown",
  "financial_state": "reserved" | "dispatched" | "pending_reconciliation" |
                     "settled" | "released" | "waived",
  "delivery_state": "processing" | "available" | "unavailable" | "expired",
  "reserved": integer_string, "actual": integer_string_or_null,
  "rate_version": frozen_rate_version,
  "artifacts": [safe_artifact_metadata],
  "failure_code": optional_safe_code
}
```

`waived`只为兼容统一投影，首批不新增通用媒体豁免权限；历史三笔 GLM 的经营批准不扩展到新媒体请求。模型生成失败、结果不可下载、财务未知彼此独立，不把其中一个状态替代另两个。

## 5. 数据与事务

新增迁移 `014_media_execution.sql`，不修改001–013。新增 `media_models`、`media_model_states`、`media_invocations`、`media_task_tokens`、`media_jobs`、`media_provider_attempts`、`media_provider_associations`、`media_terminal_evidence`、`media_receipts`、`media_assets`、`media_artifacts`、`media_voices`。定义、凭据摘要、attempt/关联/终态和结算回执使用不可变触发器；任务只能更新明确状态/lease/时间列。

媒体复用原 `credit_reservations`、holds、lots、journals、entries，不新建第二钱包。`MediaService.admit` 在同一事务按现有 wallet→invocation→job/reservation 锁顺序执行 `authorize`、权益/定义/私有绑定检查、素材归属、原键检查、预算计算、`reserve_in_transaction` 和新 job 写入。文本与媒体共同竞争原 active/pending 限制，不能绕过现有待核算上限3。

新增延迟约束检查媒体 job/reservation 的 Scope、金额、模式、归属、预留/实际与 receipt 一致。原文本 reconciliation 函数对非文本 reservation 不能被改成放过文本不变量；媒体增加独立守卫，所有原回归保持。

`MediaService.finish` 必须先持久化精确 attempt 的可信终态/计量，再在单一账本事务内调用原 ledger 完成内部路径，将 receipt、reservation、lot、journal 与 job 状态同时提交。结算失败后 `recover_financial` 只读已有证据补结算，绝不再发生成 POST。actual 超预算保留原 hold、转待核算，不能负钱包或按客户端金额覆盖。

部署角色按 `permissions.py` 的显式表、列、序列白名单增加媒体最小权限；Console 暂只增加安全只读投影，不获得密钥/原始请求/账本修改能力。测试 runner 的 REQUIRED_MIGRATIONS、打包清单与启动迁移守卫同步更新到014。

## 6. 适配器、工作器与未知结果

新增 `media/adapters/base.py`：

```python
class MediaAdapter:
    async def submit(self, frozen_request, attempt) -> Submission: ...
    async def query(self, association) -> Observation: ...
    async def cancel(self, association) -> Observation: ...
    async def retrieve(self, artifact_descriptor, sink) -> VerifiedArtifact: ...
```

这四个方法由能力/协议明确选择，不能按域名猜协议或自动从新接口回退旧接口。优先实现 `openai_images_v1` 和 `newapi_videos_v1`；随后 `newapi_dashscope_music_v1`、`newapi_suno_v1`、`openai_speech_v1`、`openai_transcriptions_v1`、`newapi_voice_v1` 按已有适配器事实独立验证。不存在的供应商合同保持closed，不编造成功适配器。

出站前先提交 `attempt_id + provider_binding_hash + request_hash`。POST仅一次；响应一到先保存精确关联，再处理任务状态。收到明确 provider_job_id 后，worker 通过有界 lease 和 `SKIP LOCKED` 取得轮询权；重启只恢复查询、下载和结算。未知是否提交且尚无 provider_job_id 时进入待核算，不重新提交。GET超时可按固定退避重查同一任务；POST超时不自动重试。

取消的三种情况：

1. 尚未 dispatch：按原 ledger `release`，原请求和记录保留。
2. 已 dispatch，供应商已确认取消且该适配器有可信零收费证据：记录该终态，按实际零值结算，不能把普通HTTP400或一句“已取消”当零费用证明。
3. 已 dispatch，费用或完成未知：`cancel_requested/unknown + pending_reconciliation`，保留预留和原关联。

首批以轮询作为异步恢复权威；不开放未验签的公网回调。后续需要回调的供应商单独增加签名、nonce/时间窗、inbox去重和精确关联验证，不能先提供一个可以任意写job状态的回调。

## 7. 产物与输入素材

中央私有媒体目录不放在桌面 outputs、通用静态目录或可公开下载目录。目录0700、文件0600；不透明存储键，禁止符号链接。所有输入、输出都有服务端 SHA256、字节数、MIME和实际格式验证，流式写独占临时文件，验证后原子提交。

PNG/JPEG/WebP验证实际图片解码和尺寸；音视频在受限进程中用已安装的固定ffprobe/ffmpeg探测并完成解码检查，限制文件大小、像素、时长、解码时间、CPU/内存。字符串后缀和Content-Type不作为成功产物证据。下载失败与内容已生成/已计费分开，允许恢复下载而非重新生成。

上游给URL时只能命中私有配置明确允许的origin及路径模板；每次解析/重定向都验证，首批禁止重定向。鉴权头只给固定上游origin，CDN下载不携带供应商Authorization。禁止云元数据/非允许私网及任意上传URL，现有跨云New API私有通道以显式受信绑定配置。

输入素材绑定完整Scope和媒体invocation；已经引用的素材被job pin住，清理器不能在执行中移除。声音登记将供应商voice ID只保存在中央，公开voice ID绑定用户/租户/产品授权；不能凭猜到voice ID跨账号克隆。

4核4GB首版媒体派发并发默认1；下载与探测有独立小并发/有界流量，不能挤占文本连接池。具体模型的文件、输出数量和时长上限由定义冻结。磁盘低水位时在预留/dispatch前拒绝新任务；已提交作业保留关联，不因磁盘不足伪装未收费。TTL清理只移除已到期blob，账本与hash/审计保留；资产和备份容量纳入后续运维验收。

## 8. Easel 和 Pi 接入

新增 `easel/platform/media.py` 的 `CentralMediaClient`，仅 host 进程读取现有私有中央会话。负责目录、mint、上传、提交、原键查询、取消、下载及原Scope复验；短期media token也不进入浏览器、Pi worker消息、持久用户环境或自定义 `NEW_API_KEY`。

新增 `easel/platform/media_records.py` 保存 `version=1 + operation_key + invocation_id + job_id? + owner_key + turn_key + capability + model + request_hash + status`；出站前fsync，不保存prompt、二进制素材或凭据。与既有v2文本operation记录分开版本校验，中央查询投影可在后续合并显示，不能让旧严格客户端误读。

Pi新增固定host工具 `create_media`、`get_media_job`、`cancel_media_job`、`download_media_artifact`；工具schema仅含公开模型/能力、已授权workspace文件引用、参数和输出位置。`PiAdapter`从当前目录投影可用工具，host从已经接受的 `RunRequest` 取得Scope，不能采信模型传来的账号、钱包、供应商地址或金额。生成工具调用先建立稳定operation_key；同一工具id重复只查询原job，普通同内容新工具调用仍是新的明确操作，不能用提示词hash去重合法创作。

媒体结果保存到经校验的 `outputs/<project>/...` 新文件，核对中央artifact SHA256；默认不覆盖原稿。产物引用进入原会话/轮次，继续复用本轮已实现的成品复制、下载及继续修改入口。取消本机Pi任务不等于已取消中央媒体，须保留job并显示实际状态。

原custom CLI模式保持。对中央订阅的技能，不删除 `require_media_available` 后直接复用用户自定义key：需要将技能的媒体步骤转交上述host工具，或由后续受限的allowlisted skill runner通过同一host IPC调用；不放开 `run_command` 的网络或继承环境，不能把“脚本可以联网”当成完成订阅接入。音乐、音频、声音工作流必须逐项以真实host工具链验收，不只提供目录名称。

## 9. 文件分工与分批可执行顺序

| 批次 | 新增/修改文件与可拒绝的独立交付 | 验收 |
| --- | --- | --- |
| M0 合同和账务底座 | 新 `media/contracts.py`、`media/rates.py`、`media/core.py`、迁移014；改 `permissions.py`、启动/runner守卫 | 真PG验证原子预留、跨产品同钱包竞争、原键/异内容、越权、冻结费率、金额与所有财务不变量；原文本1101基线不可退化 |
| M1 同步图片 | 新 `media_config.py`、`media_runtime.py`、`media_web.py`、`media/artifacts.py`、`media/adapters/openai_images.py`；接入中央app生命周期 | fixture generations/edit/variation按实际支持逐项验证；图片完整解码、hash、归属和可信basis结算；生产默认closed |
| M2 异步视频和音乐 | 新 `media/worker.py`、`media/adapters/newapi_videos.py`、`media/adapters/newapi_music.py` | 提交一次、重启续查、重复轮询/取消、未知提交、过期产物、下载失败恢复、时长/音轨核对和结算 |
| M3 音频和声音 | 新 `media/adapters/audio.py`、`media/voices.py`；按真实协议拆具体adapter | 转写/配音/登记/克隆分别验证；输入时长/字符basis、音色归属、重复登记、有效音频解码和扣点 |
| M4 客户端/Agent | 新 `easel/platform/media.py`、`media_records.py`；改Pi host/worker工具schema、版本hash和运行时包清单；skills仅加中央host分流，不改custom值 | tools无长期key、原turn关联、真实output hash、取消后原job查询、文件另存，工作台和macOS包内验证 |
| M5 真实能力开放 | 服务器私有配置、经审阅不可变定义和费率发布；媒体存储/worker部署 | 每类真实供应商任务、可播放/打开原产物、可信用量算式与wallet差额、失败/中断原操作查询；通过一类才开放一类 |

测试位置：`services/platform/tests/test_media_contracts.py`、`test_media_credits.py`、`test_media_runtime.py`、`test_media_recovery.py`、`test_media_artifacts.py`、`test_media_auth.py`；桌面 `tests/test_central_media.py`、`test_pi_media_tools.py`，保留原 `test_newapi_*` 自定义适配回归。先失败测试，再实现，通过后做独立权限/资金/重派审查。

## 10. 真实验收缺少的输入与完成标准

当前缺少媒体供应商渠道/业务令牌授权、实际媒体模型名/参数能力、可验收费率版本、已允许下载origin及声音样本/音色授权范围。它们通过服务器私有配置接入；不在聊天、源码或发行包中提供密钥。代码和合成协议开发继续，生产媒体保持closed。

完成每一能力必须同时有：当前构建和迁移/角色一致；本人账户真实授权；原job/attempt可查；供应商返回及产物实际验证；用量来源与冻结费率算式；wallet available/held/spent和不可变分录精确核对；重复查询/下载无新增生成或扣点。真实调用、合成协议、静态配置、包内验证分别出具记录。

本设计没有替代正式支付、第二真实产品、云端Agent、高可用和跨平台发行等总目标。它只规定完整媒体工作流的实现次序与可信边界；这些其他总目标继续在总交付台账中保留。
