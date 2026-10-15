# Easel 开发平台服务

本阶段提供独立的 `easel_platform` Python 包、开发账号、运营者授予的测试权益、文本 Responses 网关和 token 账本。注册成功不会获得模型额度。没有正式套餐价格、订单、支付、邮箱验证或找回密码，也没有公开部署。

平台只处理身份、授权与模型转发，不能执行本机命令，不读取 Easel 用户工作区。工具仍由客户端本机的 Codex 执行；当前订阅来源仅支持 Codex／本机，云端与订阅媒体通道未开放。自定义来源无需平台账号，原有供应商配置单独保存。

## 本机启动与测试权益

从仓库根目录使用已安装项目依赖的 Python 环境运行。以下是独立开发预览；在两个终端使用同一个临时目录路径，端口如有冲突请同时调整 `EASEL_PORT` 与 Uvicorn 端口。避免使用已有的 7860 服务。

```sh
export EASEL_PREVIEW_ROOT="$(mktemp -d /tmp/easel-account-preview.XXXXXX)"
rtk proxy mkdir -p "$EASEL_PREVIEW_ROOT/home" "$EASEL_PREVIEW_ROOT/workspace"
rtk proxy env HOME="$EASEL_PREVIEW_ROOT/home" .venv/bin/python -m easel_platform serve --database "$EASEL_PREVIEW_ROOT/platform.sqlite" --host 127.0.0.1 --port 18762
```

另一终端先把 `EASEL_PREVIEW_ROOT` 设为上一步实际路径，再运行：

```sh
rtk proxy env HOME="$EASEL_PREVIEW_ROOT/home" EASEL_WORKSPACE_ROOT="$EASEL_PREVIEW_ROOT/workspace" EASEL_RESOURCE_ROOT="$PWD" EASEL_PORT=18761 .venv/bin/python -c 'import uvicorn; from web.app import app; uvicorn.run(app, host="127.0.0.1", port=18761, proxy_headers=False)'
```

浏览器打开 `http://127.0.0.1:18761`，进入设置 → 账号与订阅，保存 `http://127.0.0.1:18762`，使用合成邮箱与 15–128 字符密码注册。平台入口不接受浏览器直接发起的 Origin 请求，浏览器只访问同源本地桥。两个服务均显式关闭代理头重写；账号和订阅本地 HTTP 权限依据真实 loopback peer，不能依赖 X-Forwarded-For。

注册后，在运营者终端创建并授予测试权益，然后点击界面“刷新账号状态”：

```sh
rtk proxy env HOME="$EASEL_PREVIEW_ROOT/home" .venv/bin/python -m easel_platform plan create --database "$EASEL_PREVIEW_ROOT/platform.sqlite" --id preview-test --name '本机预览测试权益' --tokens 100000 --concurrency 1 --days 1 --model easel-test
rtk proxy env HOME="$EASEL_PREVIEW_ROOT/home" .venv/bin/python -m easel_platform grant --database "$EASEL_PREVIEW_ROOT/platform.sqlite" --email synthetic@example.invalid --plan preview-test --key preview-one
```

`--key` 是运营操作幂等标识，不是模型密钥。重复相同授予操作不会延长或重复额度。数据库路径是独立服务数据，不应置于可下载输出、工作区或桌面发行包中。用 Ctrl-C 停止各自启动的服务；预览数据由操作者按自己的临时目录生命周期删除。

未配置上游时可以验收账号和权益界面，但模型网关明确显示尚未配置。`EASEL_PLATFORM_UPSTREAM_URL`、`EASEL_PLATFORM_UPSTREAM_KEY`、`EASEL_PLATFORM_UPSTREAM_MODEL`、`EASEL_PLATFORM_PUBLIC_MODEL` 仅由独立平台服务进程读取。上游必须 HTTPS，开发 dummy 可用 loopback HTTP。密钥不能配置到浏览器或客户端发行包。本阶段没有使用真实上游；完整非计费闭环使用下面的专用脚本。

## 隔离闭环验收

```sh
rtk proxy .venv/bin/python scripts/validation/verify_platform.py --codex /absolute/path/to/native/codex
rtk proxy .venv/bin/python scripts/validation/run_isolated_tests.py --pytest-path /path/to/pytest/site-packages tests -q --tb=short
rtk proxy node --test tests/frontend/*.test.mjs
rtk proxy npm --prefix web/frontend run lint
rtk proxy npm --prefix web/frontend run build
```

`verify_platform.py` 在 macOS 使用临时 HOME、工作区、SQLite、随机 loopback 端口和假上游密钥，启动自己拥有的两个 Uvicorn 服务和 dummy Responses 服务。它通过真实本地 HTTP 注册／登录桥、运营 CLI、原生 Codex、Python 工具文件与 SSE 验证服务端账本，检查无权益、额度、到期、撤销、刷新和跨账号拒绝，以及自定义配置字节不变。最终清理自己的服务及原生进程并删除临时数据，不访问现有 profile 或真实模型服务。

Python 服务与探针拒绝非 loopback socket；原生 app-server 使用显式 loopback provider、关闭外部代理目标和禁用 hosted web search。工具保留 Codex workspace-write 与 networkAccess=False，脚本实际验证对拥有的工作区外文件和保留测试地址 192.0.2.1 的访问被沙箱拒绝。此证据不声称原生 app-server 具有操作系统级全进程出口隔离。外层 Seatbelt 与原生沙箱叠加会产生 sandbox_apply exit71，因此不采用这种会破坏原生工具的探针包裹方式。

## 数据与异常含义

- 当前可用额度只统计此刻生效的 active grants；累计已用来自全部历史账本，过期权益不会虚增可用额度。
- 预留 token 是保守准入预算，不能作为实际消耗。待核算请求保留预留，不假装零消耗；由运营者凭测量数据使用 `reconcile` 命令幂等结算。
- 设备撤销立即使关联访问／刷新／任务授权失效。本机仍有失效凭证时，界面提供“清除失效登录”后重新登录；网络故障不自动销毁本地会话。
- 密码只在提交时使用，提交后清空；平台 bearer 仅保存在本地私有 `outputs/_harness/platform-session.json`（0600、父目录0700），不回传浏览器，不写 localStorage。任务 bearer 仅进入本机原生子进程环境。
- 已接受或运行任务期间，账号、来源、模型和服务器的变更由本地服务拒绝。历史订阅会话不能切到另一个账号继续执行。
- 网关仅允许有界、无远端上下文的文本和客户端执行工具。支持 Codex 的一层 namespace 内 function/custom 工具；媒体、hosted search、嵌套 namespace、远端 conversation、请求体和输出超限仍拒绝。

本阶段验证记录见 [账号与订阅验收](../validation/2026-10-16-platform-accounts.md)。真实模型产物、正式支付、公开服务运营、邮箱验证／恢复、云端执行、Windows 账号客户端和新桌面发行包均未在此阶段验收。
