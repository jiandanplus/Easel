# 订阅媒体服务：M0 / M1 运维边界

当前实现是中央共享钱包上的媒体合同与同步图片数据面。默认 `CENTRAL_MEDIA_CONFIG_FILE` 未配置，媒体目录为空，准入关闭；部署文本模型不会自动开放图片、视频、音乐、语音识别、语音合成或音色能力。现有 New API 的 DeepSeek 文本模型不能用作媒体供应商。

## 开放顺序

1. 使用 owner 迁移账号应用迁移 014，再重新授予 runtime role 必需权限；业务服务继续使用受限 runtime role。
2. 在服务器独立维护 `0600`、归服务进程 UID 所有的媒体配置文件，所在目录为私有目录。通过 `CENTRAL_MEDIA_CONFIG_FILE` 传文件路径；密钥仅放服务器配置，不放桌面、网页、仓库或模型工具参数。
3. 使用 `MediaService.publish(definition, operator)` 发布不可变模型合同，`operator` 必须具备 `media:publish`。模型、功能、供应商及版本、适配器和协议版本、参数 schema、实际验证引用、输入和产物限制、费率单位和计量来源必须明确。费率或合同变化发布新 ID / 版本，不原地修改。
4. 独立核对对应 test / live 套餐权益和资金类型。测试定义只能使用 test 模式；正式定义要求真实验证来源与 live 模式。仅有私有 provider 配置、HTTP 200 或供应商宣传不代表模型可用。
5. 验证后通过受限运营流程显式 `set_enabled(model, True, operator)`；目录与准入仍逐次检查当前账号权限、产品、套餐、模型状态与配置绑定。目前没有公开网页自助开放模型或任意费率写入口。

示例仅说明文件结构，不能直接用于生产，也没有默认费率：

```json
{
  "mode": "live",
  "storage_root": "/var/lib/easel-central/media",
  "concurrency": 1,
  "timeout_seconds": 180,
  "providers": {
    "YOUR_PROVIDER_ID": {
      "version": "YOUR_VERIFIED_PROVIDER_VERSION",
      "base_url": "https://YOUR_VERIFIED_PROVIDER_HOST/v1",
      "secret": "REPLACE_IN_SERVER_PRIVATE_CONFIGURATION",
      "models": {"YOUR_PUBLISHED_PUBLIC_MODEL_ID": "YOUR_VERIFIED_UPSTREAM_MODEL"},
      "capabilities": ["image.generate", "image.edit"],
      "download_origins": [],
      "legacy_variations": false
    }
  }
}
```

正式 provider 和下载 origin 必须使用 HTTPS，不跟随重定向；test 模式仅接受字面 loopback HTTP，不能把测试点数用于公网或正式供应商。`download_origins` 是明确允许的 origin 根地址；默认只接受 base64 图片。若供应商返回签名图片 URL，必须先验证 origin 并显式配置，下载时不转发供应商 Authorization。`response_format` 可按已验证接口声明 `b64_json` 或 `url`；缺省不强行加入此参数。

OpenAI 官方已停用 `/images/variations`，不能默认宣称支持。仅已验证仍提供该旧协议的供应商可同时声明 `image.variation` 与 `legacy_variations=true`；本适配器只接受单张小于 4 MiB 的 PNG 方图和该旧协议的明确参数，不会自动改走 edits。

## 产品合同与一次性执行

| 入口 | 认证与用途 |
| --- | --- |
| `GET /v2/products/{product_id}/media-models` | 当前产品会话；只返回当前可用模型、功能、费率和参数约束 |
| `POST /v2/media/task-tokens` | 产品会话；`{model, capability, invocation_key}`，签发至多 5 分钟的任务凭据 |
| `POST /v2/media/assets` | 短期任务凭据；原始 PNG / JPEG / WebP 字节，服务端真实解码、散列并登记所有权 |
| `POST /v2/media/jobs` | 短期任务凭据与 `Idempotency-Key`；持久准入和资金预留后最多一次供应商 POST |
| `GET /v2/media/operations?invocation_id=...&operation_key=...` | 当前产品会话；按原操作键精确查询 |
| `GET /v2/media/jobs/{job_id}` | 当前产品会话；执行、交付、资金状态分别展示 |
| `POST /v2/media/jobs/{job_id}/cancel` | 当前产品会话；派发前可释放，派发后仅真实记录取消申请和待核算 |
| `GET /v2/media/artifacts/{artifact_id}` | 当前产品会话；安全元数据，隐藏物理存储位置 |
| `GET /v2/media/artifacts/{artifact_id}/content` | 当前产品会话；所属账号／产品验证后，校验文件权限、大小和 SHA256 再下载 |

任务正文示例：

```json
{"model":"YOUR_PUBLISHED_PUBLIC_MODEL_ID","capability":"image.generate","prompt":"用户当前指令","assets":[],"parameters":{"n":1}}
```

