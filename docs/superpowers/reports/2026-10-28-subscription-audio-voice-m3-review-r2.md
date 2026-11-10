# M3 恢复预算修正候选 r2

r2 修正 r1 将 5 分钟 POST 截止时间错误扩张到原任务 GET 和已完成结果结算的问题。本文件对恢复期限的说明取代 r1 报告对应段落。原始 24 文件/229 条候选与 r1 的 27 文件/243 条候选及其源码包、manifest、日志全部保留，不覆盖旧证据。

## 最终恢复合同

- `post_chain_budget`：原 dispatch + 5 分钟，单次绝对墙钟最多 90 秒且不得超过剩余时间。适用于新增 MiniMax upload/register 阶段以及 DashScope HTTP POST 式 verify；每次新增 POST 仍先持久 permit。已有 unknown permit 永不重 POST。
- `recovery_budget`：原 dispatch + 24 小时，单次绝对墙钟为 `min(worker_wall_seconds, 90, 原恢复期限剩余时间)`。适用于只查询原 ID 的 GET，以及已被证明完成的 register 结果入账。继续复验原冻结配置与身份/证据约束，恢复不创建新预算。
- upload 已完成但 register 尚未派发，超过原 5 分钟后禁止创建 register POST。已有 register task 超过 5 分钟仍可 GET 原 ID，成功后登记 ready voice、结算原预算一次；已保存 completed register 结果可以不做 GET 直接补完结算。
- GET 慢流超时或原 24 小时恢复期限已过时保留 hold，没有伪造费用或重复 POST。DashScope 缺安全素材 producer 的旧公共 enrollment 继续 closed。

## 修改范围与回归

相对 r1，生产代码仅改 `media/voice_execution.py`，拆分 POST 与恢复预算；019 严格数值校验保持 r1 字节。新增/修正的行为回归位于 `test_media_m3_review_regressions.py`。r1 的 10 条桌面中央联调继续沿用，不修改桌面生产文件。

修改实现前，真实隔离 PG 红测为 **4 failed / 4 passed / 4.76 s**，证明 r1 拒绝了301秒的原任务GET、已completed register结算，以及本应允许进入的24小时剩余预算/配置wall慢流GET。修正后新8条审查回归与原voice runtime/stages共 **39 passed / 21.62 s**。更宽M3定向集合为 **247 passed / 78.94 s**（原229 + 审查8 + 桌面10），见新候选manifest和focused.log；完整suite仍由根任务协调。

新增边界覆盖包括：301秒GET原ID成功且无额外POST；301秒已completed register仅补结算；301秒已completed upload不得新POST；24小时剩余100ms慢流GET及测试注入worker_wall 100ms的慢流GET及时终止、保留hold，过期后不再查询；原DashScope POST慢流截止与SQL事实校验反例继续通过。

证据目录：`/private/tmp/easel-m3-runtime-review-r2-20261028/`。`red.log` 为原始失败输出，`review-green.log` 为39条通过收据，`r2-vs-r1.patch` 为生产修改及回归差异。001–016/018、固定f50/016运维工具与原候选证据均不修改；017/019仍未部署。本批没有真实供应商请求、生产动作或商业费率发布。
