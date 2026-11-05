# 官方插件安装工具

本目录提供本地操作工具。**工具和模拟 HTTP 测试完成，不表示服务器已升级或插件已在线安装。** 线上结果必须以目标服务器的插件列表、源码哈希和运行时读回为准。安装成功也不代表供应商密钥、渠道、价格或真实推理已配置和验证。

生产备份与宿主升级脚本见 [UPGRADE.md](UPGRADE.md)，默认只读 plan，只有 `--apply` 执行备份或升级。

当前工具针对 `../catalog.lock.json` 锁定的 New API `v1.0.0-rc.45` 管理协议。该版本名称仍是 release candidate。锁文件包含 13 个官方市场插件；此宿主内置 11 个完全相同版本与源码哈希的 factory 插件，另外两个是 Incho、TypeSafe。工具根据目标服务器实际列表判断是否需要上传，不会重复上传已匹配的 factory 插件。

## 使用

从 Easel 仓库根目录运行，Python 3.10+。只有启用密码加密登录时需要 `cryptography`，Easel 已声明该依赖；工具仅在该登录分支导入它。网络、JSON、哈希校验和测试使用 Python 标准库。

使用已由管理员准备好的私密 env 文件，文件必须归当前执行用户所有、权限为 `0600`，不能是符号链接。支持以下两种内容之一：

```dotenv
ADMIN_USERNAME='root'
ADMIN_PASSWORD='由管理员在私密文件中填写'
```

也可以使用**已存在**的控制台会话 access token 或 root 管理 PAT：

```dotenv
ADMIN_ACCESS_TOKEN='已存在的控制台会话或管理PAT'
```

不要把凭据粘贴到命令行或仓库文件。文件按字面 dotenv 解析，可加 `export`；不会通过 shell `source`、变量替换、反引号或命令替换执行。含空格、`#` 或其他 shell 字符的值应使用引号。仅接受上面三个变量，不读取进程环境中的同名值。

默认执行 `plan`，读取服务器状态与配置、下载固定提交中的源码和图标、校验 SHA256 并逐项报告，不上传或激活插件：

```bash
rtk proxy python3 integrations/newapi/ops/install_plugins.py \
  --base-url https://api.drawbookai.cn \
  --credentials-file /absolute/private/path/new-api-admin.env
```

密码认证会调用 `/api/user/login`，因此服务器会创建常规登录会话；plan 不修改插件、价格或渠道配置。已有 token 路径避免发起密码登录。新版本使用 `Authorization: Bearer`，工具不会使用旧版 cookie / `New-Api-User` 认证。密码加密开启时按官方 RSA-OAEP/SHA-256 协议登录，长 Unicode 密码使用官方 v2 RSA + AES-GCM envelope。遇到二次验证挑战时停止；不会关闭验证或创建 PAT。

只有明确指定 `install` 才上传并在必要时激活插件：

```bash
rtk proxy python3 integrations/newapi/ops/install_plugins.py install \
  --base-url https://api.drawbookai.cn \
  --credentials-file /absolute/private/path/new-api-admin.env \
  --lock integrations/newapi/catalog.lock.json
```

`--ca-file /absolute/path/ca.pem` 可指定可信 CA；工具始终要求 HTTPS 和证书/主机名校验，拒绝重定向。默认每个网络请求超时 30 秒，可用 `--timeout` 调整，最高 120 秒。

如使用已存在的 scoped PAT，需要 root 角色，以及 `profile:read`、`option:read`、`plugin:read`；安装还需要 `plugin:write`。普通模型调用 `sk-...` 令牌不是管理认证凭据。无需为本工具创建新 token。

## 实际行为与保护