`assets` 只接受本次 invocation 拥有的上传 ID；不能传本机路径、任意 URL、钱包、扣点数或 provider key。图片编辑与 mask 均从已验证素材读取。模型参数必须同时通过不可变模型 schema 和固定图片适配器允许列表。

同操作键、同规范正文返回原 job；不同正文冲突。精确重放查询不重新读取已失效的输入，也不重新派发。网络超时、断连、响应缺少可信关联、无效图片、用量不可信、超额用量等结果保留原请求和预留，进入待核算；不自动换模型、不重新生成、不伪造零用量。同步 HTTP 连接中断后服务端监督任务仍持有原任务，重启恢复仅处理财务状态。

服务先保留供应商 HTTP / response 关联，真实验证并登记产物，再提交不可变终态证据，最后由中央账本结算。结算暂时失败时产物仍可交付，资金为待核算；恢复读取原证据结算，不再次调用供应商。客户端不自报权威图片数量或 token 数。M1 仅支持 `image/verified_delivery_v1`、`unicode_scalar/verified_input_v1` 和 `input_token/output_token/provider_usage_v1` 的明确组合；其它计量来源在目录、签发和新准入时关闭，不以不相干的 usage 字段代替权威数量。

## 资源、存储与恢复

上传原始字节默认最多 20 MiB，同时受模型输入限制约束；上传有最长 30 秒期限。图片解码在独立、无供应商凭据环境的子进程中完成，强制完整 decode，限制单帧、字节、维度、像素、CPU 与墙钟时间；Linux 另限制地址空间。服务默认只并行 1 个生成任务，配置最多 4 个。使用专用低权限 Linux 账号和服务管理器内存限制，不把 macOS 预览结果当作 Linux 内存隔离验证。

文件存放于 owner-only `0700` 目录、`0600` 普通文件，固定散列元数据与不可预测键；拒绝符号链接、路径穿越、文件被替换或内容改变。付费调用前检查磁盘余量，并在准入事务中预留最大输出字节和文件数；额度不足不会调用供应商或保留资金预留。实际产物将预留存储预算转为真实占用，已知终态释放未用部分，未知任务保留。

迁移 015 增加中央存储 policy 和 blob registry。默认按 user / tenant / billing account 跨产品合计 256 MiB / 128 个文件，全局 2 GiB / 4096 个文件；runtime 不能提高上限。上传在写文件前持久化 staging，所有未确认物理删除的 staging、素材、产物和输出预算都占额度。配置上限为受限 owner 运维事项，不能经用户网页或任务参数覆盖。

素材和产物默认 24 小时有效期，到期内容不可下载。媒体 worker 先对无活跃或未知任务 pin 的过期记录持久化软删除和 GC 队列，再校验实际文件 key / SHA256 并幂等 unlink，最后确认后才释放存储配额。下载持 SQL FOR SHARE 直到文件 snapshot 完成，GC 在锁后再次检查 pin。写前／写后／登记提交未知均有 staging 记录。完整 `.part` 仍须匹配预期长度、SHA256、UID、0600 和 inode 才可自动清理；不完整／不可核验部分文件会保持 GC 失败和额度占用，由私有受限运营按确切文件核对、备份和授权后恢复，不能把 final 缺失当作已清理。错误 hash、符号链接和非 registry 文件不被删除。queued / running / cancel_requested / unknown 的素材、产物与 staging 不按 24 小时强制清理。不能直接定时删除整个媒体目录。

当前使用单个持久私有卷，不宣称已具备通用对象存储或多节点媒体 worker；备份应同时覆盖业务数据库、blob registry 和对应文件。未知请求仍需可信证据或受限运营核账，不能通过删除文件释放金融预留。

Pillow 固定为 12.3.0，仅安装到中央服务依赖；其许可证随中央 wheel 放入 `platform_service/licenses/`。桌面 Agent 运行时不因此增加 Pillow。

## 验证与当前缺口

测试使用固定合成图片、httpx 协议 fixture、真实 Pillow 完整解码和独立 PostgreSQL schema；覆盖合同、资金守卫、实际产物 SHA、所有权、重放、超时、HTTP 身份撤销和证据持久化后的财务恢复。它们不代表真实供应商媒体生成已通过。

真实开放仍缺媒体供应商、明确的模型／能力、私有服务器 key、实测费用／计量依据与真实产物验收。异步视频、音乐、语音识别、语音合成和音色数据面需后续适配，合同已保留全部能力，未配置能力继续关闭。正式支付商户配置由用户暂缓，不以测试钱包替代正式付款。

官方依据：[Images API](https://developers.openai.com/api/reference/resources/images)、[旧 variations 停用说明](https://developers.openai.com/api/reference/resources/images/methods/create_variation)、[Pillow Image 验证与解码](https://github.com/python-pillow/pillow/blob/12.3.0/docs/reference/Image.rst)。
