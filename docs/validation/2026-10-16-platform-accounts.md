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

首次交付后端全量1541通过／6跳过／1项既有warning（67.18秒）；所有 Python 单元测试均使用 `scripts/validation/run_isolated_tests.py` 的临时 HOME／工作区和跟踪的公开 seed，不调用真实模型、原生账号或现有 profile。

```sh
rtk proxy .venv/bin/python scripts/validation/run_isolated_tests.py --pytest-path /opt/anaconda3/lib/python3.12/site-packages tests -q --tb=short
rtk proxy node --test tests/frontend/*.test.mjs
rtk proxy npm --prefix web/frontend run lint
rtk proxy npm --prefix web/frontend run build
rtk proxy ruff check scripts/validation/verify_platform.py --output-format concise
rtk proxy git diff --check
```

首次交付前端130／130通过；lint退出0，既有 linkifyOutputs.ts:27 的 no-useless-escape warning仍存在；构建通过，既有大于500kB chunk提示仍存在。首次后端全量1539通过／6跳过／2失败，失败为 CLI新增 proxy_headers=False 期望未更新，以及队列取消测试假设单个事件循环 tick 足够，而接受任务已异步offload；分别更新显式参数期望及等待真实排队 activity 的 Event，不修改产品逻辑。相应45项回归通过。既有 doctor.py:102 的 python_multipart PendingDeprecationWarning保留。

工具版本：Python3.12；Node24.14.0；Codex0.146.0；Vite8.1.4；FastAPI0.143.1、HTTPX0.28.1、Uvicorn0.54.0。新前端文件提交后重新构建，使 Vite源码回执包括它们。

## 后续边界

未验收真实上游模型授权／视频图片产物、正式支付、公开托管／安全运营、邮箱验证及恢复、云端任务执行、Windows账号客户端或重新打包的macOS桌面包。此前发行包仍是历史产物，不能据本次源码验证宣称已有新账号功能。启动、测试权益和本机复验步骤见 [平台说明](../platform/README.md)。


## 最终复核修订：普通原始凭证输出边界

最终复核发现：同一 OS 用户下，原生工具可以普通 `cat`／`cp` 读取规定位置 `outputs/_harness/platform-session.json`（0600）；权限位不能隔离原生工具与本机桥。此修订保留该位置与信任关系，使用有界、仅进程内的敏感值上下文，在会话读写、刷新旋转、退出与原生准备时保留已观察的 access／refresh／task bearer 及既有 child credential。账号凭证不进入子进程环境、argv、RunRequest 新字段或公开 metadata。Codex 与 ACP/OpenCode 共用增量遮蔽，覆盖 token／thinking 分片、权限问题及协议／完成错误；当前 task bearer 遮蔽继续保留。

公开 `/api/output`、`/api/media`、项目 `.easel.json` 展示与 ZIP 索引使用同一安全快照：macOS／Linux 逐组件用目录描述符拒绝符号链接／特殊文件；不支持目录描述符的平台使用普通文件兼容分支，对父目录、文件的 lstat／fstat 身份和 reparse 标记进行读前后验证，无法验证则拒绝。两条路径均固定已打开的普通文件，检查读前后修改状态，复制到内存最多1MiB、超出转私有0600临时文件的 spool，扫描全部原始字节后才返回；服务始终读取该已扫描快照。原文件后续替换不改变响应。先扫描完整文件再处理 Range，支持单区间／最多16个多区间；正常完成、发送失败、断线取消、扫描中取消均关闭快照。含已知原始凭证、敏感文件名／别名或普通凭证 JSON 值的产物拒绝，清洁文本／媒体／Range 下载保持可用。文本 JSON 接口限8MiB，较大清洁文件仍可用媒体下载接口；metadata JSON限64KiB。

普通 ZIP／Office 压缩诊断也检查解压成员，不将“压缩复制的会话文件”当作任意编码豁免：中央目录最多2MiB、1000个成员、解压合计64MiB，不解压落盘。加密／ZIP64／嵌套 ZIP／不安全或超限压缩包拒绝，不回退到未检查下载。普通媒体不因固定扫描内存上限而设文件大小上限，但每次 Range 请求会先复制、扫描完整文件，增加磁盘 I/O 与首字节等待；大文件需足够临时磁盘空间。

敏感上下文每工作区最多256个值、单值最多64KiB，全进程最多512个根与8MiB敏感值原始字节；旋转／退出不驱逐旧值，达到上限时明确失败并要求重启。上下文不写入日志或持久化历史；重启后当前会话重新读取，普通 access_token／refresh_token JSON 复制仍按凭证值识别，已撤销旧凭证的纯 token 复制若未被新进程观察，则不保证识别。操作系统同用户任意读取、模型输入中私有文件读取、任意编码／转换／隐写泄漏均不属于此防护声明；此实现不是原生 OS 沙箱或任意同用户数据外传防护。

同时修复订阅目录 pending 后切回自定义服务导致原生“拉取模型”卡住：订阅加载状态独立。账号权益、目录、用量与设备独立读取；未加载／失败的权益显示未知与重试提示，只有成功读取的空列表显示“当前无测试权益”，不会因目录或用量失败伪造零授权。

本轮没有重复真实原生模型验收或访问现有7860；新增验证均使用隔离测试运行器的临时 HOME／工作区与合成凭证、mock RPC 及实际本机 Web 路径。该范围与上文历史 native／浏览器证据分别记录。


