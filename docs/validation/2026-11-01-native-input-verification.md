# macOS 原生输入与剪贴板验收

范围：剩余交付计划中的系统剪贴板、中文 IME 和 VoiceOver，分别保留证据来源，不以浏览器 DOM 或协议测试替代系统交互。

## 系统剪贴板：用户手动 3/3 通过

2026-10-31，用户在独立 Chrome 访客合成页面中亲自执行用户消息、助手回复、交付文档的真实复制按钮，再按 macOS ⌘V；明确回复“三项都正确，后两项换行保留”。U/A/D 文本及 A/D 换行均由用户确认。

使用 v36 包内 13 个原始前端资源，JS/CSS/图片/SVG 未改写；仅 HTML 插入合成数据 seed。测试服务无模型、账号、账本或其他后端 actor。最终业务 POST 接受数和 fixture 外发分发数均为 0，文档 GET 为 1。专用标签页、访客 Chrome 和合成服务已正常退出。

证据：[验收记录](../../outputs/_run/workbench-v36-delivery-20261031/native-clipboard-actual-20261031/REPORT.md)、[结构化结果](../../outputs/_run/workbench-v36-delivery-20261031/native-clipboard-actual-20261031/result.safe.json)、[七文件清单](../../outputs/_run/workbench-v36-delivery-20261031/native-clipboard-actual-20261031/manifest.safe.json)、[独立复核](../../outputs/_run/workbench-v36-delivery-20261031/native-clipboard-actual-independent.safe.md)。清单 SHA256：`50a396ab04e1eb0b25d17f675cca2f52bc86828ebe2387b392542a185afaecb4`。

代理没有直接观察手动粘贴文本或取得其截图，也没有读取系统剪贴板。自动化原生通过数为 0/3：原生工具不能可靠区分两个 Chrome 实例，Tab 快捷键走虚拟剪贴板。普通专用标签页截图只记录合成界面，不能作为访客窗口粘贴证据。人工结果与自动化限制分别保留；不将工具限制归为产品复制失败。

## 中文 IME：当前 macOS／Chrome 系统拼音原生按键用例通过

当前 ChatPage 已有组合输入 ref、原生 `isComposing` 和 `keyCode===229` 的回车保护，并有组件合同测试；这些不是原生输入法验收。拟用原始 v36 前端、可正常发送的合成模型目录和只计数的本机请求端点，先确认原生候选词回车及 Shift+Enter 不提交，再以一次普通 Enter 验证确实能发出合成请求。

验证载体需求在 [task brief](../../outputs/_run/native-ime-preparation-20261101/task-brief.md)。已完成12项纯测试（Root实际0.059秒／0失败）、全部代码与固定资产pin核对，并经[独立复核](../../outputs/_run/native-ime-preparation-20261101/independent-review.safe.md)，未发现确定P1/P2。浏览器方法按[controller addendum](../../outputs/_run/native-ime-preparation-20261101/controller-browser-addendum.safe.md)采用精确绑定的专用Chrome标签页／新随机origin，不声明独立profile或cookie隔离，不清除其他origin存储。

实际服务已启动，专用页面为 `http://127.0.0.1:50859/#/chat?session=native-ime-v36-qa`。已直接观察原始v36页面的nonce、合成模型选择及非空文字时发送按钮可用；Root仅用公开“发送预检”验证可用性，再清空，未发送。此为非原生预检，不计作IME通过。预检服务端 stream POST=0、有效合成提交=0、模型／外发=0；[安全状态](../../outputs/_run/native-ime-actual-20261101/preflight-server.safe.json)与[页面预检](../../outputs/_run/native-ime-actual-20261101/ready-preflight.safe.json)已保存。

用户已回复“已输入两行，没有自动发送”。随后对精确绑定Chrome标签页134797899的只读核对发现：nonce及地址正确，但唯一textarea仍为空，长度0／换行0／发送禁用。已连接Chrome与应用内浏览器中，同一50859地址只定位到这一个标签页；不能推断用户在其他未连接窗口中的输入结果。

同一服务仍为stream POST=0、有效合成提交=0、模型／外发=0。用户陈述与当前页面观察未能对应，故候选阶段记为**证据不一致／待定位窗口**，不记通过，也不直接认定产品丢字或误发送。已单独询问实际浏览器／端口／窗口，未要求重输、未填入预期两行、未发起正控。见[不一致诊断](../../outputs/_run/native-ime-actual-20261101/candidate-inconclusive.safe.json)。

随后使用当前origin限定的CUA `Page.bringToFront` 成功激活精确Tab，原生Chrome App也显示同一50859／会话和焦点输入框。Root由此直接完成系统原生按键用例：拼音组合态产生系统分隔符；组合期间Return确认Latin且POST0；原生拼音＋Space选出中文，Shift+Return换行，第二行原生拼音＋Space，AX／DOM／截图均为精确`中文输入验收\n第二行`，发送可用且POST0。原先将Return笼统称为“选择中文候选”的说明已纠正；当前源使用Space选择中文。

