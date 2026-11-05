# Fish 二进制私有桥

New API rc.45 的 task submit 读取 JSON/SSE，不能直接把 Fish `/v1/tts` 的二进制响应作为任务结果持久化。本桥是这一二进制边界的必要扩展：由插件提交一次私有请求，桥固定调用 Fish 官方接口，受限保存音频，返回小型 JSON 元信息，再通过 New API 用户归属校验后的通用 task artifact content 接口下载。

**本轮仅完成本地实现、mock 上游和离线契约验证。没有创建额外监听端口、构建镜像、部署服务、安装 Fish 渠道或使用真实供应商密钥。**

## 信任边界与凭据

- 桥不提供公网模型网关，不处理用户会员或共享额度，也不能替代 New API 的调用鉴权、账本和中央结算。用户只调用 New API，不能持有内部 bridge secret。
- `FISH_API_KEY` 仅由桥的环境持有。New API 专用渠道 `apiKey` 保存独立随机内部 secret，插件只向私有桥发送它；桥向 Fish 发送自己的供应商 key，不转发内部 secret 或用户 gateway key。
- `FISH_BRIDGE_INTERNAL_SECRETS` 是 JSON 字符串数组，每个值为至少 32 随机 bytes 经 URL-safe base64 编码生成的 secret（43–128 个字母/数字/`_`/`-`）。代码检查格式和长度，随机性由部署人员保证。缺失、空数组、弱短值或缺供应商 key 会阻止启动。`.env.example` 故意为空，不能直接启动。
- `FISH_BRIDGE_ALLOWED_REFERENCE_IDS` 是管理员批准共享的预置声音 ID 数组，默认空。任意用户提供的其他 `reference_id` 都在调用 Fish 前被拒绝。用户自己的声音通过 inline 样本请求，不会隐式读取供应商共享私有声音库。
- 参考样本只接受 base64 和转写文本，不支持客户端地址、凭据、路径或重定向。上游地址固定为 `https://api.fish.audio/v1/tts`，TLS 校验开启，忽略进程代理环境，禁用重定向。

生成内部 secret 的操作应在部署环境执行，放入权限为 0600 的 ignored `.env` 或秘密管理器。不要在终端日志、聊天、Git 或本模板中保存生产密钥。secret 轮换期间，旧 secret 的指纹仍绑定旧产物与 receipt；在音频过期及 pending 核对前保留必要旧 secret。

## JSON、MessagePack 与用量

