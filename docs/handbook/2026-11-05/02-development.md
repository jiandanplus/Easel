# 02 · 开发手册

本手册记录现有工程入口。命令是供具备对应环境的开发者使用的操作配方，本次文档编写没有安装依赖、运行测试或构建软件。

## 1. 代码与版本管理

主仓库为 `jiandanplus/Easel`，上游为 `ZJU-REAL/Easel`。当前开发分支 `codex/ui-redesign`，HEAD `e972af0`。大量开发内容尚在未提交工作区；仅记录 HEAD 不能复现当前版本，交接时还需要完整公开源码清单与包哈希。

此前合并请求已撤销。继续工作前先记录分支、工作区、暂存区和对应发布制品；不要为清理环境使用 `git reset --hard`、`git clean`，也不要将未完成候选自动合并到 main。后续合并、推送和生产发布分别执行。

```bash
rtk proxy git branch --show-current
rtk proxy git status --short
rtk proxy git diff --stat
rtk proxy git diff --cached --stat
```

项目规则以当次 AGENTS.md 为准：命令使用 RTK；库、SDK、CLI 配置问题使用 Context7 获取当前官方文档；只有仓库存在 `.codegraph/` 时才先使用 CodeGraph。开发记录不得带入私钥、完整 DSN、真实会话或用户文件。

## 2. 环境和目录

| 范围 | 开发要求／现有合同 |
| --- | --- |
| 本地 Python 项目 | 根 `pyproject.toml` 声明 Python ≥3.10；桌面固定运行环境另有清单 |
| 中央服务 | Python ≥3.12 且 <3.13；`services/platform/uv.lock` 锁定依赖 |
| 前端 | 工作台与 Console 分别有 package-lock；使用各自锁定版本 |
| Pi | `runtime/pi/package-lock.json`；交付基线 Pi 1.4.0、Node 24.22.1 |
| 数据库测试 | 自有临时 PostgreSQL 16；数据库名 `easel_s1_test` 前缀，literal loopback |
| macOS 交付 | 当前 macOS 15+ Apple Silicon 预览，Python 3.12.13 |

```text
Easel/
├── easel/                     CLI、Agent、平台桥与本地状态
├── web/app.py                 工作台 FastAPI
├── web/frontend/              工作台 React / TypeScript / Vite
├── runtime/pi/                Pi worker 与锁定 SDK
├── services/platform/
│   ├── src/platform_service/  中央 API、Console、财务、媒体、备份
│   ├── frontend/             独立 Console
│   ├── scripts/              构建、部署与验证工具
│   ├── deploy/               systemd、公开配置与历史运行手册
│   └── tests/                中央单元／协议／PostgreSQL 测试
├── integrations/newapi/       插件、客户端与运营工具
├── skills/                    业务技能与共享脚本
├── packaging/pi/              桌面来源策略、库存与许可证
├── scripts/packaging/         桌面构建工具
├── tests/                     本地后端与前端测试
└── docs/                      设计、计划、运维与验证记录
```

源码中的 `easel_platform` 是旧 v1 SQLite 开发平台，不替代当前 PostgreSQL 中央 v2。旧 Codex/OpenClaw 兼容入口也不代表默认桌面 Agent 已变回这些引擎。

## 3. 独立开发工作台

优先使用已有开发环境；新建环境应在单独检出／目录准备，避免覆盖原 17868 工作台与真实工作区。源码开发可安装根项目依赖：

```bash
rtk proxy python3 -m venv .venv
rtk proxy .venv/bin/python -m pip install -e .
rtk proxy npm ci --prefix web/frontend --ignore-scripts
rtk proxy npm --prefix web/frontend run build
```

根项目依赖比较宽，媒体和浏览器等可选能力还有独立前提。以上安装不等于桌面完整媒体环境。Pi 的专用运行环境通过后面的准备工具建立，不能只安装 Python 就认为 Agent 已可运行。

对新预览显式指定独立 HOME／工作区，资源目录指向源码；使用未占用端口，例如 17870。`/ABS/...` 须先替换为真实新目录，不能指向用户现有工作区：

```bash
rtk proxy mkdir -p /ABS/NEW/easel-dev/home /ABS/NEW/easel-dev/workspace
rtk proxy env HOME=/ABS/NEW/easel-dev/home \
  EASEL_WORKSPACE_ROOT=/ABS/NEW/easel-dev/workspace \
  EASEL_RESOURCE_ROOT=/Users/jiandan/Code/GitCode/Easel \
  .venv/bin/python -m easel web --port 17870
```

启动页为 `http://127.0.0.1:17870/`。上例是独立 Web 开发启动，不承诺新空工作区已具备全部技能种子、Pi payload、登录和模型配置。按相应入口逐项准备并验证；不要借用用户会话或自动调用付费模型。

## 4. 中央服务开发

```bash
rtk proxy uv sync --project services/platform --python 3.12 --frozen --group test
rtk proxy uv run --project services/platform --frozen python -m platform_service --help
```

准备独立测试数据库和私有环境文件，迁移 owner 与 runtime 分离。`migrate` 不由 Web 启动自动执行；`register-product` 用于新环境注册产品；已有恢复环境不重复初始化钱包或发点。命令及真实目标应按 [中央服务说明](../../../services/platform/README.md) 核对。

开发配置主要包括 `CENTRAL_DATABASE_URL`、`CENTRAL_DB_SCHEMA`、`CENTRAL_PUBLIC_URL`、`CENTRAL_OIDC_ISSUER`、`CENTRAL_OIDC_CLIENTS`，模型另用 `CENTRAL_EXECUTION_CONFIG_FILE`／`CENTRAL_MEDIA_CONFIG_FILE`。只有专用 loopback 演练启用 `CENTRAL_ALLOW_LOOPBACK_HTTP=1`。Console 单独使用 `CENTRAL_CONSOLE_*` 配置，不回退产品或 owner 凭据。

