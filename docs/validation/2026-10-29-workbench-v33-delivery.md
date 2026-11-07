# Easel v33 文本工具与恢复状态验证（2026-10-29）

**v33已在原17868工作台完成本轮恢复状态流程验收。** 同一真实任务页面显示两行“已结算／结果可用”，扣点分别为0.001326与0.001622，查询结束后可再次查询，没有“查询超时”或媒体错误。此次关闭的是v32完整查询链路的20秒总预算缺口；[v32过程快照](2026-10-29-workbench-v32-delivery.md)及其失败记录保留，不覆盖为成功。

## 修复内容与交付包

v33将“登录状态→并行文本／媒体恢复→再次账号校验”的单一有限总预算改为90秒，媒体恢复读取采用30秒共享预算、单次最多10秒及剩余预算限制。原前后账号签名、Scope、目标轮次、账户边界、Abort和晚到结果拒绝规则保留，没有增加模型提交或付费重试，也没有新增UI解释段落。[实现记录](../../outputs/_run/workbench-v33-delivery-20261029/implementation/recovery-budget-chain.md)及[独立限定审阅](../../outputs/_run/workbench-v33-delivery-20261029/reviews/recovery-budget-independent.md)给出准确边界。

[macOS15 Apple Silicon ZIP](../../outputs/_run/pi-portable-20261029-v33/Easel-Pi-macOS15-arm64.zip)为129,749,097字节，SHA256 `6af10003c9b176ae9fb8ecbf03ba77d0a66e20a2dcf1fed420f121d82b91a195`。最低macOS15，未签名、未公证。本归档者实际流式核对ZIP摘要，没有解包、扫描App或重新运行测试。

[root包回执](../../outputs/_run/workbench-v33-delivery-20261029/validation/package.safe.json)核验15,402个资源记录、ZIP全部CRC、13份前端输出与100份前端来源。904份冻结源码记录相对v32仅`easel/platform/media.py`与`web/frontend/src/components/ConversationRecovery.tsx`不同；本归档者通过两份公开来源清单再次比较，确认其余902份来源和60项构建输入相同。root另记录11,932个Pi文件保持一致，凭据前缀保护与原产物引用核心字节不变。ZIP共36,118个成员，其中15,407个是payload非目录成员，17,174个是AppleDouble／resource metadata非目录成员；不能把32,581个非目录成员全部当作应用payload文件。[成员分类回执](../../outputs/_run/workbench-v33-delivery-20261029/validation/zip-member-kinds.safe.json)单独保存。

## 本轮回归与原工作台验证

[root完整前端回归](../../outputs/_run/workbench-v33-delivery-20261029/validation/frontend-full.log)为573passed／0failed，tsc及[Vite构建](../../outputs/_run/workbench-v33-delivery-20261029/validation/frontend-build.log)通过；作者定向媒体／认证回归为17passed，独立静态审阅Spec／Quality均PASS。前端虚拟45秒链路可完成、90秒停滞链路仍会终止并拒绝晚到结果；媒体虚拟慢读取保留共享30秒预算与账号校验。这些定向、完整与v32基线测试有重叠，分别陈列，不合并为另一套总数。归档者只核对已给定日志和报告，没有重跑套件。

[实际浏览器安全回执](../../outputs/_run/workbench-v33-delivery-20261029/activation/browser-final.safe.json)记录原origin `http://127.0.0.1:17868`，会话`mvsy5q2r3knvdz`和轮次`e68be990-da7d-4196-85c6-d35b2a5fd8af`完整状态查询成功：2条已结算、2条结果可用，展示0.001326与0.001622点，无超时／媒体错误。账号菜单可用，DeepSeek Flash模型选择就绪，原历史和回复保留；本次UI操作是只读状态查询。真实AX与原工作区截图保持私有，归档仅包含它们的安全摘要和hash，没有读取或复制raw。

