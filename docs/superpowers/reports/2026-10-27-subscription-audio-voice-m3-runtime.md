# M3 中央音频与声音运行时交付候选

日期：2026-10-27。仅本地候选；所有生产配置和渠道继续默认closed。没有commit/push/merge、SSH、生产配置改动或真实供应商请求。

## 本批接通的链路

- 标准speech：明确AudioProfile → 冻结请求/一次预算 → 一次POST → 真实x-request-id typed reference和收到的body SHA → 原字节全解码 → owned artifact → 一次结算。没有供应商对象ID时不制造ID。
- 标准transcriptions：owned音频/带音轨视频上传、真实AV解码 → 固定安全multipart → 严格JSON/token/duration usage profile → UTF-8 JSON成品（空text也有可验证文件）。视频输入按真实audio_duration_ms计量。
- MiniMax：owned样本 → 先持久upload permit → 上传 → 先持久register permit → 登记完成proof → owned ready voice → registration=1。不同公共登记/合成model可复用同一被证明的供应商音色边界。
- MiniMax/DashScope/Fish合成：一次POST保存原task关联；复用M2 GET-only lease、稳定source/artifact身份、原字节解码及财务围栏。下载失败/重启只查原task，不生成第二份。
- 旧DashScope URL enrollment仍closed。独立verify阶段runner要求已有可信create阶段，每一轮POST单独持久permit；最多8次verify/总9POST，链路5分钟上限，共用原hold。unknown/rejected停止，没有安全素材producer时公共登记不调用此runner。

## 计量矩阵

| adapter | capability | 支持unit/basis |
|---|---|---|
| openai_speech_v1 | audio.speech | unicode_scalar/verified_input_v1；audio_ms/verified_delivery_v1 |
| openai_transcriptions_v1 | audio.transcribe | audio_ms/verified_input_v1；tokens profile明确支持input_token、output_token/provider_usage_v1；duration profile支持audio_ms/provider_usage_v1 |
| newapi_minimax_voice_v1 | voice.enroll | registration/verified_delivery_v1 |
| newapi_minimax_voice_v1 | voice.synthesize | unicode_scalar/verified_input_v1；audio_ms/verified_delivery_v1；明确audio_length毫秒/provider_usage_v1 |
| newapi_dashscope_voice_v1 | voice.synthesize | unicode_scalar/verified_input_v1；audio_ms/verified_delivery_v1 |
| newapi_fish_voice_v1 | audio.speech | unicode_scalar/verified_input_v1；audio_ms/verified_delivery_v1 |

桥字符事实仅私有保存，不转成provider scalar/token；任何缺失/不可信用量都保留hold。表中没有商业费率，本批只使用test-only不可变fixture费率。

## 身份和中断边界

原登记scope和binding/proof保留不可变。合成前验证当前session/authorization_epoch、同user/product/tenant/billing account、provider ID/version/base/credential hash/protocol/完整profile/capabilities/target/download边界。允许同账户新会话复用；跨Scope、provider、目标、profile、凭据、过期/撤销、缺proof与未ready均拒绝。公共voice只含id/name/language/expires_at/state，不返回供应商音色或任务ID。

独立VoiceExecution阶段runner可在已完成的upload后创建唯一register permit；既有permit不重用。unknown有原task时先GET复验，unknown无task时保留hold。M2 MediaWorker和VoiceRecoveryAdapter永远只执行GET，POST不进入其恢复路径。声音GET尝试也有append-only观察记录，受64条/阶段及24小时恢复边界限制。

## 迁移、完整表与最小权限清单

- 001–016和018原bytes保持不变。
- 017仍未部署：media_voice_steps、media_voice_step_observations、media_voice_registration_proofs。proof增加voice_boundary_hash；新增media_voice_owned_proof约束触发器，阻止通过旧core或裸INSERT把未ready声音登记为已就绪。
- 新019仍未部署：扩展media_work的adapter CHECK接受三个明确声音协议；保留原M2 work/observations/sources所有状态与围栏约束。
- 新019新增media_protocol_facts：job/attempt/binding/namespace/真实response SHA及严格计量字段；不存body、prompt、URL、voice ID或key。其immutable/no_truncate/binding/terminal hash触发器及closed-field CHECK都必须随迁移保存。这里只保存最终响应事实，不将中间POST阶段事实冒充final body。
- 生产runtime仅对四张新增私有证据表授予SELECT/INSERT；无UPDATE/DELETE/TRUNCATE/REFERENCES/DDL。原media_work/media_sources沿用M2列级UPDATE白名单，原共享账本锁序不变。
- 未来完整M3备份必须在M2完整表集上加入上述四表及017/019函数、触发器、约束与role ACL，并包含所有owned AV blob和真实SHA。旧f50/016 mixedDR工具、固定SHA与其证据未升级；新版本恢复验收需要根任务另开候选。
- scripts/validation/verify_central_live_text.py 的候选准入清单已同步019；测试仅prepare/受控协议fixture，不代表真实text或media供应商验收。

## 验证与剩余验收

真实隔离PostgreSQL随机schema、真实ffmpeg/ffprobe和本地合成AV；HTTP供应商传输是受控fixture。首次较宽focused 149 passed/44.14s，随后新增证据/启动/字段闭集等红绿。最终focused为229 passed / 82.53s；日志与不可变文件manifest记录在 /private/tmp/easel-m3-runtime-20261027/。

完整中央suite、独立代码/资金/权限审查和M4真实host连中央fixture由根任务协调。未配置真实渠道、未发布商业费率、未证明生产模型能力；本批不能表述为音频/声音全功能已上线。普通音频POST缺header/丢body、声音unknown无task、过期/损坏产物、冻结配置变更均保持安全待核算，不自动重发。
