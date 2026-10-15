# 第三阶段账号、测试权益与网关验收

日期：2026-10-16；分支 `codex/ui-redesign`。本阶段源码在本地提交，未合并、推送、部署或新增桌面安装包。十二项既有日夜主题修改保持原始字节与未暂存状态。平台独立后端与本机工具执行器分离，正式商业订阅和云端执行尚未交付。

## 已验证的交付

设置新增“账号与订阅”：明确配置平台地址、合成邮箱注册／登录、邮箱未验证、零权益、服务端真实测试套餐、active grants 当前额度、历史实际消耗、预留与待核算、到期／撤销、设备与退出。密码提交即清空，浏览器不存平台 bearer。失效本机会话提供显式清除恢复入口，网络错误保持会话并允许重试。

底座设置独立选择自定义／订阅来源，本机执行始终明确，云端按钮禁用并说明原因。订阅模型来自平台授权目录，仅 Codex 可保存；切换来源不会自动替换执行引擎或覆盖自定义模型／供应商。无网关、无权益或错误均显示具体中文原因；前端选项不授予权限。

真实 Codex 0.146.0 的请求包含 namespace 函数工具及默认 hosted web_search。最初闭环在上游调用前以 unsupported_request_shape 拒绝，未绕过网关检查。新增 RED 回归后仅允许一层 namespace 内客户端 function/custom，显式给订阅 Codex 设置 web_search="disabled"；媒体、hosted 工具、嵌套 namespace、远端上下文和额度／输出上限保持拒绝。

## 原生本机闭环

```sh
rtk proxy .venv/bin/python scripts/validation/verify_platform.py --codex /Users/jiandan/.nvm/versions/node/v24.14.0/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex
```

脚本使用独立临时 HOME／工作区／平台 SQLite、随机 loopback 端口、假密钥、真实 Uvicorn（proxy_headers=False）和本地 HTTP Origin。实际路径为注册账号 → 本地桥私有会话 → 无权益拒绝 → 运营 CLI 幂等授予测试权益 → 真实模型目录 → 刷新旋转 → 接受订阅任务 → 原生 Codex → 平台 Responses 网关 → dummy Responses → 本机 Python 文件 → 工具结果 → SSE token/done → 账本结算。

通过结果：2 次模型请求、2 条 settled 记录；每次 dummy 提供 input1/output1，合计实测4 tokens，预留0，待核算0。Python marker 文件和工具回传均匹配预期。无权益、配额不足、过期、撤销、跨账号设备／用量／原生历史访问均拒绝且无自定义回退；原始自定义配置字节保持不变。独立平台拒绝浏览器 Origin，公开输出／SSE 未泄漏假密码、访问／刷新／上游／自定义密钥。私有 Codex home0700，探针拥有的服务与原生 PID 均退出，临时数据删除。

Python 网络连接有 loopback 限制；原生使用指定127.0.0.1 provider、封闭的 HTTP／HTTPS／ALL_PROXY 127.0.0.1:9 和 NO_PROXY loopback。原生工具保持 workspace-write／networkAccess=False，并实际拒绝写入拥有的工作区外目录和连接保留测试地址192.0.2.1（errno1/13）；没有向真实外部服务发送探测。原生 app-server 的路由／代理限制属于应用层防护，不声称操作系统级全进程出口隔离。

初始尝试外层 Seatbelt 限制原生进程出口，工具启动时出现 `sandbox-exec: sandbox_apply: Operation not permitted`／exit71；移除外层包裹后原生工作区沙箱正常，不改变产品权限或采用 danger-full-access。该叠加限制也见 [OpenAI Codex 相关问题](https://github.com/openai/codex/issues/45657)。它是探针沙箱嵌套限制，不是对产品沙箱边界的豁免。

本地可核对 JSON：`outputs/_run/phase3-validation/platform-proof.json`。该文件不包含真实凭证；输出目录是本机验收产物，不作为发行包或生产记录。

## 浏览器验收

父任务使用自己的临时 HOME／工作区／SQLite 和独立 loopback 端口，通过真实浏览器操作设置入口、未配置地址禁用、注册零额度、运营授予后刷新100000、退出／错误密码／正确登录、设备撤销、失效登录清除后重登录、API引擎下选择订阅不自动换引擎且无法保存、Codex预览和云端禁用。Theme A 日间与 Theme C 夜间布局均检查，并重载最终构建验证中文提示修订；没有通过浏览器调用真实模型或支付。父任务关闭其专属浏览器标签、停止所拥有的预览进程并删除临时数据；原有7860服务未改动。

截图：`outputs/_run/phase3-validation/account-light.jpg`、`outputs/_run/phase3-validation/account-dark.jpg`。浏览器发现当前设备撤销后本机会话仍保留、登录被拒且没有清除入口；新增行为回归 RED 后修复，并由父任务实际验证清除 → 错误密码提示及清空 → 正确登录恢复100000额度。

## 回归命令与结果

最终后端全量1541通过／6跳过／1项既有warning（67.18秒）；所有 Python 单元测试均使用 `scripts/validation/run_isolated_tests.py` 的临时 HOME／工作区和跟踪的公开 seed，不调用真实模型、原生账号或现有 profile。

```sh
rtk proxy .venv/bin/python scripts/validation/run_isolated_tests.py --pytest-path /opt/anaconda3/lib/python3.12/site-packages tests -q --tb=short
rtk proxy node --test tests/frontend/*.test.mjs
rtk proxy npm --prefix web/frontend run lint
rtk proxy npm --prefix web/frontend run build
rtk proxy ruff check scripts/validation/verify_platform.py --output-format concise
rtk proxy git diff --check
```

前端130／130通过；lint退出0，既有 linkifyOutputs.ts:27 的 no-useless-escape warning仍存在；构建通过，既有大于500kB chunk提示仍存在。首次后端全量1539通过／6跳过／2失败，失败为 CLI新增 proxy_headers=False 期望未更新，以及队列取消测试假设单个事件循环 tick 足够，而接受任务已异步offload；分别更新显式参数期望及等待真实排队 activity 的 Event，不修改产品逻辑。相应45项回归通过。既有 doctor.py:102 的 python_multipart PendingDeprecationWarning保留。

工具版本：Python3.12；Node24.14.0；Codex0.146.0；Vite8.1.4；FastAPI0.143.1、HTTPX0.28.1、Uvicorn0.54.0。新前端文件提交后重新构建，使 Vite源码回执包括它们。

## 后续边界

未验收真实上游模型授权／视频图片产物、正式支付、公开托管／安全运营、邮箱验证及恢复、云端任务执行、Windows账号客户端或重新打包的macOS桌面包。此前发行包仍是历史产物，不能据本次源码验证宣称已有新账号功能。启动、测试权益和本机复验步骤见 [平台说明](../platform/README.md)。
