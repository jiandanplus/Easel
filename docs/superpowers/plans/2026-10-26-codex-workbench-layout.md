# Codex Workbench Layout Implementation Plan

> **For agentic workers:** Use subagent-driven-development to isolate the history extraction from root layout integration, then review the result.

**Goal:** 将工作台改为工具栏、对话列表、内容区三列，历史不再在工具栏下方。

**Architecture:** `Sidebar` 保留全局导航和底部账号；`ConversationSidebar` 接收现有会话数据及回调并独立管理重命名/归档展开状态；`App` 以同级 flex 子元素排列两个侧栏与现有 main。结构CSS最后导入以统一五套主题，窄屏使用独立抽屉。

**Tech Stack:** Existing React/TypeScript/Vite, CSS theme variables, Node frontend regression runner, CUA browser verification. No new dependency.

## Global Constraints

- 当前 `codex/ui-redesign` 分支，不回滚既有脏文件，不合并主分支。
- 不改Agent、模型、认证、钱包、订阅、自定义接口和既有会话数据契约。
- 不发送聊天/模型请求；只使用合成会话验证交互。
- 桌面80px工具栏＋264px列表；760px以下列表抽屉；右侧min-width:0。

## Task 1: Conversation list extraction

Files: create `web/frontend/src/components/ConversationSidebar.tsx`; meaningful interaction coverage in `tests/frontend/conversationSidebar.test.mjs`.

- [x] 提取既有session渲染与操作回调，参数保留 `sessions`, `activeSessionId`, select/delete/rename/archive/new callbacks，另加 `mobileOpen`, `onClose`。
- [x] 9项新交互测试通过；验证重命名Escape不会触发失焦提交、Enter只提交一次、中文输入法Enter不提前保存，归档和选择使用原回调。
- [x] 列表独立滚动，移动打开聚焦关闭按钮，Escape关闭并由App恢复焦点；浏览器实测通过。

## Task 2: Global rail and sibling layout

Files: modify `web/frontend/src/components/Sidebar.tsx`, `web/frontend/src/App.tsx`, `web/frontend/src/index.css`; create `web/frontend/src/styles/workspace-layout.css`.

- [x] Sidebar仅保留导航/账号/状态/日夜切换，提供窄屏历史按钮。
- [x] App排成 `Sidebar → ConversationSidebar → main-content`，保留旧handler与右侧内容；导航/设置关闭抽屉。
- [x] 最后导入结构CSS，覆盖旧sidebar宽度与footer显示；用主题变量匹配A–E，窄屏不挤压主区。

## Task 3: Verification and visible delivery

- [x] 最终前端260 passed / 0 failed / 0 skipped，包含9项新组件交互测试；类型及Vite构建成功，3个改动TSX文件oxlint通过。首轮257/260的3项失败为旧VM测试缺少新状态stub，补stub并增加抽屉关闭断言后全绿。
- [x] 新源码预览使用127.0.0.1:5173并代理原17868 API；五主题实测同级80px/264px/剩余列且无横向溢出。窄屏默认隐藏列表，展开和Escape关闭/焦点返回通过。预览仅编辑未发送的空合成对话，未调用模型。
- [x] 独立审查通过，截图/安全回执已保存；83个前端输入与13个构建输出哈希验证通过，配置哈希不变，当前分支codex/ui-redesign，diff空白检查通过。

## 交付与边界

