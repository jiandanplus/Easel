# Easel 普通创作者任务体验实施拆分与验收计划

> 本文是待确认方案的实施拆分，不是已获授权的代码执行记录。后续逐任务实施时使用 executing-plans；用户明确要求并行后才启用子代理。每批独立评审和验收。

**Goal:** 打通普通创作者的开始、补充、执行、恢复、修改和交付路径。

**Architecture:** 保留工具栏—对话历史—创作区结构，复用当前 FastAPI、本地会话、jobs 事件和中央恢复记录。增量补充版本/提交确认/产物契约，集中草稿、导航和任务状态规则，不改供应商/账号归属和账务规则。

**Tech Stack:** 现有 React/TypeScript、FastAPI/Python、node:test、pytest 与实际浏览器行为验收；不预设新增基础框架。

**Design:** [完整方案](../specs/2026-10-26-creator-task-flow-design.md)。所有用户行为、文案、契约和验收条件以该方案为准；实施前确认方案及当前工作区差异。

## 全局约束

- 保留三栏与现有主题，不自动切换服务来源/模型/账号。
- 不重发结果待确认的请求；只读查询和新执行分开。
- 不把 done 当成功并已结算，不把“CLI 已安装”当真实推理验证。
- 草稿按工作区/账号来源/会话范围隔离，凭证不进入草稿或 URL。
- 现有用户文件、会话、未提交代码及配置保留；实施只处理本批明确文件。
- 页面无证据不显示百分比、费用、已保存原稿或完成下载。
- 所有步骤的产物均需代码测试和可观察行为证据；测试数量不替代任务验收。
- 当前工作区有用户改动，开始代码工作前重新确认分支、diff、相关 AGENTS 和构建输入；需要隔离时先复用合适 checkout/worktree。

## A1. 版本兼容与统一就绪判定

涉及：web/app.py、web/frontend/src/App.tsx、components/DashboardPage.tsx、components/ChatPage.tsx、components/ComposerModelPicker.tsx、lib/api.ts、lib/chatModels.ts；拟新增 lib/workbenchReadiness.ts、components/TaskStatus.tsx。

消费：现有 /api/status、/api/chat/models、ChatModelCatalog 和服务来源设置。

交付：统一 readiness 结果（可提交/正在检查/连接失败/需设置/需确认/来源变化/版本不兼容），含明确原因与允许动作；所有入口共用。

- [ ] 记录当前源码和运行资源，建立“原实例旧接口、源码实例新接口”的复现夹具，不重启用户服务。
- [ ] 增量定义并测试状态响应的契约版本、构建标识和 workspace scope；旧字段保持兼容。
- [ ] 先验证：首页与对话对目录 404、超时、过期、账号变化返回相同可执行决定。
- [ ] 实现就地原因和恢复动作；普通目录刷新仅在权限、来源、模型仍合法时保持选择。
- [ ] 浏览器输入真实草稿后模拟上述故障，验证恢复不丢内容、不提交额外任务、不跨来源选择。
- [ ] 提供修改前后截图和接口回执，再进入 A2。

重点测试扩展：tests/frontend/composerModels.test.mjs、tests/test_chat_models.py；新增 tests/frontend/workbenchReadiness.test.mjs、tests/test_workbench_contract.py。

## A2. 提交确认、原请求查询与任务动作

涉及：web/app.py、web/frontend/src/lib/api.ts、lib/store.ts、App.tsx；拟新增 lib/taskLifecycle.ts；复用 easel/platform/execution_records.py 及 CentralExecutionRecovery.tsx 的范围检查。

消费：clientTurnId、现有 jobs 文件与事件、last 快照、clean_end/stop_reason、中央 operation/invocation 标识。

交付：可校验 accepted 确认、按精确 turnId 的有界状态查询、查询/续接/停止/重新执行的独立动作许可；查询永不触发生成。

- [ ] 先测试确认丢失、重复 ID、相同 ID 不同内容、停止回执晚到、中央账务待确认。
- [ ] 后端验证并持久记录后才发 accepted；把用户可见停止状态绑定真实确认。
- [ ] 补精确单轮查询，不按最后一条或模型名猜任务；缺日志时保留 unknown。
- [ ] 将原任务查询放回任务上下文，保留现有设置恢复入口，不能关联的旧记录明确降级。
- [ ] 连续刷新和断线重连，核对 taskId、事件游标、实际下游分发次数以及原消息/产物保留。
- [ ] 对收费路径仅在已授权测试渠道执行；输出本地、受控供应商、实际渠道的独立证据。

重点测试扩展：tests/frontend/chatRetrySafety.test.mjs、centralExecutionRecovery.test.mjs、tests/test_chat_retry_safety.py、tests/test_central_execution_recovery.py；新增 tests/frontend/taskLifecycle.test.mjs、tests/test_chat_submission_receipts.py。

## B. 草稿、附件引用与导航连续性

涉及：App.tsx、ChatPage.tsx、DashboardPage.tsx、ConversationSidebar.tsx、lib/store.ts；拟新增 lib/composerDrafts.ts、lib/workbenchNavigation.ts。

消费：A1 的 workspace/账号来源范围，A2 的 accepted 确认、turnId、任务状态。

交付：DraftEnvelope v1；读取、更新、持久化、恢复、按已提交 revision 确认清理的接口；hash 路由与旧 outputs 链接兼容。

