# 复制回复的真实成功反馈

本轮修复浏览器没有 Clipboard API 时，复制回复仍显示“已复制”的分支。原 `ChatPage` 可选调用返回 `undefined`，`MessageBubble` 等待它后正常进入成功状态。新回调显式拒绝缺失接口，并等待 `writeText` 完成；已有“复制失败”状态和重试入口保持，未增加界面说明或样式。

相对 v35 的公开发布来源只有 `web/frontend/src/components/ChatPage.tsx` 一处四行回调变化，当前 SHA `b2a74a5cbc39b223da06ed440c34d0b667fa85fa8e925730c1eb1cf81da87444`。逆向恢复原一行后的 SHA `2c2011eff8c75f73863884c7e8d64bbe64b9f17b948c82d2fb97e2ed2eaa51b8` 精确匹配 v35 authority；904 份来源集合和 60 项构建输入未增删。`MessageBubble` 和 `DeliverableActions` 没有修改。

新 `tests/frontend/replyCopy.test.mjs` 运行实际 TS 组件和私有 ActionBar，隔离 navigator、hook 状态和计时器。原分支为 4 项中 2 项失败，用户及助手消息在 API 缺失时误报成功；修复后覆盖 API 缺失或空对象、写入拒绝、失败后成功重试、等待写入及成功标签复位。作者相关三文件 25 项通过及应用 TypeScript 检查通过；独立复审另跑 4 项通过，计数不累加。

Root 完整前端命令 `rtk proxy node --test tests/frontend/*.test.mjs` 实际退出 0：589 passed、0 failed、0 skipped，4.5198 秒。新测试 SHA `5963933cb8f1557e83e1e7e2f60caa3c050c15c79d298a5648eb554de8356284`。独立报告 SHA `a93d0c68d31e08472b1923ea5b3ecf0c504b0b585164d92c1ef85841293c89a1`，限定审查无 P1／P2。

这些是合成组件行为与源码验证，未操作真实系统剪贴板，不替代原生剪贴板、IME 或 VoiceOver 证据。新的前端 receipt 和 macOS 包尚待本轮构建、静态包验证及受保护激活；原 17868 工作台仍以 v35 为准。订阅和自定义接口路径未改变，没有模型、付款或资金调用。
