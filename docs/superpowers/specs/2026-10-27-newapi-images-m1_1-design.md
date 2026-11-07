# M1.1：New API 图片接入与原体验权益扩展

本设计落实用户新增生图模型后的配置更新请求。用户将本次体验定价及范围交由项目方决定；主任务确定：`gpt-image-2.5-flare`、`gpt-image-2.5-sunburst` 均按 **1 点／完整验证交付的图片**，即 `1000000 microcredits/image`。仅 `n=1`、`size=1024x1024`；不发布尚未验证的编辑、quality 或其他参数。正式商业价不由此体验规则自动产生。

真实协议探针由主任务在服务器内完成一次 flare POST：HTTP 200，body 只有 created/data，data 一项 URL，下载 origin `https://api.image2pro.top`，没有 usage。实际 X-Oneapi-Request-Id 为网关关联标识；UUID 形态的 x-request-id 不充当供应商对象。原始证据、签名 URL 和密钥留在服务器。图片完整下载／解码、sunburst 独立验证和中央链路真实验证分别记录，不能用目录可见或 POST 200 代替。

## 候选来源与变更边界

基线仅使用已独立审查的 M1 wheel，SHA256 `b070d12f7c48f65709ebdfb295a76a2e1e45b79b612404893e5e541f00d1dfd9`。隔离工作目录 `/private/tmp/easel-media-m1.1-staging-20261027/` 从该 wheel 解出，保留旧迁移 001–015、图片解码、配额、GC 和资金守卫的原始字节。

当前共享树中的未完成 M2/M3 不进入候选。新增迁移为 `016_m1_image_enablement.sql`；M3 的 017 待本迁移冻结后由其所有者去除重复 typed 底座，只保留声音阶段增量，当前不自动移动文件。尚未落地的 M2 工作迁移计划顺延 018。候选经独立审查后再将准确增量 backport，避免从整个工作树混打。

## 明确的网关 HTTP 引用

私有 provider 配置增可选 `response_profile="newapi_images_v1"`。缺省不追加配置字段到旧 binding hash，旧配置 hash 保持原样；显式 profile 纳入 binding hash，不能在同一已接受任务中改变解释。

该 profile 只声明已验证的 `image.generate`。它要求真实 `X-Oneapi-Request-Id`，按固定 New API rc.45 格式校验为 23 位时间数字加 16 位字母数字。采用固定命名空间 `newapi_x_oneapi_request_id_v1`；不回退到 UUID x-request-id、created、请求 UUID 或随机生成值。终态与回执的 `provider_response_id` 为 NULL，`reference_kind` 为 `http_request_id`。

通用核心接口保持旧参数并增可选字段：

```python
record_http_response_reference(
    job_id, attempt_id, *, namespace, http_request_id, response_body_sha256
)
record_terminal_evidence(
    job_id, attempt_id, *, terminal_status, provider_response_id,
    quantities, basis_evidence, evidence_hash,
    reference_kind="object_id", http_reference_namespace=None,
    http_reference_value=None,
)
```

原 object 分支仍要求真实关联的 provider_response_id；HTTP 分支严格要求该字段为空。`media_http_response_references` 追加记录绑定 job、attempt、binding、provider_scope、命名空间、原 header 值及精确响应 body SHA。不可修改／删除；同 provider_scope＋namespace＋ID 不得用于第二个 job。终态的复合 FK 要求 evidence_hash 等于已持久化 body SHA；回执的全部 typed 字段必须与终态一致。保留 `openai_x_request_id_v1` 的 req_* 合同供后续 M3 使用，不加入其未完成的声音表。

锁顺序沿用 wallet → invocation → job → reservation，之后才取得 provider reference 去重锁；不存在反向获取这些锁的路径。网关 POST 保持一次；header、body 引用和 owned artifact 均持久化后才产生终态／结算证据。

## 计量与目录

新 profile 仅接受 `image / verified_delivery_v1`。没有 raw usage 的模型不能发布或执行 token 费率；能力查询、目录、mint、派发四处均关闭这种组合。图片数量只取真实全解码、SHA 验证、登记成功的 owned artifact；URL 数量、HTTP 成功、网关补 1 或预估量均不能扣点。

本次明确 profile 在付费 POST 前还要求参数恰好为 `n=1,size=1024x1024`，不隐式采用质量、其他尺寸或数量。完整解码后、stage_blob／文件写入／artifact 登记之前，再将实际宽高与冻结请求尺寸比对；2×2 或其他不符产物不构成交付。它保持 pending、actual=NULL、原点数预留及完整存储预算，没有 terminal／receipt，原操作键不重新生图。legacy 无 profile 分支不增加此尺寸限制。

下载超时、坏图片、关联头缺失、未知响应继续 pending 并保留预留和存储预算；幂等重放只查询旧 job，不重新 POST。已知交付和财务恢复分离，现有配额／GC／session 撤销规则保持。

