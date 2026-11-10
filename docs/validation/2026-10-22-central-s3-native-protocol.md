# S3-A 原生 Codex 操作标识与重试验证

日期：2026-10-22。执行对象为当前便携包中的原生 `codex-cli 0.146.0`，PATH 安装版亦为同版本。使用独占临时 HOME、CODEX_HOME、工作区和 literal loopback 假上游，仅使用合成 token；没有读取真实登录、模型、商户或 Logto 凭据。

| 场景 | 配置 | 实际模型 POST 数 | 结果 |
| --- | --- | ---: | --- |
| 首次 HTTP 503，随后模拟成功 | 原生默认 | 2 | 自动重试后 completed |
| SSE 在终结事件前断开，随后模拟成功 | 原生默认 | 2 | 自动重连后 completed |
| HTTP 503 | request_max_retries=0、stream_max_retries=0 | 1 | failed，没有二次提交 |
| SSE 在终结事件前断开 | 同上 | 1 | failed，没有二次提交 |
| 同一 thread 提交两次合法相同提示 | 同上 | 2 | 两次独立 completed |
| update_plan 工具调用后继续模型请求 | 同上 | 2 | 第二次包含 function_call_output |
| 配置静态 Idempotency-Key，执行两次 turn | 同上 | 2 | 两次收到同一个静态 key |

`X-Client-Request-Id` 等于 thread/session 标识，跨重试、独立回合和工具续轮均可相同。`x-codex-turn-metadata.turn_id` 能区分用户回合，但工具执行前后的两次独立模型请求仍相同。原生没有自动发送逐操作 `Idempotency-Key`。HTTP 重试 body hash 相同，而断流重试的 body hash 在该次探针中发生变化；不能使用 body hash 推断重试或抹掉合法重复操作。

因此，中央模式关闭两层原生重试，由本机代理给每个接受的模型请求分配新的 UUID。单个操作在中央服务内以 invocation + operation key + body hash 幂等处理，代理不自动重发结果未知的调用。一个用户回合内的工具续轮使用不同 operation key；新用户回合使用新 invocation。静态进程 header、thread ID 或 turn ID 均不能替代这一层。

实际原生工具目录包含顶层 function 和一层 `namespace`，其中 `multi_agent_v1` 包含多个 function；文本网关需要支持这一结构。`web_search=disabled` 的捕获请求未包含 hosted search。本次工具验证只执行本地安全的 `update_plan`。

配置依据通过 Context7 的 library → docs 流程从官方 Codex 仓库获取，并与当前二进制比对：[配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)、[官方 provider 定义](https://github.com/openai/codex/blob/main/codex-rs/model-provider-info/src/lib.rs)、[官方 Responses endpoint 实现](https://github.com/openai/codex/blob/main/codex-rs/codex-api/src/endpoint/responses.rs)。main 分支源码不代表已分发二进制的精确提交；表中行为是实际 0.146.0 实测。

正式回归位于 `tests/test_central_execution_native.py`，使用 `EASEL_TEST_CODEX_BINARY` 指定当前受验二进制，覆盖真实 CodexAdapter、owned proxy、工具往返、HTTP 503、断流及取消清理。未指定可执行文件时不能把纯 mock 测试称为原生验收。更换原生版本后应重跑这些协议测试。

该证据验证本机协议与操作身份，不代表真实供应商兼容、真实产物或正式商业计费验收。
