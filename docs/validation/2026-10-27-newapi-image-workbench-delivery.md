# New API 生图接入：真实工作台交付与扣点验收

**M5 两款模型的真实工作台交付和共享点数扣费已通过。** `gpt-image-2.5-sunburst`、`gpt-image-2.5-flare` 均完成工作台发起、内置 Pi 工具调用、中央服务派发、原图完整校验、归属登记、本地下载和一次结算。

本报告事实来自 **root 于 2026-10-27 当前采集的真实工作台、服务器只读核账与浏览器回执**。报告编写者只读取既有脱敏回执并整理文档，没有新增测试、供应商请求、产品修改、运行包修改或服务器操作。采集快照后发生的新交易不包含在以下余额中。

仍有一笔此前失败的 Flare 请求保留 **1 点预留、actual=null**，尚未处理；新两笔成功验收没有重放或结算该旧请求。

## 一、用户可用入口与交付范围

root 已在实际工作台核对“设置 → 账号与订阅 → 刷新生图模型”：两款模型均显示可用、**1 点／张**，没有刷新错误。账号页显示的钱包及 image／text 权益与服务器回执一致；Flare 本轮 UI 的三笔文本费用加一笔图片费用与账本一致。

实际调用链为：`17868 / v23 工作台 → 内置 Pi → 中央账号与共享钱包 → New API → 指定图片模型 → owned artifact → 本地下载`。当前验收参数为每次一张 `1024×1024` 图片，使用已批准的内部体验费率 `internal-one-point-per-image-20261027-v1`，1 点 = 1,000,000 microcredit。原自定义接口模式保留，用户的历史、登录和接口配置没有因本次桌面升级被覆盖。

浏览器证据：[模型目录](/Users/jiandan/Code/GitCode/Easel/outputs/_run/newapi-image-sync-20261027/evidence/workbench-image-models.png)、[Flare 实际对话](/Users/jiandan/Code/GitCode/Easel/outputs/_run/newapi-image-sync-20261027/evidence/workbench-live-flare.png)、[Flare 本轮账单](/Users/jiandan/Code/GitCode/Easel/outputs/_run/newapi-image-sync-20261027/evidence/workbench-live-flare-ledger.png)。

## 二、两笔真实成品

| 模型 | 中央任务 ID | 终态 | 图片扣点 | 本轮文本扣点 |
| --- | --- | --- | ---: | ---: |
| Sunburst | `9ccc8430-3c29-46cc-9d5d-961be08f6acc` | completed / settled / available，本地 downloaded | 1.000000 | 0.005385 |
| Flare | `a934ccdf-d78d-446c-95ec-7b8fd028a2fa` | completed / settled / available，本地 downloaded | 1.000000 | 0.005618 |

root 已使用 Pillow 完整解码，并视觉核对这两张 1024×1024 橙色方块／蓝色三角形合成验收图；本报告没有重复调用生成或解码。

- [Sunburst 实际下载 PNG](/Users/jiandan/Code/GitCode/Easel/outputs/_run/newapi-image-sync-20261027/evidence/live-sunburst-01.png)：434,956 字节；SHA-256 `f74b0051159ae66c9a6f53612a5fe039a31df8e3fafdd413aa69b0d1b81e8abb`。
- [Flare 实际下载 PNG](/Users/jiandan/Code/GitCode/Easel/outputs/_run/newapi-image-sync-20261027/evidence/live-flare-afterfix-02.png)：463,165 字节；SHA-256 `ef39cbfca2410fb4bcdda196f010aeaa20b0f74817681e8373ec887269a16207`。

每笔均有 **provider attempt 1、typed HTTP reference 1、terminal evidence 1、receipt 1、artifact 1**；blob 已 attached，文件 SHA 与登记相符，未用输出预算已释放。每笔 receipt 的 `quantities.image=1`、`actual=1000000`，`provider_response_id=null`。没有用虚构 body ID 或 token 用量补齐证据。

这些结论来自真实成品、归属和账本链，不以单独的 HTTP 200 代替交付。只读核账回执中的 `provider_posts=0` 指**核账探针本身没有新增请求**；两笔真实生成分别有一条已持久化的供应商尝试。

## 三、共享钱包对账

Sunburst 文本三笔合计 5,385 microcredit，图片 1,000,000，本轮合计 **1,005,385**。Flare 文本三笔合计 5,618，图片 1,000,000，本轮合计 **1,005,618**。

两笔成功验收总计 **2,011,003 microcredit = 2.011003 点**，其中图片 2 点、文本 0.011003 点。成功验收前可用余额 8,963,156，之后为 6,952,153，减少量与两轮实际结算一致；spent 从 36,844 增至 2,047,847。

