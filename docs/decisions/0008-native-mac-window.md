# 0008 桌面版用 Mac 原生窗口

日期：2026-11-06 · 状态：已实施，`desktop-v41` 起生效

## 背景
Easel.app 原来只是启动脚本：起本机服务，再交给系统默认浏览器。用户希望它是独立的 App。另外，直接打开 Easel.app 用的是默认工作区 `workspace` 和端口 17860，而真实数据在 `workspaces/main`、端口 17868，只有 `Start Easel.command` 指向它。

## 决定
- `Contents/MacOS/Easel` 换成 Swift 写的窗口（`packaging/macos-shell/EaselShell.swift`，系统 WebKit，不新增依赖），构建时用 `swiftc` 编译；原脚本改名 `easel-launcher`。带参数运行时直接转发给脚本，`Stop Easel.command` 和 `verify_portable.py` 不变。
- 窗口固定使用 `workspaces/main` 和 17868。服务仍是独立进程，应用内更新的流程不变。
- 关窗后服务继续运行，任务不中断；退出 App 才停止服务，有任务运行时先询问。
- 站外链接交给默认浏览器；中央登录和本机预览在 App 的弹出窗口里打开；下载走保存面板。
- 从旧版本路径启动（比如 Dock 上固定的旧版本）时，自动转到 `current` 指向的版本。

## 影响
- 构建机需要 Xcode 命令行工具；外壳源码哈希记入 `build-provenance.json`。外壳不在 `build-inventory.json` 范围内，完整性由签名清单对整个 ZIP 的校验保证。
- 网页本地存储（草稿、新手引导状态）与浏览器分开，浏览器里的未发布草稿不会自动带入 App。
- 仍未做 Apple 签名公证。
