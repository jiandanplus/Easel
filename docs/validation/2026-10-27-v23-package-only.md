# v23 桌面候选：只打包，未激活

ZIP：`outputs/_run/pi-portable-20261027-v23/Easel-Pi-macOS15-arm64.zip`。

SHA-256：`48ea4652435178847d825399680c62a818c5431d1877cd31a7d1a779b1850ccd`，129,825,454 字节，36,112 个 ZIP 条目，全部 CRC 通过。

本次没有停止或修改 17868 运行实例，没有读取或修改真实用户 auth／状态，没有激活 v23，也没有新模型调用。原 v22 ZIP 的 `843c3a333eaacf6a75dbfcab36d09d3c79f1fbcb523056e1471c1f8a65f16dc4` 再次核对不变。

## 发布输入

公开来源从 v22 的 900 个增为 901 个，仅四个路径改变：

- `easel/platform/auth_refresh.py`：新增共享持久刷新围栏。
- `easel/platform/central.py`、`easel/platform/media.py`：认证护栏与 GET 一次续期。
- `easel/harnesses/pi.py`：root 的媒体工具结构化 content 修复。

来源 manifest SHA-256 为 `b59e1120f857e7ae454f17d771d682952ecb0c21b29e92ba885b684a6a13316e`。59 项构建输入、前端来源与构建产物均沿用 v22；26 个锁定 wheel、Python 3.12.13、Node／Pi payload 复用并验证。独立 f50 服务包不变，`services/` 下 M2／M3 工作代码不在该桌面包中。

资源 inventory 全部 15,399 文件重新校验并核对完整性。相对 v22 的额外变化只有两个 manifest／provenance 和六个 wheel `RECORD`：逐行差异仅为安装器生成且未发货的 `bin/` 入口脚本哈希，其他同名行全部相同，包内不存在这些 `site-packages/bin` 文件。详见 `installer-record-diff-safe.json`，没有把这六个文件假称逐字相同。

## 验证及范围

- 使用新建独立 HOME 和系统 PATH，从 App launcher 启动独立 port 0 工作台；默认 Pi／订阅模式、统一登录地址、无旧 auth、无供应商 key 均通过。测试结束只停止该自有实例。
- 使用包内隔离 Python 和 Pillow 12.3.0，完整解码两张此前已取得的原始 1024×1024 PNG，并核对原 SHA；没有重新请求生成。
- 使用包内实际 `auth_refresh`、中央和媒体模块，通过合成 transport 验证正常轮换保存、未知结果持久围栏和重启零重放；外部 HTTP 与供应商 POST 为 0。
- 815 个实际公开 payload／前端文件完成通用 PEM 私钥、供应商 key、字面 auth token 扫描，结果为 0；ZIP 用户状态路径排除检查为 0。扫描没有读取真实用户 auth 或服务器密钥，未改变的 vendor payload 另由 inventory／锁定输入验证。
- 认证源码的 326 项原生回归与 root 单独 Pi 验证见认证报告；独立 auth／Pi review 由 reviewer 单独记录，不与包验收计数相加。

完整安全回执在 `outputs/_run/pi-portable-20261027-v23/validation/`，同名 `2026-10-27-v23-package-only-safe.json` 另存本文目录。

该包仍未签名、未公证。本次是同机新 HOME 验证，没有声称已完成干净设备／Gatekeeper 验收；上游 Python 原始 archive SHA 仍未补验，只核对了受控运行时 inventory。激活、真实用户状态保留及实际模型验收由 root 后续独立控制。