- [ ] 先用序列测试覆盖 r 提交后写 r+1、r 确认晚到，断言 r+1 不被清空。
- [ ] 实现输入短延迟保存、主动离开前刷新、附件引用归属/存在校验、持久化失败的真实提示。
- [ ] 恢复工作区/会话范围；账号变化保持原草稿，不自动带到新身份。
- [ ] 实现页面/会话/成品/返回位置的 URL 状态，兼容旧链接，不把正文与绝对路径写入新 URL。
- [ ] 使用三个约束、两个素材实测切页、切会话、刷新、浏览器前后退、结果预览返回。
- [ ] 实测存储受限、文件不存在、跨标签占用与不同工作区，保留可读反馈且不串数据。

新增重点测试：tests/frontend/composerDrafts.test.mjs、workbenchNavigation.test.mjs；扩展 conversationSidebar.test.mjs。证据必须包含附件实际请求字段和 taskId，不能只看输入框截图。

## C. 文案成品取出及可访问预览

涉及：OutputsPage.tsx、ArtifactGallery.tsx、MessageBubble.tsx、ChatPage.tsx、lib/artifacts.ts、lib/api.ts、web/app.py；拟新增 DeliverableActions.tsx。

消费：精确 turnId、后端验证后的文件引用、现有安全输出/媒体路由、B 的来源导航与草稿。

交付：文档复制、原文件下载、来源会话返回、继续修改的目标引用；键盘可用的文件卡片和预览。

- [ ] 先覆盖文本文件存在/不存在/无权限、预览解析失败、复制失败、同名不同路径、没有文件只有回复。
- [ ] 在现有文件预览与对话成品处复用交付操作；复制文档内容，不混入执行说明。
- [ ] 下载独立于预览成功，验证文件名、响应和原件哈希；页面准确表达“已开始下载”。
- [ ] “继续修改”绑定目标文件和来源会话，不能通过猜最新文件拼接上下文；无来源时明确新建。
- [ ] 卡片使用链接/按钮，抽屉补名称、焦点范围、Escape、背景不可交互和关闭焦点归还。
- [ ] 浏览器完成原稿→修改稿→预览→复制到空编辑器→真实下载→返回修改；保留原稿哈希。

重点测试：新增 tests/frontend/deliverableActions.test.mjs、outputPreviewKeyboard.test.mjs，扩展现有 artifacts/outputs 相关测试；后端补文件关联、下载与访问边界测试。

## D. 首页、任务文案与必要信息补充

涉及：DashboardPage.tsx、ChatPage.tsx、MessageBubble.tsx、QuestionCards.tsx、ConversationSidebar.tsx、navigation.ts、现有样式 token。

消费：A 的 readiness 与任务状态、B 的草稿/位置、C 的成品操作。

交付：输入优先的首屏；分开的加载/空/失败；可理解且有证据的任务阶段；保留原入口与三栏。

- [ ] 调整首页顺序，保留欢迎图和统计但降低权重，不扩展新功能卡片。
- [ ] 示例只填草稿，空输入进入创作，有输入经过统一校验提交；默认值不静默换来源/模型。
- [ ] 技术活动收进详情，用户区只展示真实可确认阶段；无阶段事件使用中性处理文案。
- [ ] 已有问答能力按缺失的关键事实提问；普通文本追问不伪装成结构化系统等待。
- [ ] 加载/空/失败逐项验证，完成与停止不混淆，文件/费用状态分别表达。
- [ ] 小样本先确认入口理解和术语，再决定是否全站改导航名称。

重点测试：扩展 dashboard/navigation/theme 相关测试；浏览器验证桌面及窄屏内容层级。不得用快照里出现文案替代实际任务可操作性。

## E. 组合验收与发布准备

- [ ] 跑项目要求的后端/前端相关测试，以及前端 build/lint；最后统一回归现有重试、权限、账号范围和打包检查。
- [ ] 浏览器测同一旧书市集任务：完整事实首次生成、遗漏事实追问、附件修改、任务中刷新、取消、故障查询、取回新版、复制/下载。
- [ ] 全链路真实键盘、VoiceOver、中文 IME；实际 320 CSS px、200%/400%缩放、主题对比度和触摸目标检查。
- [ ] 5 位目标用户按方案第 11 节进行任务验证，记录操作与原话，不估算总体转化率。
- [ ] 生成前后端构建/契约对照回执，在匹配的打包运行实例重新验收，不能只验开发服务。
- [ ] 准备兼容顺序与回滚验证：旧记录可读，新草稿保留，文件不被清理，原请求仍可查询。
- [ ] 经明确发布授权后才部署；先兼容后端，再匹配前端，重新检查生产来源与真实渠道可用性。

建议基础命令（执行时先核实项目脚本与当前依赖，不能在本方案阶段声称已运行）：

- `rtk proxy node --test tests/frontend/composerModels.test.mjs tests/frontend/chatRetrySafety.test.mjs`：验证现有选择和重试安全基线。
- `rtk proxy .venv/bin/python -m pytest tests/test_chat_models.py tests/test_chat_retry_safety.py tests/test_central_execution_recovery.py`：验证相关后端基线。
- 在 `/Users/jiandan/Code/GitCode/Easel/web/frontend` 执行 `rtk npm run build` 和 `rtk npm run lint`：构建与静态检查。

新增测试命令随对应文件创建后纳入同批验证，执行时记录实际结果。单元测试、浏览器、打包运行、真实供应商与生产验收分别出具证据。

## 本轮文档自查

- 就绪与任务状态分离；执行/交付/文件/费用分离。
- 模型便利性不绕过来源、租期和权限校验。
- 草稿确认按 revision，恢复按精确 taskId，不按最新结果猜关联。
- 文本复制与文件下载先修现有断点，不引入新文件管理或版本平台。
- 首页层级属于待用户验证方案，不伪装成已证实转化提升。
- 本文件未运行以上命令、未实施代码、未创建 Git 提交、未发布。