平台兼容分支在 macOS 通过合成 capability 切换验证普通匿名产物与 Range 保持可用，符号链接／reparse 标记／打开时文件替换被拒绝；未在真实 Windows 主机执行此验证，不新增 Windows 账号支持声明。自定义匿名原生运行不构造平台 HTTP 客户端，不创建账号文件，也不需要 fcntl。临时 spool 目录若配置在公开 outputs 内则明确拒绝，避免把扫描中的私有快照放入公开目录。


本轮最终检查：后端全量 **1567通过／6跳过／1项既有 warning，67.99秒**，对应主体实现与26项新增隐私检查；其后仅增加兼容 capability 分支与公开临时目录防护，重新执行完整覆盖集 **289通过／1项同一 warning，5.36秒**（含最终30项隐私检查）。未因这两项有界后续修订重复后端全量。完整前端 Node **134通过／0失败，634ms**；lint／TypeScript＋Vite build退出0，仍只有前述既有 warning。新隐私模块、session、共享redactor和隐私测试的 ruff通过，git diff --check通过，12项外观文件 SHA256仍与冻结清单逐项一致。

本轮精确覆盖命令（所有 Python 均通过隔离运行器）：

```sh
rtk proxy .venv/bin/python scripts/validation/run_isolated_tests.py --pytest-path /opt/anaconda3/lib/python3.12/site-packages tests/test_platform_client.py tests/test_subscription_execution.py tests/test_agent_harnesses.py tests/test_codex_completion_cleanup.py tests/test_bundled_provider.py tests/test_direct_api.py tests/test_output_privacy.py tests/test_output_paths.py tests/test_output_delete_protect.py tests/test_artifact_archive.py -q --tb=short
rtk proxy .venv/bin/python scripts/validation/run_isolated_tests.py --pytest-path /opt/anaconda3/lib/python3.12/site-packages tests -q --tb=short
rtk proxy node --test tests/frontend/*.test.mjs
rtk proxy npm --prefix web/frontend run lint
rtk proxy npm --prefix web/frontend run build
rtk proxy ruff check easel/platform/privacy.py easel/platform/session.py easel/harnesses/secrets.py tests/test_output_privacy.py --output-format concise
rtk proxy git diff --check
```


## OpenClaw 输出边界与大文件扫描响应性补正

现有自定义 OpenClaw 继续可用；本机保存平台会话后切回自定义服务也适用同一保护。Web 的公共事件出口现在使用共享 StreamRedactor，动态读取同一仅进程内敏感值上下文及当前 `.env` 凭证；token／thinking 跨分片遮蔽后才写浏览器队列、事件 JSONL 和完整回答累积。error／done 和 supervisor 取消清理均先 flush，完整回答、last-turn、事件回放和诊断 stdout/text tail 保持遮蔽。同步 OpenClaw stdout、stderr 和异常也在返回或截断前遮蔽，启动前先观察当前账号值；不向子进程 env、argv 或 metadata 注入账号凭证。

OpenClaw CLI reader 与 HTTP gateway 使用同一出口；HTTP 协程的增量直接入队，保证同步完成的模拟提供者也在终止 sentinel 前保留全部正文／思考，线程 reader 继续使用线程安全入队。`run_agent_sync` 是 Web 的 CLI runner seam，本轮以实际 handler＋模拟 subprocess 覆盖；独立 `easel chat` TUI／`easel skill` CLI 未被 Web 调用，也不写 Web 浏览器公开日志，没有扩展终端托管范围。

`/api/output` 与 `/api/media` 复用同一可取消 worker 快照生命周期：全文件复制、原始扫描和 ZIP 检查移到线程；取消等待时仍在 worker 返回后关闭其快照。合成阻塞扫描期间，实际异步 stop／last-status handler 可继续执行。全文件扫描的磁盘 I/O、临时空间与首字节延迟成本仍存在，原有扫描上限和同用户／任意编码边界不变。

补正回归：隔离后端全量 **1583通过／6跳过／1项既有 warning，68.89秒**；最后覆盖集 **271通过／1项同一 warning，4.62秒**，含 **42项隐私检查**。覆盖集在全量启动后补充的 HTTP 错误先遮蔽后截断和完整会话 JSON cat 断言后重新执行；不重复无新失败的全量。完整前端历史 **134通过**，本轮仅修改后端／测试／本文档，未重跑 Node／构建，也未改动前端源与回执。ruff 隐私测试检查与 git diff --check 通过；12项冻结外观 SHA256和 main ref 均保持一致。

```sh
rtk proxy .venv/bin/python scripts/validation/run_isolated_tests.py --pytest-path /opt/anaconda3/lib/python3.12/site-packages tests/test_output_privacy.py tests/test_direct_api.py tests/test_agent_harnesses.py tests/test_codex_completion_cleanup.py tests/test_platform_client.py tests/test_subscription_execution.py tests/test_output_paths.py tests/test_output_delete_protect.py tests/test_artifact_archive.py -q --tb=short
rtk proxy .venv/bin/python scripts/validation/run_isolated_tests.py --pytest-path /opt/anaconda3/lib/python3.12/site-packages tests -q --tb=short
rtk proxy ruff check tests/test_output_privacy.py --output-format concise
rtk proxy git diff --check
```

新增检查为真实 Web 路径的合成 CLI raw stream／HTTP MockTransport、同步 subprocess mocks：完整普通 cat、split token／thinking、stdout fallback、错误、停止及实际 supervisor 取消 flush、事件持久化／回放／last-turn、正常文字保留、子进程不含账号值及扫描取消关闭。没有真实 OpenClaw／Codex 推理、外部网络、账号或画像访问；没有修改既有7860、推送、合并、部署或创建新桌面包。前述原生与浏览器验收仍是历史证据。
