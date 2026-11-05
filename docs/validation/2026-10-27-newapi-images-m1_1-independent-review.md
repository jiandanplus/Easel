# M1.1 New API 图片与原体验权益：独立审查

> 快照状态：本报告记录旧候选 `02626db…6a2209`。随后主任务明确将真实 1024×1024 尺寸一致性作为交付与收费前提，新候选 `f50e5251…705abf` 增加该 gate，独立回归已完成（1322 passed、2 skipped），见 `2026-10-27-newapi-images-m1_1-size-independent-review.md`。旧 1317/2 与下述回环证据保留，不能用来替代新尺寸候选验收或生产批准。

审查日期：2026-10-27。审查人：独立只读 agent `delivery_media_review`。

**结论：在下述冻结候选、隔离数据库和合成供应商协议范围内，没有剩余已知阻塞问题。完整中央候选回归为 1317 passed、2 skipped，exit 0。** 本结论不等于真实媒体调用、生产启用或用户桌面端验收完成；这些由主任务分别取证。

## 冻结输入

候选仅来自 b070 M1 wheel 的独立 staging，不从共享工作树打包。共享树中的暂停 M2/M3 增量不纳入此次结论。

- 基线 wheel SHA256：`b070d12f7c48f65709ebdfb295a76a2e1e45b79b612404893e5e541f00d1dfd9`。
- 本次 wheel：`/private/tmp/easel-media-m1.1-staging-20261027/dist/easel_central_platform-0.1.0-py3-none-any.whl`。
- 本次 wheel SHA256：`02626db045e2a4f3ed7e82e151704b9aeec94bc0ad2e19052e545150376a2209`。
- 83 个归档文件通过 CRC；76 个 py/sql 文件逐字节等于 staging 源文件。旧 001–015 迁移逐字节等于 b070 基线。
- candidate-manifest SHA256：`9eb82d20233578cdb63db5dd96d88d6f0765dd2119bafcdfcc4e69426898f954`。
- support-manifest SHA256：`09bda4d52bf1fdb3150d9cb619408853fd1fd02019638f4a85bf8b323220163e`。

11 项源增量、5 项修订或新增测试、189 个真实测试支持文件在完整回归前后逐项 hash 验证一致。`.pytest_cache` 是实现者最后一轮测试更新的派生缓存，独立回归关闭 cacheprovider，且仅将该缓存排除于支持文件 hash 核验；未排除任何实际源码、断言、脚本、静态资源或配置样例。

## 已修复的审查问题

1. **live 图片借用 text 权益。** 初版可将 live image.* 配成 `feature=text`，借用原文本会员。最终在 `media/contracts.py:78` 的发布校验、`media/core.py:74` 的旧目录读取和 `media/core.py:211` 的派发校验同时关闭。新增反例还直接插入模拟旧误配目录，验证列表为空、mint 拒绝、没有预留；未修改原 definition/hash，也未将已派发的已知费用恢复丢弃。
2. **误授 INSERT 不能被启动检查发现。** 默认运行权限已无扩权益表写入，但初版安全检查遗漏意外的表级/列级 INSERT。最终 `permissions.py:223` 将新表纳入敏感目录写入检查。实际 non-owner runtime 能读取权益，不能扩权益、插入/修改/删除/清空历史或禁用触发器；两个误授反例均拒绝，重新 provisioning 后移除权限。

上述问题均在本次冻结后完整独立回归中覆盖。没有以改弱断言、扩真实授权或新增资金记录绕开问题。

## 核验的关键边界

- `response_profile=newapi_images_v1` 必须显式配置并进入新 binding hash；缺省 profile 的旧 binding 字节保持相同。新 profile 仅声明 image.generate，关闭编辑、旧变体和 response_format 配置。
- 采用固定 `newapi_x_oneapi_request_id_v1` 命名空间及真实 `X-Oneapi-Request-Id`。不将转发的 UUID x-request-id、created、客户端请求 ID 或随机值冒充供应商对象；该分支 provider_response_id 为 NULL。
- `016_m1_image_enablement.sql:5` 的不可变 typed 引用绑定 job/attempt/binding/provider_scope、真实响应 body SHA。相同 provider_scope+namespace+ID 不能支付第二个 job。终态复合 FK（:34）和回执一致性守卫使 typed tuple/body SHA 不能被调换。
- 没有 usage 的 New API profile 仅支持 image/verified_delivery_v1。目录、能力、mint、dispatch 排除 token 计量；计费图片数量来自已完整解码、校验并登记的 owned artifact。测试中的费率是合成费率，不把它冒作正式或体验价格发布结果。
- URL 下载维持 origin allowlist、无 Authorization、无重定向及原图片资源限制。新协议 fixture 验证 POST/GET、全解码、下载、不可变证据、结算和重放；下载超时、坏图及无效关联头保留未知状态和预留，同原键重放不再 POST。
- `internal_feature_extensions`（迁移 :126）只追加原 internal_grant 的 image 权益，绑定原 environment/user/tenant/wallet/product=easel 和原 starts/expires。helper 只返回原有效会员，不重写原 text plan、grant、10 点/7 天 lot、会员、并发或账本；到期/当前授权仍由原 active_plans/authorize 限制。
- 扩权益操作需要 schema owner 原始连接及 `internal:extend_features`，无 HTTP 自助入口。幂等、并发重复、错环境/用户/租户/钱包/产品/期限及修改历史反例均验证。运行角色只读扩权益历史；停用原用户后权益查询被拒，审计保留。

