# 中央订阅文本执行（S3-A / S3-B）

本阶段把中央账号、套餐和共享点数钱包接入本机 Codex 的文本与工具执行。自定义接口继续独立使用；旧版 v1 订阅会话保持原渠道。首次明确选择中央订阅后，用户只需登录、拥有对应模式的套餐和点数，并选择已发布模型，无需填写供应商 Key。

当前开发预览采用本机模拟 Responses 服务和测试点数。它验证认证、协议、工具循环与账本，不代表真实供应商、正式商业费率或真实收款已经验收。图像、视频、音乐、ASR、TTS、音色和云端执行仍待后续接入。

2026-10-23 的独立服务器续验已通过 New API Type 58 转换后的 MiniMax 文本、function 往返、幂等重放和按真实 token 用量结算；使用非商业验收账号与点数，没有更改上述产品预览。实际 Node Codex 完整请求仍失败并保留待核算预算，用户正在评估 Pi/OpenClaw 执行引擎路线。不能将中央协议通过视为整个原生执行链或商用发布已通过，详见 [真实模型续验报告](../../docs/validation/2026-10-23-new-api-live-discovery.md)。

## 服务器配置

先备份业务库和 Logto 库，再用独立 owner 配置执行 `migrate` 和 `grant-runtime`。新增 `006_execution.sql`，不修改 001–005。常驻进程使用 `central_runtime`；模型和费率发布使用 owner 运营配置，不能包装为公共 HTTP。

不设置 `CENTRAL_EXECUTION_CONFIG_FILE` 时默认关闭执行。启用时，该参数指向服务器上的普通文件，必须由服务进程用户持有、权限 `0600`、不使用符号链接，且不超过 64KiB。目录和凭据不得进入仓库、桌面包或普通日志。配置结构如下，示例仅用于独立的本机模拟服务：

```json
{
  "mode": "test",
  "providers": {
    "fixture": {
      "version": "1",
      "endpoint": "http://127.0.0.1:18766/v1/responses",
      "secret": "REPLACE_WITH_LOCAL_FIXTURE_SECRET",
      "models": { "central-text-fixture-v1": "fixture-upstream-v1" }
    }
  },
  "concurrency": 4,
  "timeout_seconds": 90,
  "heartbeat_seconds": 10,
  "recovery_seconds": 15,
  "worker_enabled": true
}
```

`endpoint` 是完整固定 Responses URL，不能由产品请求指定。`test` 仅接收 literal loopback HTTP 上游；`live` 只接收 HTTPS 上游。HTTP 客户端禁用环境代理、重定向和隐式重试。供应商可以是直接兼容 Responses 的服务，也可以是已验证兼容此路径的 New API 渠道；该配置不证明它支持全部 Easel 接口。

公开模型 ID 与上游模型名通过服务器映射。绑定指纹包括模式、供应商版本、地址、凭据摘要和映射；修改配置后必须重启服务。已接受操作保留原绑定和费率，不能借重试更换供应商。需轮换时受控配置新版本、验证新目录，再停用旧模型。

## 不可变模型和费率

模型定义示例：

```json
{
  "id": "central-text-fixture-v1",
  "name": "中央文本与工具 · 本地测试",
  "version": "1",
  "capability": "text",
  "feature": "chat",
  "products": ["easel"],
  "test_only": true,
  "provider_id": "fixture",
  "provider_version": "1",
  "verification": "fixture",
  "verification_ref": "YOUR_LOCAL_VALIDATION_RECEIPT",
  "rate": {
    "version": "fixture-token-rate-v1",
    "input": { "numerator": "100", "denominator": "1" },
    "output": { "numerator": "200", "denominator": "1" }
  },
  "limits": {
    "max_input_bytes": 1048576,
    "max_input_tokens": 65536,
    "max_output_tokens": 4096
  }
}
```

这里的费率是测试常量，不是商业定价。`feature` 必须匹配同模式有效套餐的功能。正式定义需要 `test_only=false`、`verification=live` 和真实验收依据；测试点数不能支付正式请求，正式点数不能支付测试请求。

```bash
rtk proxy uv run --project services/platform --frozen --env-file /etc/easel-central/central-operator.env python -m platform_service model-publish --file /etc/easel-central/text-model-v1.json --operator YOUR_OPERATOR_ID
rtk proxy uv run --project services/platform --frozen --env-file /etc/easel-central/central-operator.env python -m platform_service model-disable --model central-text-fixture-v1 --operator YOUR_OPERATOR_ID
rtk proxy uv run --project services/platform --frozen --env-file /etc/easel-central/central-operator.env python -m platform_service model-enable --model central-text-fixture-v1 --operator YOUR_OPERATOR_ID
```

上述命令优先使用 owner DSN。同 ID 相同定义可重复发布；任何定义变化必须发布新 ID/版本。数据库 runtime role 无模型发布或费率改写权限。启停不改变已有操作的快照。

## 扣点、幂等与恢复

1 点等于 1,000,000 微点。输入和输出 token 分别乘有理数费率，相加后只向上取整一次。API 的微点数量使用十进制字符串。开始前以配置的输入 token 上限和有限输出上限预留预算；完成后按可信终结响应的用量结算并释放余量。不要用请求字节数推测供应商 token，也不要把未知用量当作零。