## 5. 开发一项新能力

按以下顺序做小范围变更：

1. 写明触发条件、输入、输出、账号／产品归属、取消与未知状态、费用单位和验收门槛。
2. 确定本地工具还是中央模型能力；自定义与订阅两条路径分别设计，禁止因订阅缺能力自动借用自定义 Key。
3. 先写能覆盖旧问题或新合同的测试，再实现最小改动。网络、文件、数据库和工具权限各自验证。
4. 如需数据库变更，追加新迁移，保留旧迁移逐字节内容；同步 runtime／Console 最小授权及权限守卫。
5. 按“纯逻辑 → 合成协议 → 实际隔离 → 真实供应商 → 目标包／生产”分级验收。记录失败与不确定状态。
6. 更新版本、能力目录、验收引用和文档，再准备发布包。前端产品界面保留直接可操作的文案，开发解释放文档。

## 6. 模型和推理强度的开发规则

订阅模型目录至少经过供应商渠道／令牌、中央 provider 映射、已发布模型合同、启用状态、产品权益与费率这几层。前端从当前账号目录读取，不直接列出 New API 所有模型，也不在后台固定一个模型替代用户选择。

推理强度从明确元数据读取，例如 `reasoning.efforts`；自定义目录兼容已定义的 `supported_reasoning_efforts`。未知和“不支持”不同；不按模型名字猜测。默认不额外发送强度参数。新档位要验证 Pi SDK 到真实上游的最终报文、工具后续轮次以及超限拒绝。

文本发布 CLI 在 [EXECUTION.md](../../../services/platform/EXECUTION.md)。该文件保留早期 Codex 阶段说明，协议／CLI 合同可查，但默认 Agent 与真实部署状态以本手册和后续验证记录为准。媒体发布采用 [MEDIA.md](../../../services/platform/MEDIA.md) 的独立合同；当前没有通用 `media-model-publish` CLI，不能套用文本命令。

新媒体适配必须覆盖：单次提交、任务 ID、轮询终态、取消后查询、产物下载、类型与完整解码、尺寸／时长／声道计量、归属、幂等和费用未知路径。HTTP 200 或插件安装成功不足以开放能力。

## 7. 测试命令与证据

工作台前端及本地隔离测试：

```bash
rtk proxy node --test tests/frontend/*.test.mjs
rtk proxy npm --prefix web/frontend run lint
rtk proxy npm --prefix web/frontend run build
rtk proxy .venv/bin/python scripts/validation/run_isolated_tests.py tests -q --tb=short
```

最后一条需要开发环境已有 pytest；脚本使用新 HOME／工作区及受限代理，不能以此证明操作系统级出口隔离或所有外部组件可运行。缺少明确依赖时先准备专用环境，不向已打包不可变运行时安装测试包。

Console：

```bash
rtk proxy npm ci --prefix services/platform/frontend --ignore-scripts
rtk proxy npm test --prefix services/platform/frontend
rtk proxy python3 services/platform/scripts/build_console.py
```

中央 PostgreSQL 测试，DSN 文件须 0600、位于仓库外并明确指向自有临时数据库：

```bash
rtk proxy env EASEL_CENTRAL_TEST_DSN_FILE=/ABS/PRIVATE/easel-test-dsn \
  uv run --project services/platform --frozen --group test \
  pytest services/platform/tests -q
```

不能指向生产、原身份库或用户工作区。缺 DSN 不能改为跳过并宣布通过。B3 和原生组件另有固定来源、磁盘及独占目标要求，不因上述全套测试命令而自动具备执行条件。

每份测试报告记录源文件哈希、命令、原进程退出码、通过／失败／跳过数、实际范围与未验证项。不同版本或重叠套件不相加为总体覆盖率。

## 8. macOS Pi 打包

按 [Pi 包说明](../../../packaging/pi/README.md) 准备经过固定来源校验的 Python、wheel 与 Pi payload。构建输入均使用新目录：

```bash
rtk proxy python3 scripts/packaging/prepare_pi_runtime.py --output /ABS/NEW/pi-payload
rtk proxy python3 scripts/packaging/build_pi_macos.py --freeze-source-only
rtk proxy python3 scripts/packaging/build_pi_macos.py \
  --inputs /ABS/PREPARED/PYTHON --pi-root /ABS/NEW/pi-payload \
  --output /ABS/NEW/easel-delivery
```

`--freeze-source-only` 是评审后冻结公开来源清单的写操作，不能为了消除构建报错随意刷新；应先构建当前前端并更新相应 receipt，再确认公开来源策略没有用户数据。实际构建必须匹配 source-manifest、前端 receipt、运行时 provenance 和完整库存。包内许可证须保留。

交付核对 ZIP 安全条目与 CRC、逐文件哈希、资源来源、无凭据、新 HOME 启停、真实工具／点数、旧工作区接续。当前包以浏览器展示 UI，不宣称已经是独立原生窗口。签名、公证、Gatekeeper 和独立净机另验。

## 9. 变更完成标准

- 功能和错误恢复符合设计；订阅与自定义接口均保留。
- 影响范围测试有本次终态证据，关键独立审查已收口。
- 发布候选与源码清单一致，旧工作区、登录与历史保护方案明确。
- 迁移、服务切换和资金变化有各自具体步骤与审计。
- 文档明确“已构建、已激活、已部署、真实验收、正式发行”分别到哪一步。
