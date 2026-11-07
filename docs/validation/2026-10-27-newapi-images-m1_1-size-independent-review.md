# M1.1 尺寸交付 gate：第二次独立审查

日期：2026-10-27；独立只读 reviewer：`delivery_media_review`。

**结论：冻结后端候选 `f50e5251…705abf` 无剩余已知阻塞；独立完整候选中央回归 1322 passed、2 skipped，exit 0。** 本报告替代旧 `02626db…6a2209` 作为本轮后端候选结论；真实模型、部署、真实账号权益操作和 M4 桌面工具分别验收。

旧候选的原始结果、严格 schema 恢复和证据边界保留于 `2026-10-27-newapi-images-m1_1-independent-review.md` 及同名 safe JSON，未重写旧轮子或 safe 回执。主任务随后明确：1024×1024 尺寸必须是收费交付条件，不能仅验证“是一张可解码图片”。

## 新冻结输入与复审

- 独立 staging：`/private/tmp/easel-media-m1.1-size-staging-20261027`。
- wheel SHA256：`f50e52511e941d3bea8e724eddbd68330e20eaa6339c5e2437b3880ca9705abf`。
- 83 文件 CRC 通过；76 个 py/sql 与 staging 源逐字节相同。
- 对比旧候选，产品代码仅 `platform_service/media_runtime.py` 和 `platform_service/media/adapters/openai_images.py` 改变；001–016 的 SQL 逐字节不变。旧来源目录已按原 manifest 还原，旧 wheel 保留。
- manifest SHA256：`9bb0c7087a362e568b4a36a1a498a815d289c39daf3b8405d2368615c57f16d6`；support-manifest SHA256：`0c6ea25d55ebc3bb5c4f9cf734989e77fe53bf9736de8a8e608edf0c5c3add8c`。

新增 gate 仅作用于显式 `newapi_images_v1`，旧 legacy 图片协议不变：

1. adapter.prepare 在付费 POST 前严格只接受 `n=1,size=1024x1024`；缺 size、多图、其他尺寸或未经证实的 quality 都拒绝。
2. credential-free 资源受限解码完成后，以完整解码的 width/height 对照冻结 request.size。在 stage_blob、实际文件写入、artifact 注册之前拒绝不符产物。
3. 真实 HTTP body 引用仍保留；尺寸不符不制造终态或回执，不释放原金融预留或输出预算。原键重放只查原 job，供应商 POST 仍恰好一次。

独立读取反例与正例后，在完整回归中核实：正常产物使用真正 1024×1024 PNG；2×2 后端反例 pending、actual=NULL、原 hold/budget 不变，blob/artifact/terminal/receipt 数量为 0，重放不再 POST。没有将“HTTP 成功”或 URL 数量当作完整交付。

## 独立回归

```text
1322 passed, 2 skipped in 296.16s (0:04:56)
exit_code=0
```

两个新增模块共 29 项，全部包含并通过；比旧快照新增 5 项尺寸/参数反例。11 项源增量、5 项修订或新增测试、189 个真实支持文件在运行前后逐项 hash 一致，测试缓存关闭。

两项 skip 仍为 `test_encrypted_restore_drill.py:100` 和 `:350` 的 `explicit synthetic Docker environment required`。6 个未入本轮 83 文件候选的暂停 M2/M3 模块仍明确排除，不能据此称混合共享树 M2/M3 已通过。

完整原始日志：`/private/tmp/easel-media-review-20261027-m11/candidate-size-central-full.log`。机器证据为本目录同名 `-safe.json`。

001–016 的字节完全相同，因此复用上一快照真实 PG16 schema dump/restore 证据：529 条约束定义和 flags 完全相等，包含 typed FK 的 DEFERRABLE INITIALLY DEFERRED。该回环需要冻结 R2 v6 exact 模板重建 7 条已知旧 BETWEEN CHECK 后，原始 dump 才逐字节相等；016 新约束未重建、未放宽 fingerprint。本轮没有把两项 opt-in 加密 Docker 演练 skip 改称通过。

本轮只使用已有隔离 PostgreSQL 的随机合成 schema 和 synthetic provider transport；未读取业务 Key、未真实供应商 POST、未写生产 SQL，未启用模型或扩大真实权益。正式支付继续关闭，本报告不对部署后的真实图片/收费作推定。