每个 invocation 最多 32 次模型操作，累计实际花费与未结预算不超过 100 点。工具调用前后是两次模型操作；本地工具本身不额外收费。task token 最长 300 秒、invocation 最长 1800 秒，且受设备会话和权益期限约束。续期沿用同 invocation，不重置预算。

原生 Codex 的会话／回合 header 无法唯一识别工具循环中的模型操作。本地转发层为每次接受的请求生成独立 operation UUID，在中央发送前保存 UUID 和 body hash。原生两层自动重试均关闭。相同 key、相同 body 复用已有操作；改变 body 返回冲突；不同 key 的合法相同请求分别计量。

中央在供应商 I/O 之前提交 dispatch 标记，每个操作最多一次物理上游尝试。只有结构有效、带 token 用量的 `completed` 或 `incomplete` 终结响应才结算。HTTP 错误、超时、断流、取消或缺少用量的已发送请求进入 `pending_reconciliation`，保留预算并等待可信证据；不能通过换 key、重建请求或手改余额“补救”。结算可信已发生费用不依赖原用户会话继续有效。

worker 使用租约恢复：确定未发送的过期预留可释放；已发送但结果未知的操作只标记待核算，绝不自动重提供应商。运行一次维护恢复：

```bash
rtk proxy uv run --project services/platform --frozen --env-file /etc/easel-central/central.env python -m platform_service execution-work
```

完成结果最多保存 24 小时用于幂等重放，JSON 上限 2MiB、SSE 总上限 8MiB、单事件上限 256KiB；到期清除响应内容，保留账本和元数据。重放到期不会重新调用模型。响应可能包含用户内容，应将数据库和备份按私有业务数据管理。

## 产品入口与权限

在 Easel 设置的“账号与订阅”登录中央账号，查看共享钱包、模型费率和当前账号调用记录。再进入“Agent 底座”，选择“订阅服务 → 中央订阅 → Codex → 已授权模型”，保存后新建对话。原有缺少 backend 字段的订阅配置按 legacy 解释，旧聊天不会因中央登录而改绑。

产品 bearer 可读取模型、调用记录并申请短期 task token；只有 task token 可请求 `/v2/responses`。task token 不传给浏览器或原生子进程，原生子进程只取得当次运行的 loopback token。转发层退出前关闭对应 app-server。已支持的媒体脚本在中央文本模式下、读取 dotenv 或派发子进程之前明确拒绝待开放能力，避免自动使用用户自定义密钥。

| 中央路径 | 用途 |
| --- | --- |
| `GET /v2/products/easel/models` | 已验证且适用当前账号的安全目录 |
| `POST /v2/task-tokens` | 本机私有桥申请模型／invocation 绑定授权 |
| `POST /v2/responses` | task bearer + Idempotency-Key，仅文本和本地工具协议 |
| `GET /v2/executions`、`GET /v2/executions/{id}` | 当前用户、产品、钱包的调用元数据 |
| `POST /v2/executions/{id}/cancel` | 未派发释放；已派发保持待核算 |
| `GET /v2/execution-operations?invocation_id=...&operation_key=...` | 当前账号按原操作查询状态与缓存是否保留，不重新派发 |

没有产品可写的价格、上游 URL、实际用量、余额或结算接口。`/v2/capabilities` 分别返回 `test_execution_enabled` 和只代表正式模式的 `subscription_execution_enabled`；媒体与云端仍列入待接入能力。

## 当前交付边界

S3-B 将本地代理改为逐事件转发有界 SSE，原生客户端无需等待完整响应才能展示文字。单事件 256KiB、总响应 8MiB，带读取、整体与下游排空期限；错误或缺少终态时不伪造成功。中央仍在发送成功终态前结算，前面的增量文字属于未完成输出。实际供应商的流式兼容性仍需单独验收。

设置 → 账号与订阅 → 订阅模型与执行记录中增加“中断请求状态”，点击“查询中断请求”读取当前账号的本机记录与中央状态。查询只使用原 invocation/operation 标识发出 GET，不创建任务凭证、不重新发送模型请求，也不重新执行工具。正常的同范围账号凭证刷新仍沿用已有认证流程。

本机转发进度、中央账务状态与结果缓存状态分别显示：本机不确定可能对应中央已结算；缓存过期也不改变已结算事实。尚未查到记录不代表没有扣点或可以重发。查询最多读取 50 条符合当前业务归属的 v2 本机记录，扫描与网络等待有界。旧版没有归属标记的记录保留在磁盘，不能自动猜测并迁移到当前账号。

历史归属绑定中央 origin、用户、租户、产品和钱包；重新登录同一业务账号可以查看历史，但不会将新会话用于续期或重放旧执行上下文。中央保存的相同 key 结果仍有幂等重放能力，本批界面只查询状态，尚未把历史回复自动注入聊天或自动恢复工具执行。

断流后费用未知的操作保留预留，等待可信核算；本批没有供普通用户修改结算结果的入口。原生进程的环境和输出过滤属于凭据传递控制，不是操作系统级文件隔离；本机工具仍受既有 Codex 沙箱和当前系统用户权限约束。

当前真实原生协议验收覆盖 Codex 0.146.0 的打包执行文件与已安装 npm/Node 启动器。原生版本升级、真实供应商接入、商业费率和正式收款需要分别验收，不能由本地夹具通过推定。已有证据见 [S3-A 验收记录](../../docs/validation/2026-10-22-central-s3.md)，本阶段状态见 [S3-B 验收记录](../../docs/validation/2026-10-23-central-s3b.md)。
