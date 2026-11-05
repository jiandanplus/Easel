# Easel v31 单次真实 UI 工具交付与结算验收（2026-10-28）

状态：root 已完成原工作台的一次真实 UI 文本工具验收，r4 离线核账退出0。本轮将明确白名单中的安全证据追加到 [live/acceptance-01](../../outputs/_run/workbench-v31-delivery-20261028/live/acceptance-01)，没有覆盖 [原base交付文档](2026-10-28-workbench-v31-delivery.md)或任何base receipt。原文的pending状态保留为创建时历史快照，随后结果以本独立增量为准。

## 单次提交与实际文件

本人在原 http://127.0.0.1:17868 重新登录同一账号后，root只点击一次UI Send，无自动重试。实际模型为 easel-deepseek-flash-internal-v1，包内Pi、subscription渠道。一次invocation包含两个工具续接operation，对应两笔live execution，均settled、结果available；两次SDK操作不等于两次用户发送。

任务只允许write_file创建 outputs/acceptance-v31-20261028/tool-check.txt，正文为EASEL_V31_TOOL_OK加一个LF，不覆盖已存在文件、不换路径或重试。root下载的副本经本归档者独立回读确认恰为18字节，SHA256：

aa5868805e04a9e21573cc0938288977dd71bcbc16de41e2c7a4a6195bc48097

[合成任务提示](../../outputs/_run/workbench-v31-delivery-20261028/live/acceptance-01/live-tool-prompt.txt)、[下载副本](../../outputs/_run/workbench-v31-delivery-20261028/live/acceptance-01/live-downloaded-tool-check.txt)、[最终UI截图](../../outputs/_run/workbench-v31-delivery-20261028/live/acceptance-01/live-final-settled.png)、[一次发送回执](../../outputs/_run/workbench-v31-delivery-20261028/live/acceptance-01/single-submit-safe.json)均按明确白名单保存。本归档者没有访问原用户工作区文件或重新下载网络产物。

## 终态、核账和准确的关联边界

[r4安全回执](../../outputs/_run/workbench-v31-delivery-20261028/live/acceptance-01/live-verified-r4-safe.json)SHA256为1718ce742600d67aaf22c8b84254132384b12d4f65eeb5802806eaeae7992677。确切会话/轮次为mvs6l1zfnszqfb / 18096fdf-fc77-4372-b151-01d2b0bcb829，completed、clean_end=true。accepted receipt与request hash均保留。[精确轮次绑定安全回执](../../outputs/_run/workbench-v31-delivery-20261028/live/acceptance-01/live-turn-binding-safe.json)绑定原origin、GET路由/query与捕获响应hash；响应本身不echo session/turn，边界依据为请求路由和审读过的server turn_key过滤，不能描述为响应自行携带了这两个字段。

两笔actual为1436与1864 microcredits，合计3300 microcredits = 0.003300点；可用余额6.938114→6.934814，差额完全一致。旧预留1.009728、pending_reconciliation 2不变，active_after=0；保留快照中的原51条usage行和金融字段不变。r4在root的原始私有输入上额外断言execution ID集合与新增wallet usage/reservation ID集合互斥。

钱包usage/reservation ID不等于execution ID。核账没有建立外键join，实际实现是rate_version、reserved amount、actual的multiset相等，加exact-turn execution与钱包净变化交叉印证。此结论不是逐笔数据库外键关联，也不是全历史或所有并发同owner活动的审计。51条旧行仅覆盖已保留的观察快照。

## 刷新与草稿、最终guard

[浏览器安全回执](../../outputs/_run/workbench-v31-delivery-20261028/live/acceptance-01/live-browser-safe.json)记录刷新后登录、同聊天/同模型、完成回复和下载链接保留；未发送合成草稿跨reload保留，验证后只清空此草稿，没有提交；未自动弹出Settings或Persona。原始AX与账号/配置数据保持私有，没有归档。

[最终guard](../../outputs/_run/workbench-v31-delivery-20261028/live/acceptance-01/live-final-guard-safe.json)记录后续只读查询及草稿刷新后钱包/usage未再变化，active_requests=0、旧pending=2；branch为codex/ui-redesign，HEAD为e972af07444e181e3eb737441ecac306bca778b3，diff check退出0，未commit/push/merge。v31 ZIP大小129745884字节，SHA256为f0e6e86581edb243496ce8e3ad8f3f40363e87e3e1a2c4a28fe2c6ace6c5f13c；归档时再次核对实际ZIP哈希一致。

## 核验器修正与独立审阅

初版[r1核验器](../../outputs/_run/workbench-v31-delivery-20261028/live/acceptance-01/live-verifier-r1.py)错误假定两个invocation并离线失败；这是核验器语义错误，没有因此重发模型。r2修正为一个invocation/两个operation；[r2独立审阅](../../outputs/_run/workbench-v31-delivery-20261028/live/acceptance-01/easel-v31-live-independent-20261028.r2.md)保留safe wording、余额派生、唯一usage及捕获来源等缺口。r3补齐主要guard，[r3限定审阅](../../outputs/_run/workbench-v31-delivery-20261028/live/acceptance-01/easel-v31-live-independent-20261028.md)仍指出ID互斥字段缺少显式assert；[r4核验器](../../outputs/_run/workbench-v31-delivery-20261028/live/acceptance-01/live-verifier-r4.py)仅追加该assert并排他输出新的r4 receipt，[r4独立增量审阅](../../outputs/_run/workbench-v31-delivery-20261028/live/acceptance-01/easel-v31-live-independent-20261028.r4.md)确认该静态差异。全部指定r1/r2/r3/r4脚本与各阶段审阅按原字节保留。

root报告r4在已有私有输入上离线exit0；独立审阅者没有执行/import核验器或读取私有raw输入，本归档者同样没有运行它们。独立审阅支持算法与安全回执一致性，不是对raw钱包/账号/恢复响应的另一轮独立重放。不得用这些离线修正叙事暗示新增模型提交、未知失败重试或更宽的金融审计。

## 增量清单与限制

[delta-manifest.safe.json](../../outputs/_run/workbench-v31-delivery-20261028/live/acceptance-01/delta-manifest.safe.json)记录18个精确白名单源文件的复制前/副本/复制后SHA256和字节数，三者逐一相同；含新README与delta manifest共20个文件。base manifest SHA保持920e5a92e4a05a2f125ecb540d328642b0d594561ea8f20531d6b3d1459958f7；原74个base文件和两份base交付docs均哈希不变。

本次只做本地归档及新live文档，没有扫描activation stage、读取私有JSON/AX/账号/配置/原工作区、访问网络/浏览器/模型/钱包接口、运行verifier、修改产品或主delivery plan。指定UI截图按root明确白名单收录，没有复制原始历史或AX文件。

结论只覆盖原机器这一条真实文本工具任务的可用回复、文件下载和观察区间内3300 microcredits已结算扣点。不覆盖独立干净设备、Gatekeeper/签名/公证、新音视频能力、正式付款、完整用户流程或全历史账务。