首阶段新布局在[http://127.0.0.1:5173/](http://127.0.0.1:5173/)完成源码预览；当时原17868仍是未修改的v10，也未重打原生包。两个前端地址的浏览器会话和外观存储独立，该阶段未复制或覆盖原聊天历史。此交付状态已由用户追加授权的Task 4替代：v11已独立重打包，原[http://127.0.0.1:17868/](http://127.0.0.1:17868/)已切到v11，v10仍保留。现有构建继续提示主JS块超过500kB，本批不做无关拆包。

首阶段安全回执：`docs/validation/2026-10-26-codex-workbench-layout-safe.json`。首阶段截图位于`docs/validation/assets/codex-workbench-layout-20261026/`，含A/C桌面和窄屏抽屉；原17868的v11截图为`docs/validation/assets/codex-layout-package-20261026/original-17868-v11.jpg`。中央后台登录的补充验收另记R1报告，与工作台布局证据分开。

当前交付与保全结果见[v11打包和原17868同步报告](../../validation/2026-10-26-codex-layout-package.md)及[最终安全汇总](../../validation/2026-10-26-codex-layout-package-safe.json)。本轮自有5173临时Vite已停止，原17868继续运行v11、18885和其他服务保留；首阶段5173地址不再作为当前运行入口。

## Task 4: 用户追加授权的 v11 打包与原17868切换

用户已明确要求同步原17868并重新打包，保留登录/历史/接口配置，继续当前分支。

- [x] 复核原v10自有实例PID15099、无运行任务或未完成登录；45个受保护文件已私有备份并验证哈希，记录原tab的可见列表/消息DOM摘要。
- [x] 新建v11源码权威，五个UI输入以外的公开源和构建输入保持；离线使用原固定Python/wheels/Pi payload生成独立新包，v10资源保持且未覆盖。
- [x] 前端260 passed、0 failed、0 skipped；Python相关回归154 passed、8 skipped。15251文件inventory、83个前端输入/13个输出及35798个ZIP条目CRC/hash通过。执行代码与Pi/SDK保持字节一致，六个RECORD差异仅为已移除生成bin入口的哈希项，已逐项核对。
- [x] 全新HOME/workspace、最小PATH、拒绝外网隔离smoke通过：内置Pi可识别、页面及静态资产200、正常自有停止、端口关闭，启动前后资源inventory一致。该smoke没有浏览器渲染或真实模型调用。
- [x] 相同HOME/workspace/origin正常切换原v10至v11；登录scope、钱包和45文件哈希保持，服务资产与新包一致。原tab刷新实测三栏，列表与消息DOM摘要前后一致，原forest偏好保持。
- [x] 独立打包、smoke、preflight、切换、浏览器及浏览器后保全安全回执和截图已保存；v10和私有备份保留，不合并主分支，模型调用为0。
- [x] root已写入最终打包/切换安全汇总并完成cleanup：仅停止自有5173临时Vite，原17868、18885及其他服务保留；浏览器保留原工作台tab1和后台tab15。

### v11精确交付与保全范围

包外提供本机“启动现有工作台.command”和“停止现有工作台.command”，固定原HOME/workspace/17868、不含凭据。脚本权限0700，语法检查通过，启动入口已验证复用当前PID47661且返回0；通用ZIP保持独立数据目录，不打包个人状态。当前验证工作区位于临时目录，本轮未执行长期目录迁移或整机重启验收。

包为`outputs/_run/pi-portable-20261026-v11/Easel-Pi-macOS15-arm64.zip`，124873946字节，SHA256为`707443f83e788b92f04587d3ee340822c18605ec0253b4c52d0fe599eb5bafc9`。应用为未签名macOS 15+/arm64包，v10保留。原17868已成功切换，当前自有PID为47661；首次切换脚本曾因package字段`KeyError`在任何stop前失败，确认原PID15099仍在后修正字段读取，随后切换成功，失败记录不覆盖。

前端260项与Python154 passed/8 skipped为各自独立轮次。后续对实际包运行资源执行的6项SDK及2项Python补验共8 passed、0 skipped，也是独立轮次，不能与154项相加冒称162项同轮通过。

原17868浏览器保全只覆盖可见列表1项、活动对话1个及4条消息：列表与消息DOM哈希前后相同，三栏同为`app-layout`直接子元素，宽度约80px、264px和剩余空间，主题仍为forest。没有浏览器storage全量哈希API，因此不能扩展为完整浏览器存储审计；没有导出聊天正文。原storage逻辑字节保持，同源同tab未重置；浏览器后再次核对45个受保护文件及15251个包资源均保持。

本轮安全回执保留在`/private/tmp/easel-v11-20261026`：`package-safe.json`、`clean-smoke/safe-receipt.json`、`preflight-safe.json`、`upgrade-safe.json`、`browser-upgrade-safe.json`、`post-browser-preservation-safe.json`与`cleanup-safe.json`。最终公开安全汇总SHA256为`aca55320efc1642afc1ba053189f96daf02e9c4ff01b188063f40510bfa2821f`，状态为`passed_for_v11_ui_package_and_17868_preservation_scope`。本次无新真实模型或扣点验收，继续使用`codex/ui-redesign`且不合并main；当前Task 4授权交付已完成，storage完整哈希、签名/第二设备及真实模型验收仍不包含在本阶段结论中。
