# 0002 工作区与构建输入不得放在系统临时目录

日期：2026-11-05 · 状态：已实施

## 背景
用户工作台的工作区和 v28–v36 的构建输入都放在 `/private/tmp`。macOS 重启和每晚维护会清理该目录：工作区从 887 个文件被删到 4 个，构建输入的官方归档与 wheel 也被清空。

## 决定
- 启动器拒绝在 `/tmp`、`/var/tmp`、`/private/var/folders` 及平台临时目录启动新实例；停止既有实例不受影响；测试与隔离诊断显式传 `--allow-temporary-workspace`。
- 用户工作区放在 `~/Library/Application Support/Easel/workspaces/main`；构建输入放在 `~/Easel-build/`。

## 影响
从 2026-10-30 备份恢复后，此后只存在于临时目录的对话与产物已无法找回。