1. 先读 `/api/status`，要求宿主版本与 lock 的 `host.tag` 精确一致（允许省略前导 `v`），再登录并通过 `/api/user/self` 重查 `role=100`。
2. rc.45 的 status 没有全局插件开关字段。工具读 `/api/option/` 后仅在内存检查有效 `TaskPluginEnabled`，再探测插件列表与 runtime 接口。全局关闭或无法确认时停止，不修改开关，也不输出其余 options 内容。
3. 锁文件只接受官方 `QuantumNous/new-api-plugins` 仓库的 40 位固定提交 URL，插件路径必须匹配 key/version；不接受 `main` 等可变分支。下载源码和上传用图标前分别核对锁定 SHA256，不在本地执行插件 JavaScript。
4. 所有目标先完成预检。任一源码/图标校验失败、版本冲突或自定义覆盖受保护时，整个批次不进行插件写操作，并报告每一个目标。
5. 当前已装版本和哈希相同则跳过；同 key/version 哈希不同即报错，始终 `force:false`。已有官方旧 factory 可由市场版本覆盖；旧 override 先与锁文件同一固定官方提交中的历史源码哈希核对，匹配才允许更新。无法证明官方来源或自定义源码不一致时保留并报错。非目标自定义 key 不作修改。
6. 保留当前已装插件的 enabled 和 remark。已经禁用的插件不会自动启用。已存在但未激活的目标版本不会重复上传；只有其保存的 enabled/remark 与当前配置一致时才激活，否则停止以保留配置。上传新 override 版本后按真实 API 语义另调 `/activate`。不重配价格、渠道、供应商凭据或 marketplace sources。
7. 写入前再次检查当前配置和目标已保存版本，发现并发修改则停止该目标。API 不提供条件写入事务，操作窗口应避免其他管理员同时修改同一插件。上传后单项失败不阻止其他已预检目标继续，整体退出码为非零，不会自动回滚覆盖其他管理员的新状态。
8. 最终重新读取列表和 runtime，核对全部目标的版本、SHA256、active、enabled、remark、runtime status，以及全局数据库/运行时错误。disabled/disabled_fallback 会标记为 `verified_preserved_disabled`；表示安装状态与禁用设置已保留。任一目标缺失、未注册、哈希/配置不符、runtime 有错误或读回请求失败都会非零退出。多节点集群需另外逐节点核对，单个入口的成功读回不覆盖全部副本。

输出 JSON 仅包含模式、root/runtime 检查状态、逐项 key/version/action/state、预定义错误代码和退出码。不会打印请求凭据、Bearer、登录响应、完整错误响应、插件源码、备注或其他配置。远端错误文本可能包含敏感信息，因此工具只报告例如 `http_status_403`、`api_request_rejected`、`source_sha256_mismatch`。

退出码 `0`：plan 已通过预检，或 install 的全部目标通过列表与 runtime 读回。退出码 `1`：预检、安装或读回至少一项失败。plan 中 `runtime_verified:false` 表示尚未执行安装后的运行时验收；不能据此宣称安装完成。`--help` 和参数用法由 argparse 处理。

## 本地验证

```bash
rtk proxy python3 -m unittest discover -s integrations/newapi/ops/tests -v
rtk proxy python3 integrations/newapi/ops/install_plugins.py --help
```

测试使用精确模拟的 HTTP 请求和本地生成的 RSA 密钥，覆盖计划模式、Bearer/root 认证、TLS/重定向、加密登录与长密码、factory 跳过、SHA 与图标校验、同版本/历史版本冲突、自定义与禁用设置保留、新版本激活、并发修改、部分失败、列表/runtime 读回失败和输出脱敏。测试不会请求真实 New API 服务器。

## 协议依据

管理协议以锁定源码为准：

- [Root 管理路由](https://github.com/QuantumNous/new-api/blob/6cf0d90b5ff27f901697dddd61e0c28aba8efefc/router/api-router.go)
- [上传、列表、runtime 和激活](https://github.com/QuantumNous/new-api/blob/6cf0d90b5ff27f901697dddd61e0c28aba8efefc/controller/task_plugin.go)
- [插件版本与 active 的保存语义](https://github.com/QuantumNous/new-api/blob/6cf0d90b5ff27f901697dddd61e0c28aba8efefc/model/task_plugin.go)
- [密码登录与 access_token 响应](https://github.com/QuantumNous/new-api/blob/6cf0d90b5ff27f901697dddd61e0c28aba8efefc/controller/user.go)
- [密码加密协议](https://github.com/QuantumNous/new-api/blob/6cf0d90b5ff27f901697dddd61e0c28aba8efefc/common/password_crypto.go)
- [PAT scope 路由规则](https://github.com/QuantumNous/new-api/blob/6cf0d90b5ff27f901697dddd61e0c28aba8efefc/middleware/access_token_routes.go)
