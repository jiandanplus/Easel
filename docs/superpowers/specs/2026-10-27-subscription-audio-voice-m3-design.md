# M3 音频与声音接入设计

日期：2026-10-27；分支：`codex/ui-redesign`。

状态：供根任务审阅具体协议和计量边界。整体开发已授权；本批不发布生产模型、不填写费率、不调用真实供应商。当前工作仅增加独立适配器、声音流程及测试；共享 config/runtime/app、钱包和其他代理的迁移文件不在本代理修改范围。

## 1. 当前事实与方案选择

M0 已有 Scope、一次派发、冻结请求、共享钱包和 owned voice。M1 图片与 M2 视频/音乐有其他代理维护；M3 复用它们的有界 HTTP、产物私有存储和音视频解码，不重新建钱包或宽松下载器。

推荐按明确协议拆适配器：OpenAI 标准配音、OpenAI 转写、MiniMax 私有桥、DashScope 私有桥、Fish 私有桥分别拥有固定路径和 meter 支持矩阵。单一“自动探测兼容音频”方案会混淆二进制、JSON、hex 和 task envelope；仅复制 custom CLI 则会绕过中央钱包、声音归属和未知请求恢复，因此均不采用。

原 custom Edge TTS、本地 Whisper 和供应商直连配置保持原样。订阅请求失败时，不转去这些来源生成替代产物。

## 2. 官方及已锁协议证据

已按 AGENTS 运行 `ctx7 library OpenAI`，再分别读取 speech 和 transcriptions 文档，共3次请求。随后核对官方 REST 页面；没有查询凭据或发送业务载荷。