当前 [Fish API 文档](https://docs.fish.audio/api-reference/endpoint/openapi-v1/text-to-speech)允许 JSON 和 MessagePack；[官方指南](https://docs.fish.audio/features/text-to-speech#direct-api-messagepack)说明 inline 原始参考音频使用 MessagePack。桥对管理员预置 `reference_id` 使用 JSON；对 inline `references` 解码 base64 为原始 bytes，再编码 MessagePack，避免把 base64 字符串错误当作音频 bytes。

支持 `s2.1-pro`、`s2.1-pro-free`、`s2-pro`、`drama-3`，显式发送 `model` header，防止未知值回落到供应商默认型号。没有混入已被官方标记弃用的 `s1`。支持单说话人、mp3/wav/pcm/opus、0.5–2 倍速。多说话人、时间戳、供应商模型上传管理和完整高级参数不在当前合同内。

成功元信息仅报告 `request_count=1` 和桥观察到的 `input_characters`，没有宣称供应商的精确 token 数、金额或发票用量。root 必须明确配置按请求/输入字符的服务费率；中央供应商成本仍需独立核对。

## 资源、产物与幂等

默认上限：2 个并发合成请求（解析正文前准入）、4 MiB JSON 请求、2 段参考样本且每段最多 1 MiB、2000 个正文字符、8 MiB 输出、64 MiB 音频容量、90 秒总调用时间、600 秒产物 TTL、10,000 条幂等 receipt。服务进程使用单 worker；部署模板另限制 16 个 HTTP 并发、256 MiB 内存、0.5 CPU 和 128 pids。

状态目录必须由当前用户拥有并保持 0700，数据库、锁和音频文件为 0600，拒绝最终目录/文件的符号链接。产物使用随机 256-bit opaque ID，无客户端路径。读取要求相同 secret 指纹、合法 ID、未过期且完整的文件；支持 GET、HEAD 和单段 byte Range。没有 URL 静态目录或无鉴权音频链接。

SQLite 持久记录 `publicTaskId`、secret 指纹、输入 hash 和状态；不保存供应商 key、内部 secret、原始输入文本或参考样本。音频成功后保存在私有文件。

1. 插件以宿主 `ctx.publicTaskId` 作为 `Idempotency-Key`。桥在发 Fish POST 前持久写入 pending；同 key 同输入串行处理，完成后重放同一 JSON，不再调用供应商。
2. 同 key 换输入或换 secret 均返回 409，避免 New API 跨渠道重试造成再次收费。不同 secret 不能读其他 secret 的 receipt 或产物。
3. 超时、连接中断、上游报错、内容类型错误、超限、进程重启等未知消费结果保持 pending；不会自动重发。插件只 GET `/v1/operations/{publicTaskId}` 查询现有状态，不会通过轮询启动新的收费操作。
4. TTL 到期后读取被拒绝；活动请求触发清理，重启清除 orphan 临时文件。receipt 保留 expired 墓碑和原有可观察用量，避免过期重放造成重新收费。音频 TTL 不等于 receipt 保留期限。
5. 当前没有自动删除 receipt 或人工结算 API；达到 10,000 条后拒绝新任务。正式扩容前应接中央持久幂等和人工核对工作流，再制定已结算历史归档策略，不能直接清表或删除 pending。

目录有独占进程锁，防止多个 worker 在同一数据目录误清理在途临时文件。不要通过副本各用不同空目录接同一逻辑 bridge secret，这会丢失共享幂等语义；当前模板适用于单实例验证。

New API 自身仍可能在任务超时/清理后内部退款。该状态不能证明 Fish 没有收费，不能直接清除中央账本的 pending。成功音频过期也不能当成供应商消费失败。

## 私网部署模板（本轮未执行）

[compose.private.example.yml](./compose.private.example.yml)没有 `ports` 映射。默认 [Dockerfile](./Dockerfile)绑定 `127.0.0.1:8787`；私有 Docker 模板显式绑定容器内 `0.0.0.0`，仅接内部 `newapi-private` 网络和用于访问 Fish 的 egress 网络。`expose` 不会创建主机公网端口。

部署时需要将现有 New API 服务通过其持久 Compose 配置加入 `easel-newapi-private`，保留原来的数据库、Redis、Web 和其他网络配置。不要把桥加入公网反向代理，不要新建 1Panel 网站或发布 8787。Docker 模板尚未构建/运行，网络接入和容器权限需在目标服务器验收。

安装 [easel-fish-tts 插件](../plugins/easel-fish-tts/1.0.0/README.md)，专用渠道 base URL 设为 `http://fish-bridge:8787`（或同机 loopback），内部 secret 设为该渠道 API key。root 配好模型和价格后再开流量。

## 验证

- FastAPI/httpx ASGI + mock Fish：23 个测试通过，覆盖鉴权、JSON/MessagePack、参考白名单、大小/并发限制、完整音频、HEAD/Range、跨 secret 隔离、持久幂等、并发同 key、pending 重启、TTL、权限、符号链接、容量和进程锁。
- 已在系统 Python 的 FastAPI 0.109/Pydantic 2.10 上运行，也在项目 `.venv` 的 FastAPI 0.143.1/Pydantic 2.14.1 上验证兼容；生产依赖钉在 [requirements.txt](./requirements.txt)的项目运行时版本，实际容器构建尚未运行。
- 可在隔离开发环境安装 [requirements-dev.txt](./requirements-dev.txt)，然后从仓库根目录运行 `python -m pytest -q integrations/newapi/fish_bridge/test_app.py`。
- 未进行完整 New API HTTP→桥→真实 Fish 链路、真实扣费、服务器内存负载或网络隔离验收；不得将离线通过标为上线完成。
