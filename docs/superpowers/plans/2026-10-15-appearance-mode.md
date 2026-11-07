# Appearance Mode Implementation Plan

**Goal:** 默认跟随系统；日间 A、夜间 C；底部一键切换并保留五主题选择。

**Architecture:** theme.ts 同时管理偏好模式和有效主题；初始化脚本采用相同解析规则。独立外观控件供侧栏和设置使用，通过现有订阅更新。

**Tech Stack:** 现有 React / TypeScript / CSS 和 Node 测试，无新增依赖。

- [x] 修改 tests/frontend/theme.test.mjs，加入系统模式和偏好状态测试并观察失败（实现前 34 项中 33 项失败，因新接口/系统监听缺失）。
- [x] 扩展 src/lib/theme.ts；同步 index.html 首屏脚本。
- [x] 新增 AppearanceControls.tsx，接入 Sidebar.tsx 和 ThemeSettings.tsx；补齐底部和设置区响应式样式。
- [x] 运行前端全量测试、构建、lint；检查事件顺序与监听清理（116 项通过）；修复并复核跨标签旧事件竞态。
- [x] 浏览器检查系统深浅模拟、实时跟随、手动切换、刷新、窄屏；恢复跟随系统并保存截图。
- [x] 更新 DESIGN.md 和验收记录。
