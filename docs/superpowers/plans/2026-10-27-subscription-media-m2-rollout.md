# M2：视频与音乐异步恢复实施小计划

状态：M1 四项 review 修复已冻结，等待独立全中央回归；本文件仅供根任务审设计，尚未修改 M0/M1 的 core/runtime/app 或启用媒体。当前真实渠道仍只有文本。保留自定义接口，不改变桌面输入文件。

## 一、已核实的协议与不能混用的部分

已按 AGENTS 先执行 Context7 `library New API` 与两次 docs 查询，但返回内容未覆盖目标接口，不能把该结果作为协议证明。随后核对当前官方 API 文档、项目现有锁文件／CLI 协议测试，以及哈希匹配的公开插件源。

| adapter / 协议 | 固定接口与真实事实 | 本阶段约束 |
| --- | --- | --- |
| `newapi_videos_v1` | `POST /v1/videos`；`GET /v1/videos/{id}`；带同源鉴权的 `GET /v1/videos/{id}/content`。官方页面提供这些接口；现有 Easel native fixture 对 rc.45 的 seconds/size、public ID 和 content 合同已有隔离证据 | 配置显式 profile / producer 版本；不按域名或模型名猜协议，不用旧 `/video/generations` fallback；不因 completed JSON 缺 URL 判失败；不自动跟随内容重定向 |
| `newapi_suno_v1` | `POST /suno/submit/music` 成功是 `code=success,data=字符串ID`；单任务 `GET /suno/fetch/{id}`；native renderer 返回公开 task ID 与宿主状态，音轨仅取明确 songs[].audio_url | 按已锁 SunoAPI 1.1.0 源码，而非已归档文档的 FAIL 拼写推断状态；不调用 batch POST 查询；不下载封面当音频；不沿用旧 `/generate`、`/feed` |
| `newapi_dashscope_music_v1` | 现有私有 `easel-dashscope-music@1.0.0` 固定 `POST /easel/dashscope/api/v1/services/audio/music/generation`；供应商同步 Fun-Music，响应 task_status=SUCCEEDED、finish_reason=stop、audio.url、usage.duration；New API 自有 `GET /easel/dashscope/api/v1/tasks/{id}` 可重放宿主已存结果 | 不伪造上游异步轮询：插件 `buildQueryRequest` 明确拒绝供应商轮询；已知公开 ID 可查宿主已有结果，无 ID 的未知 POST 不重发。拒绝旧 parameters.duration 和 X-DashScope-Async |

