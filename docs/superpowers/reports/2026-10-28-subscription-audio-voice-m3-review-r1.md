# M3 审查修复与桌面中央联调候选 r1

本文件补充 2026-10-27 M3 runtime 报告。旧 24 文件候选、229 passed 收据、manifest `66fc1036e9a737de0f0fbdd63ae6ac68129feec79dc9429b0b86e13067a1d965` 与 source tar `ba4503638666ec083fed189abdd7834729b7d0729fb772fefa1b1d1b294be9b0` 均原样保留。本批没有改桌面生产文件、001–016 或 018、固定 f50/016 运维工具，也没有提交、部署或真实供应商请求。

## 两个实际缺口与修复

1. 独立审查证明，DashScope verify 在原链仅余 100 ms 时，30 ms 一块的连续响应仍能超过原 dispatch + 5 分钟后完成。VoiceExecution 现从数据库重读原 dispatch 时间，为 POST、verify、finish 和恢复 GET 统一施加绝对墙钟超时；每次最多 90 秒且不超过原链剩余预算。超时 POST 留下原 permit 和 unknown 观察，停止派发；恢复 GET 超时也保留原 hold，后续截止时间到达后不再查询或重 POST。原报告的“24 小时恢复扫描”只是候选扫描窗口，实际声音链执行现在受这条 5 分钟截止时间约束。
2. 独立审查使用非 owner runtime 角色证明，019 CHECK 原先接受 type=null、token 总数不相等和超 int64 事实。019 现增加 immutable `valid_media_protocol_facts(text,jsonb)` 闭集数值函数，CHECK 要求结果 `IS TRUE`。它拒绝缺字段/NULL/多余字段、错误 JSON 类型、负数/非整数/超 int64 字符和 token 数、token 和不等；duration 需要明确 ceil_milliseconds_v1、有限非负数且向上取整毫秒不超 int64；MiniMax audio_length_ms 不超 900000。原 binding、attempt、namespace、真实 response SHA、终态关联与不可变触发器不变。

017 和 019 仍未部署。未来完整备份、恢复和迁移验收还必须包含新的 `valid_media_protocol_facts` 函数和 CHECK；原四张证据表的 SELECT/INSERT 最小权限不变。owner 主动删除触发器属于 owner 威胁边界，本批仅记录观察，不扩张为新的启动阻断要求。

## 行为证据

`test_media_m3_review_regressions.py` 从独立审查反例转为仓库回归。修改实现前，原两例在真实隔离 PG 下 **2 failed / 1.80 s**：三种非法事实全部被低权 SQL 接受，慢流未触发 TimeoutError。修改后两例通过，扩展为四例：加入其余单位/数值闭集边界和 MiniMax 原 task 恢复 GET 的慢流截止时间与 hold 验证。独立 4 例日志为 **4 passed / 2.37 s**。原声音、阶段、音频加这些回归曾运行 **51 passed / 25.80 s**。

新 `test_media_m3_desktop_roundtrip.py` 不替换中央或桌面实现，使用真实随机 schema PG、真实 ASGI HTTP 路由、当前 MediaHost/MediaClient、真实本地 ffmpeg/ffprobe 和 AV 字节，仅合成账户与上游 HTTP。10 例通过（最终单文件复跑 **10 passed / 9.34 s**）：

- 标准 speech 一次预算、真实 header 关联和原字节 WAV 下载；重复 tool call 查回原 job。
- owned WAV 原字节上传，经真实 multipart 转写，空文本作为 JSON 成品下载并结算一次。
- MiniMax 登记获得 ready voice，同一桌面 turn 以独立预算合成 MP3；2 个 job、2 个 token POST、2 次结算，供应商共 3 次 POST。公共投影不包含供应商任务 ID，另一 owner 的 voice GET 返回 404。
- Fish 异步原 task GET，首次下载失败后重建 runtime worker 从 PG 恢复，只有 1 次生成 POST，最终下载原 WAV。
- 视频、Suno 音乐、DashScope 音乐的原字节 owned 文件下载，均只派发 1 次并结算 1 次。
- unknown speech 重建 host 后只查询原任务，无第二个预算或生成 POST，actual=null、hold 保留。
- 原任务/产物 metadata/content 跨 owner 返回 404；下载响应迟到时 host.stop 或账户切换阻止本地发布，已有合法服务结算不被篡改。

更宽的定向回归为 **243 passed / 75.56 s**（原 229 + 新审查 4 + 新桌面 10），收据记录于 `/private/tmp/easel-m3-runtime-review-r1-20261028/focused.log`，数量也在候选 manifest 中记录。完整中央套件由根任务协调，未在本批自行启动。

## 交付边界与后续

这证明本地真实中央和桌面边界能够协作，并不证明生产渠道或真实商业计费已上线。缺真实费率、未被证明的渠道、缺安全素材 producer 的旧 DashScope enrollment 继续 closed。订阅和自定义两种入口保留；长期 Key 没有进入 UI/Pi。新候选需要根任务安排独立审查复验和完整 suite，并另行决定生产迁移、备份和恢复验收。
