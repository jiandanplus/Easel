# v25 工作台交付：已激活，账号等待重新登录

**v25 已由 root 激活到本机 17868。此次升级的 138 个受保护文件逐字不变，历史、自定义配置及账号 scope 保留。升级前中央账号已为未认证状态，错误码为 `refresh_uncertain`；当前等待用户重新登录。** 本轮保全不证明账号持续登录，也不证明客户端钱包可读取。

本报告整理已有安全回执及 root 的实际浏览器观察。文档整理阶段只写本文和[同名安全汇总](/Users/jiandan/Code/GitCode/Easel/docs/validation/2026-10-28-workbench-v25-delivery-safe.json)，未重跑测试、激活实例、读取原始 auth 或私有备份，未修改 source/tests/scripts，也没有 commit。root 的实际操作与构建者此前的验证分开记录。

[App](/Users/jiandan/Code/GitCode/Easel/outputs/_run/pi-portable-20261028-v25/Easel/Easel.app) · [ZIP](/Users/jiandan/Code/GitCode/Easel/outputs/_run/pi-portable-20261028-v25/Easel-Pi-macOS15-arm64.zip) · [artifact.json](/Users/jiandan/Code/GitCode/Easel/outputs/_run/pi-portable-20261028-v25/artifact.json)

| 包内核验项目 | v25 结果 |
| --- | --- |
| ZIP SHA-256 | `7c017d0efb35b19928bc8cbf3b6e7e4e48826773dcf40315fedaca9a28f5effb` |
| ZIP 字节数／条目 | 129,829,143／36,116，CRC 全部通过 |
| App 资源 inventory | 15,401 文件逐项 SHA 通过，隔离运行后文件集合及 SHA 再次一致 |
| 公开 source | 903 文件，较 v24 新增 2、修改 4，合计 6 条差异路径 |
| source manifest SHA-256 | `25343b52564546b363d6018f31428e35ca63a47fddab4b1b6562219527c86c44` |
| Pi runtime manifest SHA-256 | `8448f44712ed33164b3e04fd2dc198be2b03ba2bd0f62f237aec3c0d4f7d9783` |
| 运行环境 | macOS 15+、Apple Silicon；内置 Pi 1.4.0、Node 24.22.1、CPython 3.12.13、Pillow 12.3.0 及 26 个锁定 wheel |
| 发行状态 | 未签名、未公证；UI 使用本机浏览器 |

公共来源差异精确为 `easel/harnesses/pi.py`、`easel/platform/media.py`、`easel/platform/media_records.py`、新增的 `easel/platform/media_contracts.py` 和 `easel/platform/media_files.py`，以及 `web/frontend/src/components/CentralAccountSettings.tsx`。前五条对应已独立复核批准的 M4 review-r1；最后一条为 root 已批准的账号 loading 修复，SHA-256 为 `9cdf677a62d92f81dcc3aabaa791e2db1f47ba079fb58c1b168b39258a88f5da`。worker 独立构建输入 SHA-256 为 `8ec881590206c0c487f95469860f04852b9c1c80b4a198a9fd254fcf00768682`。中央 M3 工作源码未进入桌面包。

58 项 build inputs 比 v24 的 59 少一项，是默认 authority 排除自身 `packaging/pi/source-manifest.json` 的结果；没有减少实际构建依赖。其余内容只有受审 worker 发生变化。Pi payload 从 v24 验证后复制到新目录，仅刷新 worker 与 manifest，没有下载依赖或 npm 安装。Python 使用既有离线输入，`verify_python` 完整通过；原 v24 App／ZIP 保留，v24 ZIP SHA 仍为 `5789d1f441054f14479b25b5581bdaeb10d2fe5436f7d42b504386a42d53ee0f`。

root 的激活回执记录：端口 17868、采集时 PID 27506、沿用同一 workspace、没有 HOME override、当前 HTML 与 v25 包匹配、provider_posts=0。PID 是当次快照，不是后续恒定不变的承诺。v19–v24 共 12 个“启动现有工作台／停止现有工作台”辅助入口已转向 v25；旧 App 和 ZIP 不变。

