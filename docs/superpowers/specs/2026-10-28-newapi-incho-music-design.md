# New API Incho 音乐适配

范围为本地 `newapi_incho_v1` 协议、原任务恢复及已验证音频交付。保留订阅和自定义模式；不更改生产、套餐、点数或已启用模型。

## 精确协议与准入

- 依据公开固定提交中的 Incho 1.0.2 插件源码，SHA-256 `0fcda09aef4aeabc6a6a8449d4742ac887c390d49324b2d3f2083b2ca5ad1479`。不执行插件源码。
- 配置使用根 URL。唯一生成入口为 `POST /incho/submit/music`，JSON 包含显式 `model`、`task_type=normal`、`n` 及提示词／歌词。上游模型只允许 `v3.5` 或 `v4.0`；不接受 `v4.5`，也不将它映射为其他版本。
- `n` 默认为 2，只允许整数 1 或 2；提示词／歌词分别限制 1000／3000 Unicode scalar，拒绝代理码点、布尔数量、未定义参数及素材。请求规范保持中央已有哈希、幂等和预算边界。
- native decode 使用路由模型 `incho_music`，渠道 `model_mapping` 会覆盖 body.model。因此生产启用前必须核对所有候选渠道的 `incho_music` ability、分组及映射，保证所选上游精确一致。仅模型目录出现 `v3.5`／`v4.5` 不足以通过此门槛。

## 生命周期与交付

- submit 只接受 `code=success,data=非空字符串 ID`，先持久关联原 ID，再进入已有持久 GET 队列。提交响应未知且无 ID 时保留预留，不重发生成。
- 查询仅为原 ID 的 `GET /incho/fetch/{id}`。外层 `task_id` 必须精确匹配；外层状态必须与全部 choices 的状态一致。拒绝未知状态、重复曲目、超过 2 首或超过冻结请求 n 的结果。
- `SUCCESS` 仅在所有 choices 都终态并至少一首 `done` 时成立；只有 `done` 且含 `audio_url` 的曲目才进入交付。失败／取消曲目即使附 URL 也不作为成品。缺失 done 音频地址视为不确定结果，不提前收费。
- 音频只从显式 origin 和路径前缀下载，禁止重定向、域名通配、授权头转发或请求任意 URL。沿用音频完整解码、最大时长／字节／声道／采样率、稳定 storage key、owned artifact 及总文件数预算。
- 按已校验并保存的音频数量或总时长计量；不把请求 n、duration 字段或插件展示 usage 当作实际账单。失败／取消本身不证明供应商费用为零。
- 020 迁移只扩展 `media_work_adapter_check`，001–019 原样保留；不种任何模型或费率。配置缺解码工具、下载白名单、适配迁移或已验证模型定义时保持关闭。

## 验证与开放门槛

本地验证包括协议红绿、真实 PostgreSQL 事务／迁移、受控供应商 HTTP、真实合成音频全解码、双曲目一次结算、GET 恢复／下载丢失、未知 POST 不重试、跨用户及撤回后不交付。生产渠道／精确映射、供应商真实请求、真实成品与费率尚须独立验证；本设计和模拟协议通过均不能替代它们。

取消已派发请求先保留预留与 `actual=null`。中央继续恢复原 ID 的可信结果，实际保存的原成品可按证据结算；本机 host 的停止／换号栅栏另外阻止向旧工作区发布文件，不通过取消伪造零费用或退款。

实际 Pi SDK 会把某些不匹配类型（例如 `n=true`）的工具请求传给 host，因此 SDK 校验不能作为付费权限边界。Python host 必须按真实发布 schema 独立验证类型、上下限和未知键，并在 mint 前拒绝无效参数；中央和协议适配器仍各自验证。

## 2026-10-28 只读生产前提

根代理本轮只读确认 channel 7 的插件为 Incho，状态开启，模型仅 `v3.5`／`v4.5`；default abilities 仅这两个模型，`incho_music` 没有 ability，三者的 `model_mapping` 均不存在。因 native decode 固定使用 `incho_music`，当前目录不足以调通该路由。

生产接入需运营方先明确新增 `incho_music` 路由 ability 及相应业务令牌范围，同时核对所有候选 Incho 渠道没有覆盖所选显式 body.model 的映射。本适配器准备的 `v3.5` 请求必须仍到达 `v3.5`；`v4.5` 保持不支持，不改名、不降级。原有两条目录名、渠道、映射、商业费率及启用状态，本任务均不写入。

公开依据：[Incho 1.0.2 固定源码](https://github.com/QuantumNous/new-api-plugins/blob/423535f2c6d46171d2e9553c6d33a0987116aa63/plugins/tasks/incho/1.0.2/plugin.js)。Context7 `/quantumnous/new-api` 当前公开文档另外确认插件可声明 native submit／query 路由；Incho 的细节以固定版本为准。
