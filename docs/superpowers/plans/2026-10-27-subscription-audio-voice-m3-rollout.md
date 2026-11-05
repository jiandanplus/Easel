# M3 中央音频与声音运行时 Implementation Plan

> **For agentic workers:** 使用 executing-plans 逐项执行；本任务已由根任务分配，不再派生代理。不 commit/push/merge，不访问生产或真实供应商。

**Goal:** 将已完成的音频协议和持久声音阶段接到中央订阅预算、归属产物、一次结算与原任务恢复。

**Architecture:** 标准 speech/transcriptions 使用真实 HTTP typed reference、原响应 hash 和完整解码。私有声音多 POST 使用 append-only VoiceStages；合成复用 M2 GET-only 工作器及稳定 source identity。生产默认 closed。

**Tech Stack:** Python asyncio/httpx、PostgreSQL/psycopg、固定版本 ffmpeg/ffprobe。

## Global Constraints

- 订阅和自定义路径同时保留，unknown 不重新 POST、不制造 usage、额度或零费用成功。
- 不改迁移001–016与018，不覆盖M2冻结证据；017尚未部署允许完善。019需根任务分配。
- 全 shell 使用 rtk；真实隔离PG随机 schema，真实 AV fixture，禁止真实 auth/key 读取。
- 无真实渠道、商业费率或安全素材发布合同的能力继续 closed。

## 已确认缺口

1. MediaSettings 只接受 image/video/music adapter，缺少显式 AudioProfile/BridgeVoiceProfile 和不可变指纹。
2. MediaRuntime 上传只解码图片、执行固定 OpenAIImagesAdapter，无法生成 owned 音频/转写文件。
3. 标准音频 typed reference 数据约束已具备；runtime尚未消费 AudioResult。
4. Core 输入 video duration 未提取 audio_duration_ms；视频转写音轨计费会错。
5. OwnedVoices 当前 binding_hash 把登记公共模型与合成公共模型绑死；必须验证同一供应商边界和目标模型，同时保留原登记proof。
6. VoiceStages 未接 dispatch、状态恢复、ready登记和财务；DashScope旧URL producer缺失，登记必须closed。
7. M2 work adapter CHECK 仅3种协议，需要独立新迁移与显式桥包装才可复用原GET任务/稳定artifact。
8. 启动角色和API公开声音投影尚未接M3。

## Task 1: 标准音频配置与执行

**Files:** media_config.py、media_runtime.py、media/core.py；新增 tests/test_media_audio_runtime.py。

- [x] 写真实PG行为测试：speech必须保留raw WAV、真实req header/body hash、按scalar一次结算；转写上传owned WAV/multipart、空文本JSON产物、严格usage；缺decoder和未知响应closed/pending。
- [x] 执行 `rtk proxy env EASEL_CENTRAL_TEST_DSN_FILE=/private/tmp/easel-media-review-20261027-second/test-pg-private.json .venv/bin/python -m pytest services/platform/tests/test_media_audio_runtime.py -q`，记录真实失败。
- [x] 配置新增明确profile并纳入binding；runtime通过capability选择音视频输入解码及typed audio输出，使用现有stage_blob与ArtifactStore私有写入。
- [x] 同一命令验证绿色；原audio协议和引用回归。

## Task 2: 私有声音阶段与owned权限

**Files:** media/voices.py、media/adapters/newapi_voice.py、media_runtime.py、017_voice_stages.sql；新增 tests/test_media_voice_runtime.py。

- [x] 新增MiniMax上传→登记→ready→一次registration结算测试；第二阶段POST前必须可从PG读到permit；任一未知不重派。
- [x] 新增同scope/provider/version/target跨登记与合成公共模型复用测试，跨owner/target/provider拒绝。
- [x] 先观察失败，再实现run-stage持久化及公开owned voice投影；旧DashScope enrollment继续closed。
- [x] 验证unknown与重复调用无额外POST，只有真实ready计1。

## Task 3: 原任务GET恢复与受限权限

**Files:** 根任务分配的新迁移、media/async_store.py、media/worker.py、M3桥包装、permissions.py、app/media_web.py。

- [x] Fish单POST pending→重启→同task GET→完整解码→稳定artifact→一次结算；下载失败重取不重生成。
- [x] 已知声音task只GET，阶段unknown且无ID保留hold；DashScope查询POST必须单独permit和有界总预算。
- [x] 默认closed、配置指纹变更拒绝恢复、runtime仅SELECT/INSERT阶段表且禁止篡改/DDL。
- [x] focused通过后给根任务文件manifest与红绿证据；完整suite由根任务串行协调。

## 执行记录

标准音频7项行为红测首先确认配置拒绝；实现后7绿。私有声音4项红测首先确认配置拒绝；实现后4绿。随后逐项补充了完成阶段中断、公共路由/权限、worker fence、无proof登记、REFERENCES授权、DashScope能力筛选、原始usage事实和纯音频启动guard等真实行为红绿。新019已由根任务分配；未改001–016/018。完整suite与独立review由根任务协调，尚不代表生产开放。
