# M4 桌面媒体 Host 与 Pi：独立审查

日期：2026-10-27；独立 reviewer：`delivery_media_review`。仅只读审查实现，新增独立临时反例和本报告；未修改产品实现。

**最终结论：下述 P2 修复冻结输入内，没有剩余已知阻塞。独立执行 257 项 Host/原回归/实际 Pi SDK/媒体 Web/本机记录测试、3 项中央 HTTP+PostgreSQL 链路、55 项前端测试全部通过，无 skip。** 这些是本机和 synthetic provider 证据，不代表新安装包、真实供应商模型或生产已启用。

## 冻结输入

- root 最终 P2 manifest：`/private/tmp/easel-m4-host-freeze-20261027-p2.json`，SHA256 `46e926161e03e254d2c861c9d7d0b349f47936b1f664542e4c8798c98948f02d`。
- manifest 中 9 个文件和 6 个依赖逐项匹配；独立运行额外捕获 central_web、前端 API/生图目录/恢复组件及相关测试，共 23 文件的运行前后 hash 完全一致，结束后再次核验相同。
- 实际 SDK 临时 worker 与 `runtime/pi/worker.mjs` 逐字节相同，SHA256 `fa122e93d0d9cffae32aac5f95724189c039ab22a705f09d74c125d57515342f`。测试实际使用已有审核包内 Node/Pi 依赖，不是用 mock 一条成功字符串代替工具合同。
- PG 链路仅使用新尺寸后端 staging，wheel SHA256 `f50e52511e941d3bea8e724eddbd68330e20eaa6339c5e2437b3880ca9705abf`。其独立完整后端结果为 1322 passed/2 opt-in skips，另见 `2026-10-27-newapi-images-m1_1-size-independent-review.md`。

## 实质问题与修复

1. **恢复预算没有贯穿 refresh。** 媒体 HTTP 原先由同步客户端逐块检查 deadline，lookup 的最后 job GET 还可能使用默认 15 秒。随后媒体 GET 改为真正 async wall deadline，但 `_snapshot->_access` 的旧同步 token refresh 仍能超时。独立用真实无凭据 loopback HTTP 分块响应复现：配置总限 0.1 秒，实际约 0.149 秒后才拒绝。最终 MediaClient 的私有 refresh 也使用 async absolute deadline，固定 refresh 路径原始凭据仅留 Host，经原 auth validator、scope/account 完全一致后才串行保存。10 秒 recovery 预算贯穿 refresh/me/lookup/最终 job GET；slow drip、stalled read/refresh 均有测试。
2. **refresh_replayed 未沿原语义清认证。** 新媒体错误白名单遗漏原 auth-invalid 集合中的 refresh_replayed，导致已失效 refresh 被当临时媒体错误保留。最终只在明确 401 时按原规则清当前 auth；503 保留。迟到旧 401 还必须匹配原 scope 与 access token，不能删除新的同 owner 会话。
3. **1024×1024 仅为请求，未作为交付条件。** 初版 PG 正例用 7×4 也可扣 1 点，真实上游若忽略 size 会违背工具描述。root 已明确将实际尺寸纳入本轮交付。新后端仅显式 New API profile 在收费前限制 n1/1024，并在解码后、stage_blob/写文件/注册之前核对尺寸；Host 还在 completed+available 完整详情、下载 metadata 和最终完整解码另存时分别校验 1 张 1024。operation 公共摘要不伪装 artifact 详情：lookup 摘要后必须再 GET 原 job 完整详情。
4. **已付费历史被 UI 分页截断。** 独立创建 51 条合成记录复现：旧原 job 仍存在且 exact-turn 可读，但 `owned()` 复用仅最新 50 条的 rows 列表，导致 get/cancel/download 失败。最终 owned 使用独立完整有界扫描；exact-turn 直接按固定 hash 文件读。fresh claim 在私有锁内先查原键，再检查 1000 条容量，不允许生成扫描窗外的 paid job。目录扫描 4096 项/2 秒，超界或不完整显式报错；满容量/不完整均固定中文提示，不能误报 not found 或引导查询不存在的新任务。没有删除、归档或覆盖旧记录。
5. **UI 执行中映射。** 生图处理中实际合同为 processing，原映射 pending 会显示未知。最终处理中文映射明确；actual=NULL 继续显示未知，不假填 0。文本为空但生图存在时仅称“未找到文本记录”，不称整轮没有关联记录。

