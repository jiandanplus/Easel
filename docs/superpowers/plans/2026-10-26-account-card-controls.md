# 用户卡片控件收纳实施计划

**Goal:** 将侧栏运行状态及日夜按钮移入用户卡片，同步原 17868 工作台。

**Architecture:** Sidebar 继续提供原运行状态数据；SidebarAccount 负责弹出卡片布局并复用 AppearanceToggle，认证和主题逻辑保持原样。

**Tech Stack:** 现有 React/TypeScript/CSS 和离线 Pi macOS 打包工具。

## 约束

- 保持 `codex/ui-redesign`，不合并主分支。
- 不更改设置内容、账号/接口配置、模型目录、钱包或历史。
- 保留未登录直接登录，及 A/C/系统外观既有行为。

## 执行步骤

- [x] 修改 `web/frontend/src/components/Sidebar.tsx`，将运行状态作为 props 交给用户卡片，删除侧栏重复控件。
- [x] 修改 `SidebarAccount.tsx` 和 `SidebarAccount.css`，加入两行本地偏好；保持原交互与主题语义变量。
- [x] 运行现有账号、主题和对话侧栏测试，再运行完整前端测试、build、lint；不为单纯布局移动新增镜像测试。
- [x] 用现有离线输入生成新版本，沿用原 HOME/workspace 同步 17868；检查个人配置和历史完整。
- [x] 在真实桌面浏览器核对卡片、日夜切换、Escape/设置导航；通过现有短视口逻辑测试与 CSS 作用域复核，保存截图与验证记录。
