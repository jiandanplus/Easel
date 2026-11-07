# Easel 内置 Pi 与完整运行时

用户已于 2026-10-23 批准本设计方向并要求直接开发：默认内置 Pi、运行环境随安装包分发、保留订阅与自定义接口、当前分支、不合并主分支、完成真实模型／工具／扣点验收。

## 产品与范围

新安装默认显示“Easel 内置智能体 · Pi”。macOS ARM64 首个本地交付包包含 Easel Web、本地 CPython、固定版本 Pi SDK、官方 Node.js 运行时和基础技能。用户启动 `.app` 后通过浏览器打开应用，无需安装 Agent、Node.js、Python、npm 或 pip。当前不重写为 Electron/Tauri；签名公证、独立设备及其他操作系统须单独留验收证据。

已有用户的显式 runtime、服务来源、供应商 Key、会话和 root `.env` 保持不变。Pi 新对话使用独立历史，不重解释 Codex 会话；原外部引擎仍可选择。新安装与无显式配置的工作区默认 Pi，旧 OpenClaw 环境保留原解析规则。

## 执行核心

固定 `@earendil-works/pi-agent-core@1.4.0` 与 `@earendil-works/pi-ai@1.4.0`。受控 Node worker 使用官方 SDK，不加载全局 Pi 配置、用户扩展或 Codex 提示词。Python 与 worker 以 LF JSONL 交换运行、流式事件、工具请求／结果、最终状态；关闭 stdin 或取消时 abort 并回收 owned 进程。

模型采用 Responses 协议，兼容现有自定义 Responses 供应商设置和中央 `/v2/responses`。所有 SDK 模型重试显式为 0，单次用户回合最多 8 次模型请求和 16 次工具调用；有限输入、输出、事件、历史和执行期限。中央订阅的输出上限来自已授权模型目录，不能超过服务端定义。

Pi worker 不直接执行文件或 shell 工具。固定工具由 Python 实现：`list_files(path)`、`read_file(path)`、`write_file(path,content)`、`run_command(command,timeout_seconds)`。模型提供参数，Python 验证路径与权限并返回结果。读取／写入限工作区，拒绝符号链接、路径穿越、`.git`、`.env*`、中央／供应商私有状态。只读禁止写入。macOS shell 每次请求用户确认，使用系统 sandbox-exec 的文件与网络约束；无受支持 sandbox 时明确拒绝 shell，文件工具仍可用。执行子进程不继承模型、平台或宿主机密钥。

## 双服务模式

自定义模式沿用工作区内置供应商设置，固定版本 Pi 发往用户保存的 Responses 地址。地址改变不能自动沿用旧 Key。订阅模式采用中央登录、模型目录与短期 task token；task token 只在 Python 私有代理，Node 只取得当次 loopback token。每次模型请求仍由代理生成独立 operation UUID；工具往返共享 invocation，保留原预占、可信用量结算、幂等与 pending_reconciliation 规则。供应商业务密钥仅在服务器。

集中声明引擎能力；Pi 支持 custom 和 central subscription，Codex 原有 central／legacy 兼容保留。前后端使用能力声明，不能只扩大下拉框。旧版订阅会话保持原引擎与归属，Pi 不冒充 legacy 适配完成。

## 分发与完整性

独立 `EASEL_PI_RUNTIME_MANIFEST` schema 2 描述 Node、worker、Pi 版本、平台／架构及完整 payload 哈希。无效清单失败关闭，不回退系统 Node 或在线安装。保留 Codex schema 1 的旧语义。开发准备过程下载带官方 SHA256 核对的固定 Node 发行包，用固定 npm lock 和 integrity 安装依赖，禁用 lifecycle scripts；安装包构建离线使用已准备的输入。

打包清单明确枚举当前公共 Python／Web／技能源，涵盖本分支未跟踪文件并记录哈希，拒绝私有状态和环境文件。前端 build receipt 使用同一公共输入规则。运行时和 npm 依赖许可证随包携带。启动器在用户 Application Support 建工作区，默认 Pi，应用资源只读，自有实例认证和停止机制沿用。

## 验收

1. 单测先失败再实现：runtime 完整性、流式、工具权限、会话隔离、取消、错误清理及禁重试。
2. 前后端验证默认 Pi、双来源、中央模型目录、供应商 Key 保护及旧会话兼容。
3. 实际 `.app` 在临时 HOME 和最小 PATH 下启动，使用随包 CPython／Node，无全局 Agent、pip/npm 安装和外部配置；执行真实本地工具。
4. 使用用户已授权的服务器内 New API 业务令牌。独立非商业验收钱包／schema，真实 Pi 文本→工具→回传→最终响应，按真实 token 用量对账；相同操作重查不新增模型请求或扣点。真实 custom 模式使用服务器侧代理，业务密钥不出服务器。
5. 原待核算操作与未知用量预算不修改，不伪造付款／正式会员，不将夹具费率当人民币或商业定价。
6. 核对本轮开始 269 个既有脏路径、根 `.env`、HEAD、main 与六个 SQL 迁移。明确报告本机包、浏览器、真实模型、账本、签名与平台范围。