## 已核验的边界

- 供应商 Key 留在中央；产品 access/refresh、media task token 不进入 Worker payload、工具 schema/result、shell 环境、operation JSON 或前端。Worker 只收到公开模型/费率摘要和原文本执行本机 proxy 凭据。
- 本次 accepted 账号、完整 Scope、来源、workspace-write 和 turn 必须成立，媒体工具参数不能自选 URL、Key、钱包或 Scope。logout/账号切换后迟到响应不能更新当前结果；原 job/key 仍保留。
- 原 turn 在 mint 前落盘，工具 ID/模型/body hash/scope hash 冲突拒绝。同 turn 最多 1 个生成请求；mint_unknown 与 job_unknown 区分。重启或同键未知恢复只 GET 原 operation/job，不重发生成、不自动换模型。
- 产物必须属于本机 owned 原 job；当前中央授权再次验证。按 size/SHA/MIME/解码/到期及原 job metadata 核验，仅保存到允许的 outputs 新文件。目录逐级 NOFOLLOW，最终 link 无替换；原稿/已有目标不覆盖。
- 长网络/解码不持中央账号文件锁；最终短 publication 锁与 logout/stop 串行，退出后的晚到图片不能发表。失败不会变成取消完成、退款或 0 点；原状态可查。
- 订阅模式旧脚本不能绕过 Host 取产品/供应商 Key 或借已批准 shell 直接联网。实际 macOS sandbox 测试已执行，无 skip；自定义原文件/运行工具回归也包含在本次执行。
- 前端只通过 loopback 原生读入口查询目录/原 turn；未知费用与预算、已知文本费与生图费分开展示，账号 boundary、目标变化、卸载和超时均清理/拒绝晚到结果。

## 独立执行

| 组 | 实际结果 | 证据 |
|---|---:|---|
| Host+Pi 原关联回归+实际 SDK+媒体 Web+3 项独立记录反例 | 257 passed，0 skipped，14.92s | `/private/tmp/easel-media-review-20261027-m4/outside-sdk.log` |
| Host→中央 HTTP 路由→真实隔离 PG | 3 passed，0 skipped，2.71s | `/private/tmp/easel-media-review-20261027-m4/postgres-three.log` |
| 前端生图目录/账号/同轮费用恢复 | 55 passed，0 skipped | `/private/tmp/easel-media-review-20261027-m4/frontend-target.log` |

PG 三例分别为：真实 1024 PNG 解码另存且同钱包精确扣 1000000 microcredits；供应商超时保留同额 hold、actual=NULL，重启只读；7×4 尺寸不符保留 hold、actual=NULL、无 artifact（后端独立尺寸反例还明确核对终态/回执为 0）。中央核心 job POST 与 provider POST 均恰好 1 次；原键重放和恢复没有新增生成，session 撤销后查询拒绝。

独立记录反例还直接使用实际默认 1000 容量：在 999 条合成记录上竞争两个 fresh claim，恰好一个成功、另一个 capacity 拒绝，最终 1000 条；满容量仍可原 claim/owned/exact-turn 恢复。51 条旧 job/op 可取回，crossowner 拒绝。人为将扫描界置 0 时明确 incomplete，原 exact-turn 与重复 claim 不借扫描仍能读取。临时测试为 `/private/tmp/easel-media-review-20261027-m4/test_media_records_independent.py`。

最终机器可读证据为本目录 `2026-10-27-media-host-independent-review-safe.json`，包括全部 23 输入文件 hash 和每组 exit/log。证据不含业务 Key、DSN、真实供应商 URL 签名或生产账号数据。

## 范围限制

本次未调用真实供应商，未写生产 SQL、未发放真实权益/点数，未启用模型或正式付款；未安装新 DMG、未做新的干净 macOS/Gatekeeper 验收，也未将组件测试描述成浏览器视觉验收。M2/M3 视频/声音链路不在此次审查内。1000 条本机媒体记录和 4096 项/2 秒完整扫描是明确的 fail-closed 容量边界；后续扩量需索引/归档设计，本轮不自动丢弃旧付费记录。