| 最终采集快照 | microcredit | 点 |
| --- | ---: | ---: |
| 可用 | 6,952,153 | 6.952153 |
| 旧异常预留 | 1,000,000 | 1.000000 |
| 累计已用 | 2,047,847 | 2.047847 |
| 原始发放 | 10,000,000 | 10.000000 |

守恒为 `6,952,153 + 1,000,000 + 2,047,847 = 10,000,000`。当前快照 active=0、pending=1、pending 上限=3。root 最终只读 readiness 还确认原体验账号匹配且保持登录、工作台空闲、两款模型可用、自定义配置及历史保留，未新增点数，正式支付仍关闭；该 readiness 检查新增供应商 POST 为 0。没有因本轮验收新增点数或延长原体验额度期限。该点数账本不表示供应商实际货币费用或微信／支付宝商户收款已经验收。

## 四、旧 Flare 异常与修复边界

旧任务 `81a6155d-c7dd-4b98-bd3f-5e909a4d3ba9` 只派发过一次，取得 HTTP 200／typed reference 并完成图片解码，但 systemd `ProtectSystem=strict` 下媒体目录没有写入例外，实际服务 mount 为只读，触发 EROFS；没有登记 terminal、receipt 或 artifact，因此未交付成功。

root 已按受审计划仅为 API 的媒体目录加入写入例外，并验证媒体目录可写、`/etc` 和 release 目录仍只读。安全修复回执显示数据库写入 0、供应商 POST 0、旧待核算保留；原 f50 服务二进制与 016 schema 没有重打。

该旧任务保持 `pending_reconciliation`、`actual=null`、预留 1,000,000。没有重放生成、伪造用量、免单或改写账本。成功的 Flare 是**在既有真实验收授权范围内开展的另一次独立测试**；旧异常还需要单独可信核算或明确运营决策。

## 五、桌面与分支交付

v23 已由 root 激活在 17868，激活回执 PID 为 40459；133 个受保护文件逐字保存，原 account scope、钱包、历史和自定义配置验证通过。root 确认旧 v18–v22 辅助入口转向 v23；逐项 legacy 安全回执列出 v18–v21 的八个入口。旧 App／ZIP 及私有备份保留。

v23 ZIP SHA-256 为 `48ea4652435178847d825399680c62a818c5431d1877cd31a7d1a779b1850ccd`；发布来源 901 文件，仅包含已审认证护栏和 Pi 媒体结果可见性四个路径增量。中央服务沿用独立 f50 候选 `f50e52511e941d3bea8e724eddbd68330e20eaa6339c5e2437b3880ca9705abf`。

root 确认继续保留当前开发分支，没有新增提交，也没有合并主分支。此前认证响应丢失的可达机制和客户端护栏单列在认证报告；本轮真实图片存储故障不归因为认证问题。

## 六、仍未完成的事项

- 旧 Flare 的 1 点预留和未知费用尚未处理，不能把本轮两款成功验收称为全部运营异常清零。
- 旧恢复演练回执不覆盖本次新 `90` systemd drop-in 内容；旧待核算任务使当前非空备份／恢复准入不满足。覆盖最新配置与媒体状态的新恢复证明仍待补齐，不能沿用旧回执宣称当前全范围可恢复。
- 视频、音乐、语音、云端 Agent 执行等其余产品链不在本次两款生图验收范围内；尚未完成的服务接入、worker、权限、完整故障恢复与生产验证继续作为后续工作，未配置能力保持关闭。
- 桌面签名、公证及干净设备／Gatekeeper 验收尚未完成。正式微信／支付宝商户验收也未完成。本轮通过范围是受控体验账号、两款图片模型、每次一张 1024×1024 图片及相应工具／扣点链。

## 七、证据来源

根证据目录为 `outputs/_run/newapi-image-sync-20261027/evidence/`。当前 `manifest.json` stage 为 `two_models_live_workbench_delivery_and_billing_verified`，记录 36 项输入 SHA，manifest SHA-256 为 `407cdd03315386f2bbf6e42c3b8682851bb6ceb8cdc8729d92df45c21b2dc995`；旧 `manifest-pre-live.json` 保留。

服务器和本机的 `two-models-live-ledger-safe.json` 已由 root 核对相同，SHA-256 为 `c0d383ba8cf7fb900b8f1467217f92806c8a7070c3f5f86a6f398e1e7ed9c187`。逐轮回执、最终钱包、旧失败记录、存储修复、激活与保留回执的精确路径／SHA，以及账务明细汇总见同名 `2026-10-27-newapi-image-workbench-delivery-safe.json`。
