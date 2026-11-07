# 原对话请求状态查询实施计划

**Goal:** 把现有中央只读恢复接回精确的原对话轮次。

**Architecture:** 发送前私有记录保存 owner + turn_key，当前账号只读查询原 operation tuple；SSE 提供受限关联提示，助手消息持久化标识，独立卡片按用户动作查询。原资金和重发保护不变。

**Tech Stack:** 现有 Python、React/TypeScript、pytest、node:test，无新增服务或依赖。

设计见 [设计说明](../specs/2026-10-26-conversation-recovery-design.md)。用户要求在当前分支继续开发，保留双模式，不合并；不自动提交／推送。中央生产库不需变更。

## 任务

- [x] 后端关联：先写失败测试；在 execution_records.py 增加 operation_turn_key 和筛选，在 execution_proxy.py 原记录保存关联；CentralClient.recoveries 支持精确筛选，新 central_web GET 路由只读复用。测试跨owner/turn、旧记录、边界和 GET-only。
- [x] Web／引擎桥：RunRequest 增加默认空 turn_id；web/app.py 传入当前轮次；Pi/Codex 代理接入。接受中央上下文后记录并发送 central_recovery 事件，测试重放、正常/失败/停止和无隐私Scope；新快照显式标记，旧快照不推测关联。
- [x] 卡片：api.ts 增加 centralTurnRecoveries 和 SSE提示回调；新 ConversationRecovery.tsx + 集中状态投影/请求竞态控制；账号变动失效事件。MessageBubble/ChatPage 显示卡片；App/store 保留精确轮次。测试提示格式、状态文案、大数、旧响应、卸载和重试保护。
- [x] 集成回归：后端组合375通过／8个外部运行时跳过；前端334通过；build/lint通过（保留原无关warning）；独立审查发现的旧快照／hint-only停止问题完成红绿修复与复查。
- [x] 打包和验证：portable v18已启动17868，103个原文件、原身份与钱包保全；Pro真实工具＋运行中刷新后同轮次恢复＋消息内精确查询通过；两轮模型合计1133微点，原三笔waived及审计保持，重复GET无新增派发或财务变化。
- [x] 收尾：验证报告、截图、安全收据、版本／包哈希，分清本地模拟、打包浏览器和真实渠道证据。见[验收记录](../../validation/2026-10-26-conversation-recovery.md)。
