# New API 真实模型接入检查

日期：2026-10-23（Asia/Shanghai）。分支：`codex/ui-redesign`。本文件记录 S3-B 的真实供应商接入续验；当前结果为**中央协议和账本验收部分通过，安装版 Node Codex 的真实完整流程仍未通过**。没有合并主分支。

## 模型目录与首次真实调用矩阵

通过已有 SSH 连接核对运行中的 `1panel-new-api` 及其实际 PostgreSQL 数据库 `newapi`，版本为 `v1.0.0-rc.45`。开始时有两个启用的 Type 60 渠道：渠道 1“烁公益站”和渠道 2“Ark API”；`GET /v1/models` 实际返回以下五个模型标识。

下表是**新增兼容渠道之前**的首次矩阵，每个单元格各一次请求，输出上限 256 tokens、单次等待上限约 75 秒、没有重试；另有一次 GLM Responses 流式预检返回 HTTP 400。

| 已配置模型标识 | 原渠道 | Responses JSON | Chat Completions JSON |
| --- | --- | --- | --- |
| `deepseek-v4.1-flash` | 1 | HTTP 400 | 约 75 秒客户端超时；稍后网关记录 HTTP 524 |
| `minimax-m2.7` | 1 | HTTP 500，`rate_limit_exceeded` | HTTP 200；输入 47／输出 92 tokens；5.304 秒 |
| `kimi-k3` | 1 | HTTP 400 | HTTP 500 |
| `glm-5.3` | 1、2 | HTTP 404 | HTTP 200；输入 21／输出 76 tokens；69.847 秒 |
| `grok-4.7` | 2 | 约 75 秒客户端超时；稍后网关记录 HTTP 500 | HTTP 200；输入 222／输出 179 tokens；6.177 秒 |

三个 HTTP 200 的 Chat 响应均包含预期文本标记及 usage。其余结果只说明该次、该路径未成功，不据此判定模型永久不可用。公开目录中的 Responses 声明没有得到首轮矩阵支持，不能作为原生 Responses、工具或用量兼容性证明。这些是用户配置上游的模型标识，没有验证其背后的供应商品牌或官方模型身份。

## 密钥授权与数据边界

首轮检查时 New API 自身业务令牌表为空。用户在“令牌”菜单保存后，确认业务令牌 `easel`（id 1）存在且启用：default 分组、无模型限制、无限配额、无 IP 白名单、跨分组重试关闭。供应商渠道 Key 与这个下游业务令牌分别管理。

自动审批在执行前拒绝了“读取生产库业务令牌并复制到本机私有临时配置”的尝试，原因是此前交付约定密钥留在服务器；该操作没有执行。用户随后明确授权**仅在服务器读取并使用令牌，密钥不出服务器**。实际业务令牌只进入服务器私有配置，服务器内请求模型；本机仅接收白名单验收元数据。原本机复制路径保持禁用，源码、浏览器和公开回执没有业务令牌。

安装版 Node Codex 验证使用了隔离验收账号的临时产品会话，以访问中央服务；这不是 New API 业务令牌，客户端没有供应商密钥。会话文件保存在 owned 0700 目录内的 0600 文件中。

原生失败排查中，自动审批又在执行前拒绝导出经过正则脱敏的生产日志正文；改为服务器内固定词表分类，只返回错误类别和计量字段，没有导出日志正文。捕获的完整原生请求用于外部对照的操作也在执行前被自动审批拒绝，尚未作为授权成功的外发验收；当前可记录的后续对照只使用手写合成消息。

## 官方内置转换器及实际渠道变更