本轮没有访问业务密钥，没有真实供应商 POST，没有生产 SQL，也没有发布/启用模型或扩展真实用户权益。部署还需 owner 迁移 016 后重新授予精确 runtime 权限；旧 015 权限不会自动覆盖新表。

## 独立执行证据

完整回归使用 `/private/tmp/easel-media-m1.1-validation-20261027/services/platform/tests` 和冻结 staging/src，保留相对脚本结构。验证 runner 与对应测试的迁移清单精确增加 016，未忽略 prepare-only 的版本断言。

```text
1317 passed, 2 skipped in 286.78s (0:04:46)
exit_code=0
```

两个新增模块共收集 24 项，均包含于上述完整执行且通过。仅排除候选包中不存在的 6 个暂停模块：test_media_audio_protocol、test_media_audio_references、test_media_voice_protocol、test_media_voice_stages、test_media_resources、test_media_av_artifacts。此结果称为完整**候选**中央回归，不能扩称当前混合工作树所有 M2/M3 已通过。

两项 skip 的精确理由均为 `explicit synthetic Docker environment required`：`test_encrypted_restore_drill.py:100`、`:350`。本轮没有将其设置为通过。

隔离 PG 为本轮专用的 `easel-central-all-test-6cbb1f709541`，使用已有受限磁盘卷、随机 loopback 端口及 0600 私有 DSN 文件，只创建随机合成 schema。原应用容器、原 OOM 测试对象和数据卷均未清理。

原始测试日志：`/private/tmp/easel-media-review-20261027-m11/candidate-central-full.log`。机器可读安全证据：本目录同名 `-safe.json`；不包含 DSN、业务密钥、供应商签名 URL 或真实用户资料。

## 016 真实 schema 回环

独立执行全迁移至 016 → PG16 schema-only plain/custom dump → 仅删除本轮新建随机 schema → pg_restore → 原始 schema 和全部约束 flags 比对。

首次 restore 的原始比较**失败**：529 条约束中恰有 R2 v6 已知的 7 条旧 CHECK 在 PostgreSQL dump/reparse 后扁平化 BETWEEN 的嵌套 AND；其余定义及所有 flags 相同。原始 before/after、失败回执及 restored schema 保留，没有放松 fingerprint 或旧迁移。

随后校验并复用冻结 R2 v6 工具 SHA256 `4d3c4210469d6630dd4cbc71d8d5327e62aa972eedb7084685bb0df85a2c88ea` 的 **exact 原定义/目标定义/flags gate**，只在本轮 owned schema 内同事务 DROP/ADD 那 7 条已固定旧模板。没有泛化解析、忽略括号或改动 001–015。重建后：

- 原始 plain pg_dump 逐字节完全相等，SHA256：`9c2dee12d3f6f65015fa6daf60bc3b68a7298fd607fe57b91172c2c30c2c8db7`。
- 529 条原始约束定义、deferrable/deferred/validated/local/inheritance 等 flags 完全相等。
- 016 新增约束无需重建；`media_terminal_http_reference` 仍为 DEFERRABLE INITIALLY DEFERRED。

因此本轮回环结论明确依赖这 7 条已知旧模板的精确源语法重建，不能描述为首次原生 pg_restore 后无条件 hash 相等。该证据是实际 schema 回环，不能替代整套加密数据/角色恢复的两项 opt-in Docker 测试。

## 证据范围

本次可交付结论是该 83 文件冻结后端候选的独立代码、数据库、权限、账务和协议 fixture 验收。两款真实模型各自产物、真实 1 点交付/重放、用户桌面 Pi 调用入口、正式付款开通、服务部署和实际账号权益变更均需主任务各自的证据；未由本报告推定完成。
