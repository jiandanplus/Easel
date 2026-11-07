# Account Popover Implementation Plan

> **For agentic workers:** Use subagent-driven-development; delegate the account component and independent review while root handles navigation, package and live preservation.

**Goal:** 左下头像已登录弹账号卡片、未登录直达统一登录，设置入口移入卡片，设置内容保持原样。

**Architecture:** `SidebarAccount` 管理状态读取、账号卡片和现有登录API；Portal卡片不被80px栏裁切。Sidebar将已有设置回调传入卡片并移除独立设置按钮；App与设置内容不改。沿用原v11打包/保全流程生成v12并切换17868。

**Tech Stack:** Existing React19 hooks/createPortal、TypeScript、主题CSS、Node行为测试、离线Pi打包。

## Constraints

- 使用当前codex/ui-redesign，不合并main，不覆盖既有脏文件。
- 原SettingsPanel/CentralAccountSettings/store源码哈希保持。
- 不发真实模型、不退出真实账号，不更改钱包/供应商密钥/账号权限。
- GET/动作并发和未知态保护，已有pending不隐式重启。

## Tasks

- [x] SidebarAccount先红后绿，最终23项专项通过，包含未知态/原pending/失败动作/迟到响应/短高度/focusin保护。
- [x] Sidebar移除独立设置入口并完整透传回调，App和设置内容/存储源码保持。
- [x] 最终276项前端、类型/构建/lint通过；隔离浏览器一次直达登录、一次完成、一次合成退出，模型调用为0。
- [x] v12公开源码881/构建输入58/前端84及13产物精确绑定，15251资源及35798 ZIP条目验证，隔离禁外网smoke通过。
- [x] 原17868同HOME/workspace切换v12，真实登录/45文件/钱包及历史DOM摘要保持；真实卡片与设置/关闭验证通过，真实账号未退出。
- [x] 本机启动入口与回执更新，v11和私有备份保留，合成服务关闭，不合并main。