live 的 image.* 定义必须明确使用 `feature="image"`。既在发布合同校验，也在旧定义读取及派发时核对；不能把 `feature="text"` 的误配作为原文本会员生图的入口。合成 test_only fixture 保留原 feature 值，不改历史 definition/hash；已经派发的已知费用恢复不被新发布门槛丢弃。

## 原体验授权的非资金扩展

007 原 internal plan 固定为 text、10 点、7 天、并发 1，有逐字保护。扩展不修改它，也不增加 0 资金会员或额外点数。

新增不可变 `internal_feature_extensions`，精确绑定原 internal_grant、environment、user、tenant、wallet、product=easel、feature=image、原 starts_at/expires_at，记录 operator、reason、幂等 key 与 created_at。每个 grant/product/feature 仅一笔，environment/key 唯一；数据库守卫要求所有原归属和期限完全相等，来源会员仍有效。没有 amount、credits、days 或 concurrency 参数。

owner-only `InternalFeatureService.extend()` 需要 `internal:extend_features`，且当前 DB 连接为 schema 所有者的原始会话。它的参数为 environment、grant_id、product、feature、key、reason。运行角色只获 SELECT；没有此表 INSERT/UPDATE/DELETE 权限，也没有网页自助入口。

单独的 `feature_memberships()` 返回原有效会员对象，definition 不变；只有绑定的原 owner／产品可使用扩展。Media entitlement 和产品 entitlements 投影消费该 helper。`active_plans()`、文本执行校验、旧 plan/grant/lot/journal、余额、到期时间、并发和支付记录不修改。授权记录本身是不可变运营审计，不伪装资金账本事件。

## 待应用配置差异

下列为部署时的明确差异，替换项必须由服务器现有私有配置与证据填充，不能直接拿占位符发布：

```json
{
  "mode": "live",
  "storage_root": "/<service-owned-private-directory>/media",
  "concurrency": 1,
  "timeout_seconds": 180,
  "providers": {
    "newapi_images": {
      "version": "newapi-rc45-images-20261027-v1",
      "base_url": "https://<verified-existing-gateway>/v1",
      "secret": "<read-existing-business-token-on-server-only>",
      "models": {
        "gpt-image-2.5-flare": "gpt-image-2.5-flare",
        "gpt-image-2.5-sunburst": "gpt-image-2.5-sunburst"
      },
      "adapter": "openai_images_v1",
      "protocol_version": "1",
      "capabilities": ["image.generate"],
      "download_origins": ["https://api.image2pro.top"],
      "response_profile": "newapi_images_v1"
    }
  }
}
```

配置文件仍需 service UID 所有、0600、非链接，storage 0700。live HTTPS 校验不因服务器内部 HTTP 探针而放宽。每个模型单独发布同结构定义，`id/name` 替换为对应别名；`verification_ref` 必须对应该模型实际产物／协议验收证据，不能将 flare 的成功冒用给 sunburst：

```json
{
  "id": "<one-verified-model-id>",
  "version": "20261027-internal-image-v1",
  "name": "<one-verified-model-id> · 内部体验",
  "capability": "image.generate",
  "feature": "image",
  "products": ["easel"],
  "test_only": false,
  "provider_id": "newapi_images",
  "provider_version": "newapi-rc45-images-20261027-v1",
  "adapter": "openai_images_v1",
  "protocol_version": "1",
  "verification": "live",
  "verification_ref": "<server-evidence-reference-for-this-exact-model>",
  "rate": {"version":"internal-one-point-per-image-20261027-v1","meters":[
    {"unit":"image","basis":"verified_delivery_v1","numerator":"1000000","denominator":"1"}
  ]},
  "limits": {"max_input_bytes":65536,"max_assets":0,"max_artifact_bytes":20971520,"max_units":{"image":1}},
  "parameters": {"type":"object","properties":{
    "n":{"type":"integer","minimum":1,"maximum":1},
    "size":{"type":"string","enum":["1024x1024"]}
  },"required":["n","size"],"additionalProperties":false}
}
```

publish 与 set_enabled 分开，未验收模型保持关闭。原体验权益扩展仅对主任务已确认的那一笔原 grant 执行一次；不遍历全体账户。应用前后比对原记录／余额／到期／并发，并验证客户点数恰好扣 1、重放不再扣款。工作台图片 host/client 接入属于 M4，不能由本次后台接口完成状态替代。

## 验证门槛

已先观察新增合同／权益测试失败，再实现；最终记录定向与完整回归的独立运行结果。必须覆盖 typed 头与 body SHA、跨 attempt/scope 复用、无 usage 固定交付、未知结果不重派、SQL 不可变守卫、真实非 owner runtime 权限、原财务行逐字不变及 live image 不借用 text。

016 还需真实 schema dump→restore，核对完整原始 schema 目录哈希和所有约束 deferrable/deferred flags；不放宽比较器忽略括号差异。新增 CHECK 使用 flat AND，保留旧迁移原字节。独立复审、备份／隔离恢复和 root 的具体部署计划完成之前，不在生产扩权益、发布或启用模型。