- [OpenAI speech](https://developers.openai.com/api/reference/resources/audio/subresources/speech/methods/create)：本批固定 `POST /audio/speech` 的音频文件响应；不读取 SSE，不把组织聚合 usage 当作单次调用用量。
- [OpenAI transcriptions](https://developers.openai.com/api/reference/resources/audio/subresources/transcriptions/methods/create)：本批显式选择 JSON 或 verbose JSON，文件走 multipart。完成 usage 的 tokens 与 duration 是不同类型，按声明 profile 解析。
- [阿里声音复刻 HTTP](https://help.aliyun.com/zh/model-studio/voice-clone-design-http-api)：当前官方已经支持 `audio` Data URI，并兼容旧 `url`；仓库的固定私有插件1.0.0仅接 `url`，两者不能悄悄混用。
- [MiniMax voice clone](https://platform.minimax.io/docs/api-reference/voice-cloning-clone)：上传文件与音色登记是不同请求，登记不能隐式附加试听合成。

New API 的具体路由不是 OpenAI 官方协议，以已锁本地插件源码为权威：

| 私有协议 | 版本 | plugin.js SHA256 |
| --- | --- | --- |
| easel-minimax-voice | 1.0.0 | `dfb3f1a7d6f1d48106a491c38b22b673d3e27971a14a0d86e47c4cb384e7ba85` |
| easel-dashscope-voice | 1.0.0 | `0a33cc6da2590fb7b5fa4318ac5d09970f7230694d46e5badf9576ac7135b3a7` |
| easel-fish-tts | 1.0.0 | `c8b188927dea5ca94e8baa2578f922be924d69f48e245e6d1bff3e7700f62acc` |

这些源码只有 fixture/宿主合同证据；不表示服务器已安装，也不表示有可用真实音色。生产 binding 必须匹配明确协议版本和能力声明。

## 3. 独立适配器和 meter 矩阵

新增 `media/adapters/audio.py`，按类拆分标准配音和转写；私有声音协议可独立为 `media/adapters/newapi_voice.py`，不形成一个按域名猜测的入口。

### OpenAI 配音

`OpenAISpeechAdapter.prepare(request, assets, *, owned_voice=None)` 仅接受已发布的参数集合。内置声音由已发布 enum 限制；custom voice 只能由服务器把已经复验的中央 voice ID 转成私有对象，浏览器不能提交供应商 ID。

固定 `stream_format=audio`，只输出 mp3/wav。输入保留原始 Unicode scalar，不做 NFC 或其他静默改写。速度采用已发布整数 `speed_percent=25..400`，以 Decimal 转成接口的数值，避免以浮点参数作为计费输入。旧模型不支持的 `instructions` 必须由显式 profile 拒绝，不能依模型名称猜测后吞字段。

支持 meter：`unicode_scalar/verified_input_v1`、`audio_ms/verified_delivery_v1`。二进制音频没有逐请求 usage 对象，因此拒绝 `provider_usage_v1`。

可信关联明确为 `HttpCorrelation(namespace="openai_x_request_id_v1", value=真实 x-request-id)`，不是虚构的音频对象 ID。adapter不从本机 request_id、正文SHA、Content-Length或其他字段生成供应商ID，不从response body生成假usage。缺少这个明示profile要求的真实header时，返回固定错误并由runtime保留pending。adapter返回带namespace的correlation类型；shared runtime/core接入时必须保持其类型语义，经根任务确认后才能映射为持久终态关联。`evidence_hash`另外保留实际收到音频bytes的SHA256；它证明收到哪个正文，不是供应商成本或供应商ID。

### OpenAI 转写

`OpenAITranscriptionsAdapter.prepare(request, assets)` 恰好一个中央验证的音频或带音轨视频素材，固定安全 multipart 文件名。文件种类由真实格式元数据决定；不转发本机路径、不接受素材 URL。最大音频大小受模型定义和官方文件限制共同限制。

参数只允许语言、提示词及显式 JSON/verbose JSON profile；首版不冒称支持 diarized JSON、speaker references 或 SSE。转写结果保留原文；完成后生成 UTF-8 JSON 产物，即使静音结果为空文本也仍有非空可校验文件。

支持 meter：输入音频/视频实际时长对应的 `verified_input_v1`；`input_token`/`output_token` 仅在严格 tokens usage 完整时支持；duration usage 明确为秒时转换为 `audio_ms/provider_usage_v1`。不把 verbose JSON 的展示 duration 当成收费 usage，不拿转写文字长度冒充 token。

### 私有声音桥

- MiniMax：上传 `/easel/minimax/v1/files/upload` → 登记 `/easel/minimax/v1/voice_clone` → 合成 `/easel/minimax/v1/t2a_v2`；每一步有不同请求和关联。只使用网关 public task 引用，供应商 ID 不公开。固定短 MP3/hex；不附加试听。
- DashScope：create → query_voice 审核 → synthesize。只将审核 OK 且 target_model 相同的引用登记成可用中央音色。旧插件只接受 URL 素材，因此在缺少服务器受限素材发布能力时，enroll 保持 closed；不能接受用户填写任意公网 URL。可以独立开发新显式 Data URI 协议，但必须另版本、另验收，不能伪装旧插件兼容。
- Fish：只接受已有插件定义的内联已验证参考音或管理员白名单参考；提交一次，之后查询同一任务和受保护 artifact。其 `input_characters` 是桥观察值，不是供应商账单；不映射为 provider_usage token 或 Unicode scalar。没有提供音色登记接口，不能制造“供应商已登记”的假记录。

桥的字符计量可能是 UTF-16 或供应商字符规则，不能随意映射为 `unicode_scalar/provider_usage_v1`。可开放的通用价格是明确发布的实际输入 Unicode scalar 或完整解码音频毫秒；登记仅在真实 owned voice 就绪后按 `registration/verified_delivery_v1` 计1。原始供应商字符事实作为私有证据保留。

## 4. 多 POST 声音流程需要明确的持久阶段

已有 M0 每个 job 只有一个 provider_attempt 和一个 response association。不能在它下面不留记录地循环上传、登记、审核；断线恢复也不能从第一步再来。

建议新增独立阶段表（迁移编号由根任务分配；不修改015/016），`media/voices.py` 管理严格状态机：

```text
MiniMax: upload → register → ready
DashScope: create → verify(n) → ready / rejected
Fish: submit → query same task → download
```

每阶段在出站前持久化 `(job_id, attempt_id, ordinal, stage, method, wire_hash, request_id, frozen binding)`，响应后追加 HTTP/task/结果证据。阶段结果不可变；相同阶段/请求内容返回原记录，异内容409。未知是否已经提交时标为 unknown，不能再次 POST。能够只读 GET 原网关 task 时才查询原记录；没有 task ID 的不确定结果停在 pending，不凭404重建。

DashScope verify 在这个旧桥上虽然语义是查询，HTTP却为一次新的 POST/task，且插件报告操作 count，因此不得把它当成可无条件重试的免费 GET。只有上一轮明确完成并返回 DEPLOYING 时才可按明确预算开启下一轮；每轮各有持久记录，上限固定，未知轮不自动再 POST。

最终 ready 音色才调用 M0 `register_voice`。中央 public voice ID 绑定用户/租户/产品；私有记录保留 gateway task 引用、provider/version、目标模型和流程证据。合成前重新验证当前 Scope、音色状态、目标模型及冻结供应商边界；跨账号、跨产品、跨渠道或过期引用均拒绝。

根任务已分配独立阶段迁移 `017_voice_stages.sql`（016归M2）。每个POST阶段都必须先持久化；整链只使用原job/同钱包预算，不另预留或收费。旧 DashScope URL 素材入口继续closed；如果新增Data URI producer，必须拥有独立协议标识和版本。本批协议测试可以先进行，不能用fixture success掩盖这些条件。

## 5. 解码、存储和 HTTP 边界

复用 M2 `inspect_av(raw, *, kind, mime, max_bytes, max_duration_ms, max_pixels, require_audio, tools)`。它返回 `sha256/size/mime/properties`，原始 bytes 不转码；真实探测和完整解码之后才登记音频，持续时间以整数毫秒表示。没有固定 ffmpeg/ffprobe 时保持 closed。PCM本批不开放。

所有 POST 均一次，不自动 retry；关闭 redirect 和环境代理继承，Authorization 只发到服务器固定 upstream origin。下载 URL 需匹配明示 origin/path；CDN不带供应商凭据。普通HTTP200、MIME或文件扩展名都不能独立证明完整成功。

错误只返回固定 code；上游原文、signed URL、长期 key、私有音色ID不进入前端、Pi上下文或日志。缺 usage、关联、完整文件、可信阶段结果或真实时长时，不能按0点宣称完成。

## 6. 可独立验收的小步骤

1. **标准协议红测**：新测试导入适配器并断言 prepare 字节和HTTP结果。覆盖 speech mp3/wav、scalar文本、格式/参数拒绝、真实 request ID、超长/截断/重定向、无retry；transcriptions multipart、严格JSON、usage类型/数值/重复键、无usage、空文本。
2. **标准协议实现与解码**：实现最小adapter和安全结果类型；使用合成WAV/固定ffmpeg生成样本完整解码，错误容器/损坏尾部拒绝。与 M2 decoder 接口对齐，不改其文件。
3. **声音引用红测**：从已锁插件fixture提取公共 envelope，证明不泄露原始ID、错误stage/target_model/owner被拒绝；对多POST记录用真实隔离PG测试重复阶段、断线、未知与恢复不能多发。
4. **私有桥实现**：逐个实现明确prepare/parse/query/download；阶段库获根任务确认后才连接，不给缺少安全素材合同的enroll造成功路径。
5. **集成移交**：根任务/负责共享runtime代理接入registry、config、app和能力过滤；只接受明确支持的meter组合。每类对真实PG证明1次准入、预留→证据→结算/待核算、重复操作0额外POST、越权拒绝。
6. **真实能力开放**：本轮无真实媒体渠道，所有live目录继续closed。以后每类必须拿真实任务、可播放原产物、完整证据和钱包差额验收后才开放；fixture结果仅代表协议/账务测试。

本批所有shell使用 `rtk`；保留现有分支，不commit/push/merge，不改变用户接口配置或账号数据。