本轮保全基线是 **138 个文件**，不是旧 v24 报告中的 155 个。升级后的 `changes=[]`、`new_private_runtime_files=[]`，历史和自定义配置保留、scope 相同。保全回执明确 `wallet_observed=false`、`same_wallet=null`。root 交接确认升级前就已经 `authenticated=false`、`refresh_uncertain`，所以不能把文件保全写成“升级后持续已登录”或“钱包读取通过”。

root 另在服务器进行只读观察，钱包仍为可用 **6,952,153**、预留 **1,000,000**、累计已用 **2,047,847 microcredit**，两份只读回执的数据库写入和新增 provider POST 均为 0；drift 诊断确认 `wallet_unchanged=true`。这是服务器侧的独立观察，不代表 v25 客户端已登录、能读钱包或已完成真实模型验收。旧 1 点预留仍未解决。

root 已 reload 实际 v25 浏览器并进入账号 tab：loading 阶段没有错误的“订阅关闭”闪现，页面等待用户重新登录。[settings-loaded.png](/Users/jiandan/Code/GitCode/Easel/outputs/_run/pi-portable-20261028-v25/settings-loaded.png) 只作为真实设置页证据；正式账号截图待补。误名 `skills-ui-review.png` 实为 loading 设置页，本报告不将其引用为技能页面证据。

验证口径分别保留，不重复相加：

| 证据批次 | 已有结果与适用范围 |
| --- | --- |
| v25 前端 | 构建者新跑 454 passed、0 failed；TypeScript 与 Vite 构建通过。Vite 保留大 chunk 提醒。 |
| M4 review-r1 回归 | 已审来源的 259 passed；其中实际 Pi SDK 的 12 个受控场景属于该批验证，不另加为总计。 |
| M4 独立定点复核 | 另批 4 passed，关闭 rate 白名单泄露问题；不与 259 合并计数。 |
| 中央 PostgreSQL／ASGI | root 交接的 10 个受控本机协议集成场景；不能推断生产九项新能力可用。 |
| 中央 full 旧运行 | 1697 passed、90 failed、7 errors、7 skipped，423 个冻结输入不变；结果是 failed。 |
| 中央 awake full 重跑 | root 正在单独执行，本文没有通过结果，不标记 passed。 |

v25 同机隔离验收使用新的显式 workspace 和系统 PATH，HOME 不变：默认 Pi／中央订阅入口，无旧认证或供应商 key，auth_posts=0、external_model_posts=0。隔离端口 54516 的自有实例已用该 App 与精确 workspace 的停止命令关闭，原 17868 前后 HTTP 200。实际包内 Node 成功 import Pi SDK；包内 Python／Pillow 完整解码此前取得的两张 1024×1024 PNG；合成 MockTransport 验证刷新轮换保存、未知结果持久围栏和重启零重放。以上没有重新生成图片或真实登录请求。

生产仍沿用 `f50e52511e941d3bea8e724eddbd68330e20eaa6339c5e2437b3880ca9705abf`、schema through 016，当前真实验收范围为已有文本与两款图片模型。M4 的九项能力实现和受控协议测试不等于真实生产上线。本轮 v25 的 provider posts 为 0；两张真实图片、原图交付和每张 1 点结算只沿用 [v23 真实生图交付报告](/Users/jiandan/Code/GitCode/Easel/docs/validation/2026-10-27-newapi-image-workbench-delivery.md)，没有把旧请求计为 v25 新验收。

签名、公证、Gatekeeper、独立干净设备验收仍未完成；原生系统剪贴板粘贴端到端、真实中文 IME 和 VoiceOver 也没有新增验收。构建记录继续为 `python_archive_verified=false`、`python_runtime_inventory_verified=true`，不能把受控运行时库存核验改称原始上游 Python archive SHA 已验。正式支付、其余生产能力及中央异常和恢复验收继续按各自工作流推进。

所有读取的安全回执、日志和既有报告的路径、字节数及 SHA-256 见同名 safe JSON；不复制原始 auth、用户 state backup 或服务器密钥。
