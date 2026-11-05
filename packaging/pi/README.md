# Easel 内置 Pi 本机预览包

此包为 macOS 15+ Apple Silicon 的本地未签名预览，包含 CPython 3.12.13、Node v24.22.1、官方 Pi agent-core / pi-ai SDK 1.4.0 和 Easel 基础技能。打开 Easel.app 后界面在浏览器中显示。无需安装全局 Agent、Python、Node、npm 或 pip。当前并非原生窗口应用，未声明签名、公证、Gatekeeper 或独立干净设备验收。

默认数据位置：`~/Library/Application Support/Easel/workspace`。新工作区默认 Pi，已有显式 runtime 配置保留。关闭网页不会终止服务；双击随包 `Stop Easel.command` 停止经过自有 token 认证的实例。升级前备份工作区并保留旧应用，以便回退。程序不会通过 PID 杀死其他服务。

新安装的默认工作区使用中央订阅渠道，点击左下角头像即可登录，无需填写中央地址或供应商 Key。已保存的中央地址、账号、订阅／自定义渠道和模型配置继续保留。需要自定义接口时，在设置中切换渠道并使用自己的接口；当前正式付款仍按运营配置保持关闭。

自定义 Responses 地址和中央订阅沿用 Easel 设置。基础文件工具由 Easel 约束在工作区内；shell 执行需要界面确认和系统 sandbox，缺失时拒绝执行。ffmpeg、浏览器自动化及其他媒体外部工具未因此自动获得完整独立环境，程序不会往不可变运行时自动安装依赖。

构建输入：`node-provenance.json` 为固定官方发布 URL / SHA256；`runtime/pi/package-lock.json` 是 npm SHA512 integrity 权威。准备脚本只在开发期下载并校验，禁用 npm lifecycle，包内不含 npm。Node LICENSE / README 和所有 npm 原始 LICENSE / NOTICE 保留在 `runtime/pi`，`dependency-provenance.json` 列出版本和许可证路径，整个闭包由 schema 2 runtime manifest 哈希。

公共源码候选规则为 `public-source-policy.json`，包含本分支未跟踪公共源码。评审后显式运行 `build_pi_macos.py --freeze-source-only` 生成逐文件 `source-manifest.json`；实际构建必须严格匹配此清单。前端 receipt 使用相同 JSON 规则，拒绝过期 dist。普通源码排除环境、用户状态、输出、node_modules 等目录；仅独立校验的 Pi runtime payload 允许带供应商依赖。

Python 来自固定的 python-build-standalone CPython 3.12.13 归档及其离线准备目录，配套 26 个 hash-pinned wheels。构建会核对归档的实际大小、SHA256 与固定发布资产记录，再核对来源／派生记录、runtime 每文件清单和 wheel SHA256；任一不匹配即拒绝构建。`python_archive_verified=true` 仅表示该轮实际归档字节通过这些核对，不表示已验证发布者签名。该轮归档哈希与结果以 `build-provenance.json` 为准，历史交付包和回执保持原记录。

准备：`python scripts/packaging/prepare_pi_runtime.py --output /ABS/NEW/pi-payload`。
构建：`python scripts/packaging/build_pi_macos.py --inputs /ABS/PREPARED/PYTHON --pi-root /ABS/NEW/pi-payload --output /ABS/NEW/delivery`。

实际交付的 `artifact.json`、资源 `build-provenance.json`、`source-manifest.json` 与 `build-inventory.json` 记录该轮版本与哈希。构建成功与真实模型、中央扣点、干净设备验收是独立证据。
