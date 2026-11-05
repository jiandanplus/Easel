# Easel v32 过程快照与验证记录（2026-10-29）

v32已完成产物引用修复、实际匿名App／浏览器产物卡验证，并曾切换到原17868工作台；真实任务的文件与扣点已核对。**v32仍有浏览器完整状态查询超时缺口，不能作为最终可交付版本通过。** root随后发现该页面的固定20秒总预算会取消“登录状态→文本恢复→媒体恢复→再次账号校验”的慢链路，显示“查询超时”；正在由v33独立增量修复。本文保留v32阶段事实，不覆盖或提前声明v33结果。

## 修复了什么

模型正文末尾的`.txt`碰巧命中凭据前缀保护后可能变成`.tx[已隐藏]`，旧前端据此生成错误链接。v32保留原凭据保护和模型文字，由当前成功host write_file在真实文件描述符写入后通过不可变隐私快照与字节比对，生成准确路径、大小和SHA256；Pi仅采信当前host结果，Web绑定session／turn并在SSE和快照中保存。前端用独立引用生成现有产物卡，保留复制／下载／继续操作，未添加解释段落；含隐藏标记的裸URL、自动链接及Markdown目标在最终HTML边界失去链接能力。

恢复查询的文本客户端同时从单次2秒、共享10秒改为单次最多10秒、共享30秒。直接HTTP读取能通过，但上述浏览器完整流程仍受20秒整体取消限制；这两个结论必须分开。

## 包与本地回归

[macOS15 Apple Silicon过程包](../../outputs/_run/pi-portable-20261029-v32/Easel-Pi-macOS15-arm64.zip)为129,749,008字节，SHA256 `648f2858e767955d04125cca9c5dcca578afb00d9df759bd7b7c45acd1630917`，未签名、未公证。[归档目录](../../outputs/_run/workbench-v32-delivery-20261029)和[复制清单](../../outputs/_run/workbench-v32-delivery-20261029/copy-manifest.json)保存精确白名单证据。本归档者对ZIP实际流哈希核对一致，没有解包、扫描App或重跑测试。

root的[包回执](../../outputs/_run/workbench-v32-delivery-20261029/validation/package-r3.safe.json)核验15,402资源文件摘要、ZIP全部CRC、13份前端输出与100份当前前端来源。ZIP共有36,118成员，包含目录，不能称为36,118普通文件。904个冻结源码记录相对v31有11项变化；原59项构建输入不变，因本轮authority冻结在packaging目录外，额外记录了1份旧公开`packaging/pi/source-manifest.json`元数据，未改用旧源码执行。两次核验器路径假设的纠正保留在回执说明，包本体未因此修改。

[前端完整回归](../../outputs/_run/workbench-v32-delivery-20261029/validation/frontend-full-r2.log)为571passed／0failed；[定向后端回归](../../outputs/_run/workbench-v32-delivery-20261029/validation/backend-integration.log)为120passed；[前端构建](../../outputs/_run/workbench-v32-delivery-20261029/validation/frontend-build.log)通过。作者定向测试、独立小型probe和完整回归有重叠，不累加为另一套总数。[后端](../../outputs/_run/workbench-v32-delivery-20261029/reviews/backend-independent.md)与[前端r2](../../outputs/_run/workbench-v32-delivery-20261029/reviews/frontend-independent-r2.md)限定独立复审均通过；它们不证明随后浏览器完整状态流程通过。

## 两类实际验证

[匿名合成App验证](../../outputs/_run/workbench-v32-delivery-20261029/offline/offline-tool-safe.json)使用真实v32包内Pi SDK和host工具，本机合成供应商返回固定工具调用。一次CUA发送得到1个artifact事件，SSE重放与snapshot引用一致，31字节实际文件和下载SHA均为`a968dd5e49df8d7d3633eedcabc966f23052e2a4ec717d744c960911c79840cb`。[刷新后截图](../../outputs/_run/workbench-v32-delivery-20261029/offline/ui-refreshed-card.png)显示准确tool-check.txt卡片、1个正确下载链接，三条含隐藏标记的模型路径均无活动链接；原模型文字保留。该实例自有身份停止与原句柄等待均退出0，App／mock端口关闭。该验证没有付费供应商或真实账号／钱包请求；其网络边界不等于独立追踪了所有其它回环端口尝试。

[真实任务核账r3](../../outputs/_run/workbench-v32-delivery-20261029/live/live-verified-r3.safe.json)对应原17868工作台此前在v31完成的一次DeepSeek Flash／Pi订阅任务。1次UI发送、1个invocation、2个operation／execution已结算，1326+1622=2948microcredits，即0.002948点；可用余额6.934814→6.931866。25字节实际下载SHA为`baabfc84ffffef48e9ca69aa647e31c6cf631893b851cec774a0079fe49ee298`；观察到的原53条记录保持不变，旧预留1.009728点、待核算2笔和执行中0保持。未发新点数、延期、改账本或重发模型。

v32部署后root另外捕获[同轮HTTP路由／query／响应hash绑定](../../outputs/_run/workbench-v32-delivery-20261029/live/http-recovery-binding-v32.safe.json)，两笔均settled；钱包和55条记录在切换及只读读取后未再变化。HTTP200和精确绑定不等于浏览器完整查询通过。真实旧轮次没有回填新artifact协议，也没有改写其被隐藏的旧回复。

[切换回执](../../outputs/_run/workbench-v32-delivery-20261029/activation/activation.safe.json)记录同一工作区、同17868端口，174个受保护普通文件无变化，账号语义、对话历史与自定义配置保留。此前15,554,049字节、902个普通文件的私有备份经复制前／后核对；这里只有[安全备份回执](../../outputs/_run/workbench-v32-delivery-20261029/activation/backup.safe.json)，未复制真实备份或工作区。切换未迁移profile，旧App／ZIP／启动器保留。此为当时过程快照，后续运行状态以新版本增量为准。

## 核账与归档边界

root报告[核验器r3](../../outputs/_run/workbench-v32-delivery-20261029/live/verify_live_r3.py)在其私有输入上运行退出0。本归档者仅静态阅读该脚本及safe／binding，没有运行或import核验器，也没有读取raw账号、钱包、历史或认证内容。r3明确检查确切GET路由、query与响应hash；响应本身不echo session／turn，原UI点击观察也没有turn ID，来源关联仍依赖运营者捕获与已审服务器过滤的前提。[独立r2审阅](../../outputs/_run/workbench-v32-delivery-20261029/reviews/live-verifier-independent-r2.md)仅审旧r2算法／safe，不能称为另一轮r3 raw重放。execution ID与wallet reservation ID不同，金额／费率multiset和钱包净变化只是交叉印证，不是逐笔数据库外键关联或全历史账务审计。

归档仅复制33个明确白名单文件，共311,081字节；加README和manifest共35文件。33项复制前／副本／复制后SHA逐一相等，目录文件集合闭合。App／ZIP本体、真实raw、账号／配置／历史、backup、真实工作区与AX均未复制或扫描。后续final guard与v33材料须作为新白名单增量保存，原manifest和此阶段证据保持不变。

独立音视频Linux单次运行在来源处理前被kernel gate拒绝，尚未验签、提取或编译；root报告原4个服务、3个容器及3份公开输入保持，未重试。该事项不计入v32文本／UI通过范围。干净设备、Gatekeeper、正式签名／公证、正式付款、完整音视频供应商及整体验收仍无本记录的完成声明。