一次正控按键被窗口状态保护中断，先查得POST0／valid0；重新确认精确窗口、新鲜原生状态和可发送的完整两行后，实际确认执行一次普通Return，得到**POST1／valid1／公开正文SHA匹配／换行1**。固定HTTP400是合成服务的无派发观察响应，模型／外发及业务受理均为0，没有重试或第二条网络POST。此证据来自原生OS按键自动化，不是DOM赋值、复制粘贴或注入composition事件；人工先前报告仍单独保持inconclusive，不改写成人工通过。

专用Tab已关闭，核对PID61457参数后正常SIGTERM；执行session18001实际exit0，最终状态stopped，计数仍1／1。完整[实际报告](../../outputs/_run/native-ime-actual-20261101/REPORT.md)、[结果](../../outputs/_run/native-ime-actual-20261101/result.safe.json)和[21文件清单](../../outputs/_run/native-ime-actual-20261101/manifest.safe.json)已冻结，清单SHA `1a3b42e592cf1890f2e007ebff35abb5224022949536bff6adf9815e236893ef`。[证据独立复核](../../outputs/_run/native-ime-preparation-20261101/actual-evidence-review.safe.md)未发现确定P1/P2，核对所有大小／hash、截图及同一服务0／0→1／1；这是记录复核，不是独立重演原生操作。

此结论限定为当前macOS／Chrome／系统拼音技术用例；物理人工输入、其他环境及整体目标用户验收不由它推导。没有修改产品源码、重新打包或操作原17868。

## VoiceOver：未完成

可访问性树、标签、焦点和普通键盘操作均不等于 VoiceOver 实际朗读及导航结果。没有新增此项通过记录。

已准备精确v36只读合成页（60529），全部变更接口关闭、无后台／模型actor。普通CommandF5已尝试但未取得实际读屏输出；原生工具明确不支持Fn键。已打开系统旁白实用工具，只读核对原字幕面板偏好为开启、欢迎对话开启、AppleScript控制关闭，未更改这些设置，并恢复原分类界面。进程名／常规应用清单不足以单独证明系统读屏开关，因此原先“关闭”的推断保留为不确定。

已请求用户用物理键盘确认当前读屏及原状态：若已在朗读不要再切换；否则用CommandF5或FnCommandF5及欢迎框Return开启。获得真实状态后才继续字幕／焦点／操作验证，并按原状态恢复，不重复盲切。见[受控计划](../../outputs/_run/native-voiceover-preparation-20261101/PLAN.md)与[设置观察](../../outputs/_run/native-voiceover-actual-20261101/setup-observation.safe.json)。当前VoiceOver仍未验收通过。

## 发行边界

### 2026-11-02 旁白续验

用户已明确回复“原本关闭，现已开启并听到旁白”。这证明其人工启动观察，不证明页面控件验收或当前读屏状态。原 60529 服务连接拒绝、原进程句柄缺失且 PID94607 不存在，旧 Chrome Tab 也已消失，故旧状态文件不再作为存活证明。

Root 在新的私有目录启动完全相同的 server／seed，原始 v36 资源全部通过固定校验；新回环端口 54779、PID45784、执行句柄24755、Chrome Tab134798025。只读固定状态证实运行中、业务受理0／外发0。精确激活后原生窗口显示同一合成页；一次原生操作链出现 ScreenCaptureKit -3811，随后 DOM 观察为空 composer 且焦点正确。截图只证明合成页面和焦点，不能证明旁白字幕或声音；1795秒原生工具获取延迟也不归因于产品性能。

已请求用户在该页手动完成四项：输入框听读、文档与历史控件名称、一次旁白复制激活、模型菜单 Escape 与焦点返回，随后关闭旁白恢复原状态。结果和实际恢复尚待回复。新证据位于 `outputs/_run/native-voiceover-actual-20261102/`，不覆盖旧失败记录。本次没有改变旁白偏好、权限或真实工作台，也没有调用模型。


用户随后回复“四项都通过，旁白已关闭”（原答复为“4 项都通过，旁白已关闭”）。此为人工报告；Root后查固定服务无文档GET，按钮仍“复制文案”且焦点为composer，未对应复制及菜单焦点结果，因此完整旁白验收保持inconclusive，不推断产品失败，不标记全部通过。用户确认已恢复关闭，Root未独立观察系统开关。已关闭专用Tab，核精确PID后正常SIGTERM，原句柄exit0且落盘stopped。模型／业务受理0。见[续验报告](../../outputs/_run/native-voiceover-actual-20261102/REPORT.md)；不要求用户立即再开启旁白。

本记录不证明原 17868 已更新至 v36、不替代登录／历史／配置保全、不证明真实模型／工具／扣点，也不关闭永久工作区、签名公证、净机、Linux／媒体／恢复或完整发行门。仍在 `codex/ui-redesign`，未合并主分支。