[最终guard](../../outputs/_run/workbench-v33-delivery-20261029/activation/final-guard.safe.json)记录页面HTML匹配v33，同一账号仍authenticated；只读UI查询后钱包与55条观察记录完全不变，可用6.931866点、旧预留1.009728点、待核算2笔、执行中0。原174个受保护普通文件没有变化，账号语义、历史与自定义配置保留，正常令牌刷新被允许但不等于退出／替换账号；当前904份来源仍与冻结包匹配。v31和v32 ZIP保持，分支为`codex/ui-redesign`，HEAD为`e972af07444e181e3eb737441ecac306bca778b3`，diff检查退出0；本轮没有commit／push／merge，也没有新增真实模型发送、发点或资金改动。

[同源切换回执](../../outputs/_run/workbench-v33-delivery-20261029/activation/activation.safe.json)仍使用原工作区与17868端口，没有迁移profile，旧App／ZIP／启动器保留。[备份安全回执](../../outputs/_run/workbench-v33-delivery-20261029/activation/backup.safe.json)记录15,554,049字节、902个普通文件的私有备份已经核对；真实备份和工作区没有复制到本归档。

## 真实模型、产物卡与证据继承

真实模型验证仍是此前v31原工作台的一次UI发送：Pi、DeepSeek Flash、订阅渠道，1个invocation包含2个operation／execution，1326+1622=2948microcredits，即0.002948点；可用余额6.934814→6.931866。原25字节下载SHA为`baabfc84ffffef48e9ca69aa647e31c6cf631893b851cec774a0079fe49ee298`。v32基线的[r3安全核账](../../outputs/_run/workbench-v32-delivery-20261029/live/live-verified-r3.safe.json)检查原53条观察记录不变、两条新增结算及钱包净差；v32／v33后续版本切换和恢复读取没有再次发送该模型任务。

v32的[实际匿名App／UI产物卡证据](../../outputs/_run/workbench-v32-delivery-20261029/offline/offline-tool-safe.json)证明合成供应商→实际包内Pi→host写文件→artifact SSE／快照→31字节下载、准确卡片和刷新保留。v33与该管线有关的核心来源字节不变，本轮明确继承此基线，不把旧的真实模型轮次描述成使用了新artifact协议，也未回填metadata或改写其被遮蔽的原文。订阅和自定义配置保留；实际custom App工具流程的证据来自上述v32隔离验收。

root此前在私有输入上运行r3核验器退出0；该绑定依赖运营者捕获的确切HTTP路由／query／响应hash和已审服务器turn过滤。HTTP恢复响应本身不echo session／turn，旧UI点击观察也没有turn ID，不能补造更强的来源证明。execution ID与wallet reservation ID不同，金额／费率multiset及钱包净变化是交叉印证，不是逐笔数据库外键关联或全历史审计。旧独立r2静态审阅没有被描述为r3 raw重放；本归档者没有执行或import核验器、读取raw账号／钱包／历史。

## 归档清单与未覆盖项

[本轮增量目录](../../outputs/_run/workbench-v33-delivery-20261029)仅复制14个精确白名单文件，包含最终browser-safe和guard；每项复制前／副本／复制后SHA逐一一致。连同README和copy-manifest共16个文件，文件集合闭合。[v32 base manifest](../../outputs/_run/workbench-v32-delivery-20261029/copy-manifest.json)的SHA保持`96c68934ada903418dad49789f36545144e50432cb9c527d90284e1164d720b8`。App／ZIP本体、真实workspace、backup、账号／配置、raw钱包、AX及真实截图均未复制或扫描；归档者未执行外部操作、访问网络、浏览器、SSH、模型或钱包。

本记录确认原机器上的文本工具交付与恢复状态修复。独立干净设备、Gatekeeper、正式签名／公证、正式付款及完整音视频供应商不在本轮完成声明中。独立音视频Linux一次运行仍在来源处理前被kernel gate拒绝，尚未验签／提取；它不计入本轮通过。
