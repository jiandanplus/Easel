# Easel 工作台 v30 交付与证据边界（2026-10-28）

状态：**v30 冻结包、同机隔离启动和真实 App 的离线工具链已验收；同一原工作区的预览切换与保护核对有安全回执。** 这不是新设备、真实模型、账号、钱包或完整浏览器交互验收。后续 A1 入门体验修复在另一轮进行，当前工作树不能当作此 v30 包的冻结源码。

## 可使用的交付物

- [macOS 15 Apple Silicon ZIP](../../outputs/_run/pi-portable-20261028-v30/Easel-Pi-macOS15-arm64.zip)：129,745,867 字节，SHA256 `20d09be336d2e08c0d3ea5e4c82443a3c5086b83cbe7bb0e345c72f3ffeff897`。归档时重新计算 ZIP SHA256 与原回执一致。
- [解压后的 Easel.app](../../outputs/_run/pi-portable-20261028-v30/Easel/Easel.app) 与 [包元数据](../../outputs/_run/pi-portable-20261028-v30/artifact.json)。元数据写明 macOS 最低 15.0、Python 3.12.13、26 个 wheel、`unsigned=true`、`notarized=false`，UI 是本机回环地址上的网页。
- [公开证据归档](../../outputs/_run/workbench-v30-delivery-20261028)及其[逐文件复制清单](../../outputs/_run/workbench-v30-delivery-20261028/copy-manifest.json)。归档只复制 52 个明确指定的公开文件，共 258,336 字节；含清单为 53 个文件、282,884 字节。每项均记录源文件复制前、归档副本、源文件复制后的 SHA256，三者一致。归档没有复制原用户工作区、密钥、配置、cookies、大体积前后清单或隔离工作区。

## 冻结包与功能链路

[包校验回执](../../outputs/_run/workbench-v30-delivery-20261028/validation/v30-validation-safe.json)核对 15,402 个资源文件、ZIP 内 15,407 个完整容器文件的字节与符号链接、所有归档成员 CRC、904 个公开源码文件及 59 个构建输入。前端构建有 13 个文件；HTML 的 3 个本地引用和 JavaScript 的 8 个资源引用均能在包内解析。相对 v29，源码差异是 `App.tsx`、`ChatPage.tsx`、`ComposerModelPicker.tsx`，另有 `packaging/pi/README.md` 构建输入差异。11,932 个 Pi 文件与 v29 一致；比较的 2,597 个 Python 路径中，只有 6 个安装器 RECORD 的未交付 bin 行哈希差异。包内 Python、Node、Pi SDK 和 Pillow 的实际导入与 PNG 解码已验。包的源清单、Pi 清单及前端回执 SHA 见[包元数据](../../outputs/_run/pi-portable-20261028-v30/artifact.json)。

同机全新隔离工作区曾从实际 App 启动，只用包内运行时，匿名状态显示需登录，未创建凭据或供应商 Key；该自有实例经过认证停止、进程等待和端口关闭。[原始范围说明](../../outputs/_run/workbench-v30-delivery-20261028/validation/v30-validation.md)保留两次校验器探针错误：过时 npm scope 与用 CommonJS 解析仅供 ESM import 的导出。修正的是临时校验脚本，最终探针退出 0；两次失败不代表产品故障。

[实际 App 离线工具回执](../../outputs/_run/workbench-v30-delivery-20261028/offline/offline-tool-safe.json)记录：包内 Pi SDK 向合成供应商请求模型目录一次、Responses 两次；首个响应发出 `write_file` 工具调用，宿主在新隔离工作区生成 168 字节 UTF-8 文件；第二个响应收到同一 `call_id` 的工具结果。App 输出 API 回读与媒体下载和原文 SHA256 均为 `e3d01b023ebdaf7eda21ca5590049ea551d0dca002be995555ccba2524829b16`。聊天事件包括 accepted、activity、token、done，原轮终态为 `completed`、`clean_end=true`。合成供应商只在回环端口运行；非回环 TEST-NET 连接经 sandbox 预检获 EPERM。自有 App 与模拟供应商均正常停止并关闭端口。[逐证据索引](../../outputs/_run/workbench-v30-delivery-20261028/offline/evidence-index-safe.json)记录每份合成请求、事件、输出和脚本的哈希。首次 sandbox 配置及模拟监听失败是验收设施预检问题，当次未启动 App、提交聊天或生成目标文件；最终只提交一轮聊天并成功。

## 原用户工作区预览切换

[备份安全回执](../../outputs/_run/workbench-v30-delivery-20261028/activation/backup-safe.json)记录：原工作区在切换前完成独立备份，1,160 个条目、其中 894 个普通文件、内容 15,509,557 字节，159 个受保护文件逐字节核对；备份时原工作区没有移动。[切换安全回执](../../outputs/_run/workbench-v30-delivery-20261028/activation/activation-safe.json)记录 v30 预览实例曾在原端口 `17868`、同一工作区启动（PID 34685），HTML 匹配 v30 包；旧 App、ZIP、旧启动器未被改写，新启动／停止快捷入口置于 v30 目录外层。[最终保护回执](../../outputs/_run/workbench-v30-delivery-20261028/activation/preservation-safe.json)记录受保护集合没有新增或变化、工作区和来源 scope 一致、build_id 更新。`wallet_observed=false` 且 `same_wallet=null`，因此不能声称真实钱包相同；切换前 `authenticated_before=false`，不能声称真实账号登录已验收。没有 profile／数据迁移、模型提交或支付／钱包写入。

最初保护预检误请求 `/api/workbench`，返回 404；[失败回执](../../outputs/_run/workbench-v30-delivery-20261028/activation/precheck-route-failure-safe.json)证明当时尚未备份、停止旧实例或启动新实例。随后仅将操作脚本读取位置纠正为 `/api/status.workbench`。早期[阻断审阅](../../outputs/_run/workbench-v30-delivery-20261028/reviews/activation-review.md)、[修正审阅](../../outputs/_run/workbench-v30-delivery-20261028/reviews/activation-r2-review.md)和[路由审阅](../../outputs/_run/workbench-v30-delivery-20261028/reviews/activation-r3-route-review.md)保留审阅时间顺序；前两份是静态审阅，不替代上述实际运行回执。

## 证据限制与下一步

[原轮 request hash 修复验收](2026-10-28-workbench-query-hash.md)的 529 项前端测试与 15 项独立聚焦执行，绑定当时的 v30 候选 `App.tsx` SHA256 `475b2d4263d10cb278c99cfbb62d74abdf396dc4160a8dd0106add9d29191450`。旧中央 2,468 passed／7 skipped 属于更早的 1,333 路径冻结输入，不能标为这个新 App 的完整中央回归结果。当前并行的 A1 工作又会改变工作树，需另行冻结、构建和验收后才能成为下一个交付版本。

仍待验：独立干净设备、Gatekeeper、签名与公证、原生输入法和 VoiceOver、完整真实浏览器用户路径、真实新模型、账号认证、钱包与账单。离线工具只验证合成供应商；没有真实付费或云端调用。此归档阶段未重新运行构建、测试、浏览器、SSH 或网络验收，也未改动 App、ZIP、产品源码、主计划、原用户数据或已有交付物。