对官方 `v1.0.0-rc.45` 源码的核对确认：Type 60 New API 渠道按原路径透传，Type 58 Advanced Custom 渠道才支持显式的 `openai_responses_to_openai_chat_completions` 转换。路由定义位于渠道 `settings`（复数）中的 `advanced_custom.advanced_routes`，不能只给 Type 60 增加一个开关。[Type 60 适配器](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.45/relay/channel/newapi/adaptor.go#L32-L36)、[路由结构](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.45/relaykit/dto/channel_settings.go#L187-L198)、[Type 58 请求转换](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.45/relay/channel/advancedcustom/adaptor.go#L114-L115)。

真实验收只新增渠道 **3 `Easel S3B MiniMax Responses v1`**：Type 58、模型仅 `minimax-m2.7`、default 分组、priority 1、weight 10。新增渠道及 abilities 在同一事务写入；Key 在服务器数据库内部从渠道 1 复制，没有离开服务器。原渠道 1、2 的已核对配置元数据保持不变。新渠道有以下两条显式路由，渠道和路由的 body pass-through 均关闭：

| 接收路径 | 上游路径 | 转换器 |
| --- | --- | --- |
| `/v1/responses` | `/v1/chat/completions` | `openai_responses_to_openai_chat_completions` |
| `/v1/chat/completions` | `/v1/chat/completions` | `none` |

新渠道会参与 default 分组中该模型的正常路由选择，不能将其描述为对生产路由完全没有影响。写入前在服务器保存完整数据库备份；后续如需撤回，应只停用本轮拥有的渠道 3 及其 ability，不能用整库备份覆盖用户后续状态。正常缓存同步发现了新渠道，**没有重启 New API**；官方默认同步周期为 60 秒。[缓存同步实现](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.45/model/channel_cache.go#L109-L114)。

Type 58 转换器按上游 Chat chunk 增量生成 Responses 事件，普通 function 参数和函数返回历史有转换实现；custom tool 的输入须到完整参数结束后才能恢复，不能宣称与原生 custom tool 的逐片输入完全等价。`previous_response_id`、`conversation` 等有状态字段也不等价于完整原生 Responses 能力。[流式转换](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.45/relay/channel/openai/responses_via_chat.go#L123-L181)、[请求及工具转换](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.45/relaykit/relayconvert/internal/oai_responses/to_oai_chat_req.go#L122-L138)。

## 转换后的真实 MiniMax SSE

新增渠道生效后，以服务器校验 TLS 的真实请求访问 `https://api.drawbookai.cn/v1/responses`：HTTP 200、`text/event-stream`、预期标记存在、终态 `completed`，输入 47／输出 98 tokens。首个文本事件为 16.4836 秒，终态为 16.4840 秒，客户端确实在终态之前收到文本；本次两者时间非常接近，不能据此证明长回答具有良好的首字延迟。

原始响应保留在服务器私有证据文件，本机只收安全回执及 SHA256 `8675fe3d4313806ea942880378f5b3aa0152584c78fde131057a7992f9104ee2`。预检脚本最初未处理 reasoning item 的 `content: null`，在已捕获响应上离线修正回执，没有重发模型请求；可复用中央验收 runner 原已只读取 message item，未因此更改生产解析逻辑。

新增渠道后的独立 Chat 回归请求暂为 HTTP 503，上游固定分类为 overload，**尚未证明新增渠道后 Chat 路径成功**。前表中原渠道的 Chat 200 是变更之前的证据。

## 中央服务真实四操作与共享点数账本

保留原有 `18761` 产品预览、`18762` 中央服务、Logto、模拟供应商和历史钱包。本轮另外建立 owned PostgreSQL 验证 schema、runtime 和 workspace，加载原有六个迁移；所有供应商业务密钥仍留服务器。公开模型绑定为 `central-minimax-m27-live-v1`，实际转发 `minimax-m2.7`。

身份来自合成的 owned 验收会话，但使用真实 `IdentityService` 令牌签发、认证和 HTTP 服务边界。**没有验证新的 Logto/OIDC 注册登录流程。** 验收资金是明确标记的**非商业 live 账本夹具**：单独 membership、live lot 与平衡 issue journal；不是付费会员发放，不转换原测试点。数据库中 `billing_orders`、`payment_events`、`payment_transactions` 均为 0，没有伪造已支付订单，也没有给产品增加绕过支付的 live-grant 接口。

四次真实供应商 POST 各执行一次，没有重试。测试费率为输入每 token `1/2`、输出每 token `3/2` synthetic microcredits，按账本规则只在合计后取整；**这些数值不是商业售价，也不是人民币。**

| 操作 | 输入／输出 tokens | 供应商终态 | 中央状态 | 实扣 synthetic microcredits |
| --- | --- | --- | --- | --- |
| 普通 JSON 文本，输出上限 128 | 45／128 | `incomplete`，已有预期文本 | `settled` | 215 |
| 强制 `acceptance_echo` function 的 SSE | 218／60 | `completed`，正确函数名和参数标记 | `settled` | 199 |
| 同一 invocation 回传工具结果后的 SSE，输出上限 256 | 280／256 | `incomplete`，非空文本 | `settled` | 524 |
| 收到文本后断开客户端，输出上限 256 | 54／256 | 中央取得可信 `incomplete` 终态；客户端未收到终态 | `settled` | 411 |

第三个操作首文本 8.1629 秒、终态 8.1633 秒。第四个操作客户端在 12.4645 秒收到文本后断开，随后 GET 查询的中央状态已经结算；这证明**客户端未收到终态不等于中央未结算**，不将它冒充“上游断开且未知用量”的成功样本。四个 receipt 均有可信输入／输出用量，合计实扣 **1,349 synthetic microcredits**。四操作结束时，独立验收 lot 为 issued `100000000`、available `99998651`、spent `1349`、held `0`；此后原生失败新增的保留额另见下一节。

对第一个操作使用相同 key 和 body 重放，再以 GET 查询，物理供应商 POST 计数维持 `1 → 1`，execution／receipt／reservation／journal／entry 数量及余额均不变，没有二次扣点。New API 安全计量导出中的渠道 3、令牌 1、`/v1/responses` 成功日志 18–21，其四组输入／输出 tokens 与中央 receipts 一致。首次导出误从 `other` 读取 `request_id`，后改为读取日志顶层列，按 provider response ID 去掉 `chatcmpl-` 前缀精确关联；最终结果保存在可移植的安全续验回执中。

## 安装版 Node Codex：仍待完成

通过实际安装的 Node Codex、现有 `CodexAdapter`、`ExecutionProxy` 和中央 HTTP 服务做了第一次真实验证。使用独立只读 workspace，要求只执行 `update_plan` 并输出验收标记；设置最多三次中央 POST，实际只发出一次，约 9.1884 秒后失败，无重试，未观察到计划工具或最终标记。

该操作 `de56a642-a63f-421c-a7a1-8e63350a1663`（job `f343005c-146a-46ab-ba84-ea5bc67c294d`）可由当前账号的只读恢复接口找到：`pending_reconciliation`、`actual: null`、输入／输出用量均未知、`result_state: not_available`，保留 **33,152 synthetic microcredits**。没有将该保留额清零、按零费用结算、自动重发或复用其 operation key 执行新请求。前节 held `0` 是四操作阶段快照，不能当作这个失败操作之后的当前余额。

New API 对应请求记录为渠道 3 的 HTTP 500；安全服务器内分类只有 model／not support／input 相关固定词，没有足以确认唯一字段原因的正文证据。后续手写合成的 developer 和 system 两种 role 对照均返回 HTTP 200 `completed`，成功调用 `acceptance_echo`，用量分别为 195／47 和 186／51 tokens；连续 user 消息及简化 namespace 对照也均返回 200。因此不能将原生失败归因于 developer role，也不能只凭 namespace 存在就认定失败原因。

长合成输入的另一次对照返回 HTTP 500，安全固定分类含 token／quota 关键词。这不足以确认上下文超长，也不足以确认上游余额耗尽或其他额度限制；不能把尚未证实的猜测写成供应商故障根因。

用户随后提出用 OpenClaw/Pi 等轻量开源 Agent 代替 Codex 的路线问题，尚未授权完整捕获 body 外发。完整原生 body 的外部复测因此未继续，等待执行引擎方向和需要的数据授权确定。当前不能宣布安装版 Codex 的真实文本与工具流程通过，也不能把手写函数调用样本当作全部原生 tools、命名空间、custom tool、历史和上下文兼容性证明。还需完成选定执行引擎的真实完整路径、复核新增渠道后的 Chat 路径。未知用量的原操作继续保留核算证据。

## 临时服务归档与保全

结束本轮外部调用后，撤销 isolated `acceptance-session`，在服务器生成独立验收数据库备份（460957 bytes，SHA256 `5a4ca8ea03cbbbdc46448e2575203f3da8ee528cf70a41c90169a9b9fc579193`）。保存五个操作和完整待核算保留额后，只向本轮临时中央 HTTP 进程发送 SIGTERM、关闭本轮 SSH 隧道，并核对专属 owner 标签后停止新建验收 PostgreSQL 容器；未删除容器或账本。本机合成产品会话文件已删除。New API、原有预览与原中央服务保持原运行路径。

归档时独立验收 lot：issued `100000000`、available `99965499`、held `33152`、spent `1349`。四个 settled 操作和一个 `pending_reconciliation` 操作保留原状态，没有通过清理释放未知费用。兼容渠道 3 保留，未回滚整个生产数据库。

两份数据库备份及安全回执已在服务器持久保存至 owned 0700 目录 `/var/lib/easel-validation/s3b-live-c9sq0wm4`，文件为 0600；不向本机导出备份。停止验收服务后，删除该运行目录内 `gateway-private.json`、`live-execution-private.json`、`native-product-session-private.json` 三份临时凭据文件，生产业务令牌本身不变。原产品首页 `18761` 和中央 `/health` `18762` 均返回 HTTP 200。

## 验证与证据位置

本轮新增受控验收 runner 及其隔离测试。回执目标在任何配置读取或模型副作用前以 owned 0700 目录、0600 exclusive 文件预留；独立 review 的两项发现均修复。runner 测试本轮结果为 **16 passed**，相关 Ruff 通过。这不能替代原有 417 中央、461 原生、243 媒体和 212 前端用例的前一阶段完整验证，也不将那些历史计数称作本轮重新运行。

本机私有证据目录为 `/private/tmp/easel-s3b-live-20261023`。本文件依据以下不含密钥、DSN、原始提示词或生产日志正文的安全回执整理：`matrix-receipt.json`、`compat-channel-receipt.json`、`converted-preflight-receipt.json`、`central-live-receipt.json`、`gateway-metering-safe.json`、`actual-native-receipt.json`。临时目录不属于可移植交付材料；后续整体验收报告应保存必要的安全摘要，不能要求维护者依赖这些临时路径长期存在。

可移植的 [真实续验安全回执](2026-10-23-central-s3b-live-receipt.json) 已包含四笔中央操作与 New API 日志顶层 request ID 的精确计量关联、完整状态快照和 owned 服务归档记录。[本轮保全回执](2026-10-23-central-s3b-live-preservation.json) 核对开始时 264 个既有路径：除明确更新的执行文档外，其余路径不变，根 `.env`、HEAD、main 引用及六个 SQL 迁移保持不变。没有提交、推送或合并。

## 文档来源

按项目要求使用 Context7 解析 `/quantumnous/new-api-docs-v1`，读取当前官方接口及插件文档。文档将 [Responses 与模型列表](https://github.com/quantumnous/new-api-docs-v1/blob/main/content/docs/zh/guide/feature-guide/user/api.mdx) 列为独立接口；[用户 API 使用](https://github.com/quantumnous/new-api-docs-v1/blob/main/content/docs/en/guide/feature-guide/user/api.mdx) 使用业务 Key 的 Bearer 认证。[渠道配置](https://github.com/quantumnous/new-api-docs-v1/blob/main/content/docs/en/plugins/configuration.mdx) 将 Type 60 定义为连接另一个 New API。版本特定的转换、缓存和路由结论以前文引用的官方 `v1.0.0-rc.45` 源码为准；官方实现存在与本次真实功能通过是两种证据。