公共证据：[New API 视频创建](https://docs.newapi.pro/zh/docs/api/ai-model/videos/sora/createvideo)、[状态查询](https://docs.newapi.pro/zh/docs/api/ai-model/videos/sora/getvideo)、[内容获取](https://docs.newapi.pro/zh/docs/api/ai-model/videos/sora/getvideocontent)、[Task Plugin v1](https://docs.newapi.pro/zh/docs/plugins/api-reference)。后者仍将 v1 标记为演进合同；固定生产者版本／源哈希是必要条件。

锁定源：`integrations/newapi/catalog.lock.json` 的宿主 rc.45 / commit `6cf0d90b5ff27f901697dddd61e0c28aba8efefc`，SunoAPI 1.1.0 公共源 SHA256 `a4679364a8a8f119ab993ae5064b727d9e0af91e4075f6d0bb32d9297ef5285a` 已下载并核对。DashScope 以仓库现有私有插件源及其 lock 为准，不读取或转移生产密钥。公开最新文档与锁定生产者字段不同处，必须在协议 fixture 明确验证，不能静默兼容猜测。

## 二、独立新文件与内部类型

先只新增文件，不改正在复审的 M1 输入：

- `media/async_contracts.py`：`Submission(provider_job_id, observation?)`、`Observation(provider_job_id,state,artifacts,quantities,usage_hash,response_hash)`、`ArtifactSource(source_id,kind,location,mime)`。descriptor 仅由可信固定 adapter 输出，不接收客户端 URL / job ID。
- `media/adapters/newapi_videos.py`：显式标准视频合同、请求准备、单次 POST、原 ID GET、固定 content GET；首批通过 owned PNG/JPEG/WebP multipart 引用图片，不开放任意 input URL。
- `media/adapters/newapi_music.py`：Suno 与 DashScope 分开类／adapter ID，不共用宽松 envelope 解析，不互相 fallback。Suno 自定义歌词模式明确映射 title/tags/prompt/mv；DashScope 明确 input fields。
- `media/av_artifacts.py`：受限 ffprobe + ffmpeg 全解码与实际格式／时长／音轨验证，只接收本机 owned bytes/temp，不接受网络输入。产物保持原字节。
- `media/worker.py` 与 `media/async_store.py`：有限并发 GET 轮询／下载／恢复；所有生成 POST 留在提交路径，不放进 worker。
- 独立新 tests：协议映射、安全下载、AV 真实解码、异步 lease/restart/failure、实际 PG 资金与存储预算。先观察红，再实现。

M1 全绿且根任务审本计划后，串行接 `media_config.py/media_runtime.py/app.py/core.py/permissions.py`。默认缺省 disabled、无目录，不新增任何默认媒体模型、费率或 Key。

## 三、迁移 018 与唯一派发／恢复合同

新增 `018_media_async_work.sql`，不修改 001–015 已验收历史：

1. `media_work`：job_id / attempt / binding 固定；phase=query/download/done/manual，due_at、lease_token、lease_until、attempts、last_safe_code。每 job 一行，SKIP LOCKED 租约；旧租约不能提交新 lease 的结果。
2. `media_observations`：首次可信终态与规范化 usage / artifact 身份的不可变快照、原始响应 hash、关联的 provider ID／HTTP 状态。非终态只更新有界 work 摘要，避免每轮保存巨大响应。
3. `media_sources`：每个稳定 source_id 一行，私有允许源地址／固定 content 路径、下载进度和对应 artifact ID。唯一约束阻止重启或重复轮询二次登记／二次转存储预算；签名 URL 刷新必须来自原 ID 的可信 GET，并保持同一 source 身份和冻结计量。

所有表按完整冻结 Scope、job / attempt / producer binding 校验；公开投影不带私有 URL、供应商 ID、密钥、storage key 或租约。runtime 仅取得必需表与列权限，不取得发布定义、提高 policy、改账或删除历史的权限。

提交顺序：持久 job / 金融与 015 最大输出空间预算 → 持久出站 attempt → dispatch → 唯一一次 POST → 立即保存 HTTP 关联 → 一旦解析到有效 provider_job_id 先持久 job 关联 → 入队 GET worker。状态解析或下载失败不能丢掉已经保存的 ID。

崩溃后，扫描“已有精确 job association 但未入队”的任务，只补 work 记录；没有 provider_job_id 的提交未知保持待核算，不发 POST。同步 DashScope 完成响应先保留关联与结果／source 快照，然后才下载；已知 ID 可从宿主 GET 恢复，不能重新发同步生成。

worker 只允许原 ID GET／固定 content GET／已持久 source GET，禁止生成 POST 和自动切模型。GET 超时按有限退避重查原任务；轮询／下载窗口到期保持 unknown/pending，不冒充失败或退款。租约跨实例抢占、重启、下载失败和旧 worker 迟到响应均需真实 PG 故障注入。

## 四、资金、存储、状态与锁序

中央原账本是唯一资金权威。原 015 配额与最大输出预算继续使用：输出 stage 原子转实际占用，终态证据持久化后才释放未用预算，未知任务保留所有 pin。不建立第二钱包，不信任 New API 的“失败自动退款”作为中央扣点／退回依据。

计量明确分开：视频 `video_ms`；音乐 `audio_ms`；Suno 若要按实际交付音轨计数，建议新增精确 `audio_clip` 单位与 verified_delivery basis（仅完整解码并登记的 owned 音轨），待根任务确认后增量实现。Suno 插件预估 clips=2 不是实际交付量，也不是 token；DashScope 只有明确 usage.duration 才可作为 provider_usage，精确转换毫秒。没有对应可信量就待核算，不用 nominal seconds、假零量或其他字段凑齐。

即使用户在派发后退出，已接受任务继续按冻结 job/attempt 记产物和证据；新提交、上传、查询和下载仍验证当前用户会话。执行完成、交付处理中、财务待核算独立呈现。cancel 先走既有状态请求；没有明确供应商取消合同和收费证据时，不发猜测 DELETE、不提前释放点数或存储预算。

租约领取事务仅锁 work 行并立即提交；不持 work 锁做网络/解码。写证据／产物与结算遵守 wallet → invocation → job/reservation → storage 的现有顺序；worker 最终确认另取 work 行，不能反向持 work 再等 wallet。观察和 source 保存要求当前 lease token，已不可变证据的财务恢复仍不重新生成。

## 五、AV 工具、资源与分步验收

本机实际探测到 ffmpeg/ffprobe 8.0.1，Homebrew build 含 GPL/version3 编译选项；不把本机二进制拷入桌面或视为 Linux 服务已具备。先使用现有工具做隔离真实生成／解码 fixture，服务端通过显式固定路径／版本和部署许可清单加载，未具备工具就不开放 AV readiness。

探测进程清空凭据环境；禁 shell，禁网络协议、远程 URL、播放列表和外部引用；限制 CPU／Linux 地址空间／文件与输出大小／墙钟／最大像素／时长／音轨数／采样率。实际 ffprobe 之后必须 ffmpeg 全流解码成功；被截断、错误 container/MIME 或缺少已要求音轨的文件不能算成品。SHA256、字节数、格式、宽高、实际时长与音轨属性进入 owned artifact。非完整 `.part` 继续保持失败和配额，不猜删。

实施顺序：

1. 审核本计划和精确外部协议 fixture，写独立 adapter 红测。
2. 实现 video adapter 和受限 AV decoder，验证真实短 MP4 有／无音轨、截断与越界输入。
3. 实现 018 / async store / worker 的真实 PG lease、关联恢复和唯一 source 登记。
4. 接 runtime 单次提交与 worker，覆盖断连／重启／GET 超时／下载恢复／会话撤销；原 M1 全套与金融守卫不退化。
5. 分别接 Suno 和同步 DashScope；真实短音频完整解码和精确数量／时长扣点验证。
6. 独立复审、服务包和完整中央回归。真实媒体渠道和商业费率仍未配置，M5 输入缺口单列，不能用合成协议验收冒充真实模型。


## 2026-10-27 本机实施检查点

M1.1 发布只使用独立冻结的 f50 size-v2 候选，不从本机包含 M2/M3 的工作树重新打包。016 已由 M1.1 typed reference／image 权益占用，017 为 M3 voice-only；本 M2 正式使用 018。以上早期 M1 等待状态为当时检查点，不代表当前发布状态。

- AV 缺尾修复与亚毫秒音轨闭锁已完成，独立复审 27 项全过，见 `docs/validation/2026-10-27-m2-av-duration-independent-review.md`。该代码不在 f50。
- 新增 `async_contracts.py`、`adapters/newapi_async.py`、`newapi_videos.py`、`newapi_music.py`，三种明确 profile 的协议 18 红→18 绿。只合成 HTTP，无真实媒体供应商调用。
- 新增 `018_media_async_work.sql`、`async_store.py`，真实隔离 PG 队列／租约／来源／退出后已接受任务交付 5 红→5 绿，2.27 秒。
- 终态先存独立 `media_observations`，全部 owned 来源登记前不写 M0 terminal evidence，因此 015 输出预算和 pin 不会因观察到上游完成而提前释放。来源 stage/attach 按 wallet→job→work 校验当前租约，领取租约不持钱包锁。
- **尚未完成**：worker、过期队列收口、结算租约围栏、runtime/config/应用接入、018 角色权限与本机 runner 清单、完整故障注入及独立复审。因此上述 5 项不是 M2 完整交付，也不能据此认为当前混合树中央全套通过。
- 根据 root 的紧急任务，M2 实施在这些文件上停止，先调查 M4 实际登录轮换结果未知；未回退已做 M2 代码，未操作 154。恢复 M2 后先完成上述权限和 runner 集成，再跑广泛回归。
