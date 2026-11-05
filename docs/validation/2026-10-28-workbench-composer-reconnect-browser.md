# Composer 重连：实际 ReactDOM 合成浏览器验收

2026-10-28，root 使用 Codex 的 CUA 在独立 loopback Vite 开发页操作实际 `DashboardPage`、`ChatPage`、`ComposerModelPicker`、草稿与 API 模块。两条路径通过。此结果属于当前开发源码的浏览器验证；**不是 v30 App／ZIP 验收，也不是原 17868 工作台已经更新的证明**。

| 页面 | 注入故障 | 故障时 | 只恢复 mock 后端 | 只点击实际输入框「重新连接」后 |
|---|---|---|---|---|
| Dashboard | `platform_unavailable` | 发送禁用，显示「暂不可用」 | 输入框继续不可提交 | 恢复发送；手选 B／高、原草稿与合成附件保留 |
| Chat | 模型目录网络失败 | 发送禁用，显示「连接失败」 | 输入框继续不可提交 | 恢复发送；手选 B／高、原草稿与合成附件保留 |

恢复过程未打开模型菜单刷新、未重载页面，也未点击发送／重试。每次重新连接均实际请求了同一 `session_id` 的模型目录并包含 `refresh=true`，同时重新读取状态。最终 mock 计数为模型 GET 10、状态 GET 6、chat／task POST **0**、其他业务 POST **0**。初始 StrictMode effect 和目录租约可能产生额外 GET，因此不将总 GET 数量解释为一次点击的精确请求数量。只读请求历史与截图共同用于关联故障和恢复。

合成附件校验 POST 与本地故障控制 POST 属于夹具自身操作。所有 chat／task POST 均固定拒绝，未知 API 404，没有真实 API fallback。Vite 的 `envDir:false`、严格 fs allow、独立缓存和 CSP 保持启用，只有本机地址可访问。没有访问原工作台的历史、账号或接口配置，也未调用模型／扣点。

首次夹具启动曾覆盖 child 的 HOME；root 保留首次计数、进程记录和截图，停止那两个自建进程后移除了 HOME 环境项，并在修正后的环境中重新完成上述两条路径。修正后的 child 仅接收 LANG、NODE_ENV、PATH、PYTHONDONTWRITEBYTECODE、TMPDIR。九个记录的实际产品源码前后 SHA 一致。两次 Playwright 精确 checkbox 定位未匹配 native AX 投影，后续用新读取的 AX 索引完成操作；这两次自动化定位失败没有被当作产品故障或测试通过。

根验证结束后关闭合成浏览器 tab，仅停止自身 mock／Vite 进程。原 17868 的 v29 工作台继续保留。新包构建仍等待至少 2 GiB 可用空间；没有修改 dist、来源清单、Pi／Python 运行时、App 或 ZIP。

完整合成证据保存在 [验证目录](/Users/jiandan/Code/GitCode/Easel/outputs/_run/workbench-composer-reconnect-browser-20261028)，包括请求历史、实际夹具、来源 SHA、原始和修正环境的进程记录、截图、AX 观察及结构化回执。清单仅覆盖明确列出的文件，不包含 Vite cache 或用户配置。

![对话页重连后仍保留模型、推理强度、草稿和附件](/Users/jiandan/Code/GitCode/Easel/outputs/_run/workbench-composer-reconnect-browser-20261028/chat-nohome-recovered.png)
