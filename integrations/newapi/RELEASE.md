# New API 版本与插件锁定记录

核对日期：2026-10-20。此文件保存官方发布物与安装协议，现场部署结果见[服务器验收](../../docs/validation/2026-10-20-new-api-upgrade-plugins.md)。该服务器已升级至下述版本并完成18个插件注册；真实供应商调用未验收。下面的安装协议仍可用于后续复核，不能用发布物下载成功替代现场读回。

## 已锁定的宿主

- 官方 `releases/latest`：`v1.0.0-rc.45`，发布于 `2026-10-17T15:42:41Z`。
- 源码 commit：`6cf0d90b5ff27f901697dddd61e0c28aba8efefc`。
- 镜像：`calciumion/new-api:v1.0.0-rc.45`。
- 多架构 manifest 摘要：`sha256:6c63c7ee9cd5bf7880df3984e99dd3ef07992d7a97538cfb2719adf9d87bc3f9`。
- 核对时 `latest` 与上述标签摘要相同；升级使用固定摘要，避免随后浮动。
- GitHub 返回 `prerelease: false`，但标签名称与发布说明仍明确为 RC，v1 正式版尚未发布。不能将该标记解释为正式 v1 stable。

来源：[release](https://github.com/QuantumNous/new-api/releases/tag/v1.0.0-rc.45)、[GitHub latest API](https://api.github.com/repos/QuantumNous/new-api/releases/latest)、[Docker Hub tag](https://hub.docker.com/v2/repositories/calciumion/new-api/tags/v1.0.0-rc.45)。架构级摘要和校验时间见 [catalog.lock.json](./catalog.lock.json)。

## 官方市场的全部任务插件

锁定索引 commit 为 `423535f2c6d46171d2e9553c6d33a0987116aa63`，索引文件 SHA256 为 `ccf5737967a443ca197ef70487172cc64e269d5317d48d6b77a64595094bb011`。全部 13 个最新插件源码、两个附加图标均已下载校验，后续已完成线上安装与运行时读回。源码 URL 固定到该 commit。

| key | 版本 | rc.45 中的交付方式 |
| --- | --- | --- |
| alibaba | 1.4.2 | 内置 factory |
| doubao | 1.3.0 | 内置 factory |
| google | 1.1.0 | 内置 factory |
| hailuo | 1.3.0 | 内置 factory |
| incho | 1.0.2 | 通过管理 API 上传 |
| jimeng | 1.1.0 | 内置 factory |
| kling | 1.1.0 | 内置 factory |
| sora | 1.1.0 | 内置 factory |
| sunoapi | 1.1.0 | 内置 factory |
| typesafe | 1.2.0 | 通过管理 API 上传 |
| vertex-ai | 1.1.0 | 内置 factory |
| vidu | 1.1.0 | 内置 factory |
| xai | 1.1.0 | 内置 factory |

上述 11 个内置插件的源文件 SHA256 与市场最新版完全一致。升级后应先读回运行时列表，确认其未被禁用、没有编译或路由错误；不需要重复上传同版内置插件创建 override。宿主还存在全局 `TaskPluginEnabled` 设置和 `TASK_PLUGIN_ENABLED` 环境变量，后者默认 true；应读回全局设置，不能只根据单插件的 enabled 推断整个任务系统已开启。安装并启用全部插件也不会自动配置供应商密钥、模型价格及可用额度。

来源：[锁定市场索引](https://github.com/QuantumNous/new-api-plugins/blob/423535f2c6d46171d2e9553c6d33a0987116aa63/index.json)、[宿主内置加载代码](https://github.com/QuantumNous/new-api/blob/6cf0d90b5ff27f901697dddd61e0c28aba8efefc/plugins/embed.go)。

## 安装与读回协议

以下管理接口在 rc.45 中均经过 `RootAuth`。合法 root 控制台会话访问令牌，或 root 用户的合适 scope PAT，可通过 `Authorization: Bearer ...` 调用；普通模型 API key 不具备该权限。使用 PAT 时，读接口需要 `plugin:read`，写接口需要 `plugin:write`。不要在文档、命令行参数、日志或版本库中保存实际凭据。

| 操作 | 方法与路径 | 请求或验收点 |
| --- | --- | --- |
| 列出插件 | `GET /api/plugin/task` | `data` 数组含 `meta.key/version`、`source`、`source_hash`、`enabled`、`active`、`runtime_status` |
| 运行时状态 | `GET /api/plugin/task/runtime/status` | 检查 `plugin_errors`、`database_error`、`last_rebuild` 及当前 generation |
| 安装版本 | `POST /api/plugin/task` | JSON：`source` 必填；`sourceSha256`、`enabled`、`remark`、`icon` 可选；保持 `force: false` |
| 激活已有版本 | `POST /api/plugin/task/:key/activate` | JSON：`{"version":"..."}` |
| 启用插件 | `POST /api/plugin/task/:key/status` | JSON：`{"enabled":true}` |
| 离线 hook 调用 | `POST /api/plugin/task/:key/dryrun` | JSON：`hook`、可选 `member`、`args`；不是上游推理验证 |

上传接口接收源码，而不是远程安装 URL；市场页面先获取源码再上传。服务器会校验 SHA256、编译、校验元数据、检查路由冲突、保存并同步运行时。一个 key 首次安装会自动成为 active；若已有 active 版本，上传新版本后需另行激活。每步均需检查 JSON `success`，不能只检查 HTTP 200。既有插件的禁用状态、remark 和自定义 override 应作为用户配置保留，避免无差别覆盖。

源码：[路由](https://github.com/QuantumNous/new-api/blob/6cf0d90b5ff27f901697dddd61e0c28aba8efefc/router/api-router.go#L248-L264)、[上传和读回](https://github.com/QuantumNous/new-api/blob/6cf0d90b5ff27f901697dddd61e0c28aba8efefc/controller/task_plugin.go)、[版本保存和激活](https://github.com/QuantumNous/new-api/blob/6cf0d90b5ff27f901697dddd61e0c28aba8efefc/model/task_plugin.go)、[PAT scopes](https://github.com/QuantumNous/new-api/blob/6cf0d90b5ff27f901697dddd61e0c28aba8efefc/middleware/access_token_routes.go#L192-L205)。

二进制仅提供 `new-api plugin lint <plugin.js>` 和 `new-api plugin test <plugin.js> --fixture <fixture.json>`，用于本地编译及 fixture 重放。CLI 不连接数据库，不安装插件；当前核对未发现免 root 凭据的本地插件管理入口。无需也不应通过数据库写入或伪造登录会话绕过该权限边界。[CLI 源码](https://github.com/QuantumNous/new-api/blob/6cf0d90b5ff27f901697dddd61e0c28aba8efefc/pkg/jsplugin/cli.go)

## 跨版本升级需要处理的变化

- 从 rc.26 及以前版本跨过 rc.27 的任务插件架构调整时，需要重新配置视频模型价格。先记录既有价格和在途任务，再验证目标版本的价格规则。[发布说明](https://github.com/QuantumNous/new-api/releases/tag/v1.0.0-rc.37)
- rc.44 将游乐场记录按账号隔离，升级后会清空一次旧游乐场历史。[发布说明](https://github.com/QuantumNous/new-api/releases/tag/v1.0.0-rc.44)
- rc.45 默认关闭敏感词过滤；以前填写过词表但没有显式保存开关的部署，需要在请求检查中重新启用并核对配置。[发布说明](https://github.com/QuantumNous/new-api/releases/tag/v1.0.0-rc.45)
- 现场操作应在替换前备份 PostgreSQL、数据目录、应用配置及 Redis 持久化，并保留原镜像摘要；数据库经过新版本迁移后，仅改回旧镜像不能证明回退成立。备份和独立恢复演练是执行时的验证工作，本记录不声称已经完成。

这里的“全部插件”限定为本次官方市场索引中的 13 个条目。它不等于供应商的所有接口，也不等于每个插件声明的模型都可在当前账号调用；Easel 的声音登记、克隆和其他缺口应按接口矩阵单独验证及开发。
