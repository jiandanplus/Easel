# 当前中央候选：M3／Incho 020／关联头／DR 修复验证

**完整套件：1895 passed／14 skipped／0 failed／0 errors，352.26 秒。另一次 SDK 补充：7 passed／0 skipped／0 failed／0 errors，3.16 秒。** 两次结果分列，不累加为单次 full；原7项显式恢复环境测试仍未覆盖。

| 运行 | 唯一工具会话 | 结果 | 冻结范围 |
| --- | --- | --- | --- |
| 全 services/platform/tests | 8911，terminal exit 0 | 1895 passed，14 skipped，352.26 秒 | 431 路径，前后全部 SHA 相同 |
| 仅新增 Incho SDK 7 节点 | 92805，terminal exit 0 | 7 passed，0 skipped，3.16 秒 | 同431源码＋Pi worker/package/lock共434，payload 11,932文件库存前后相同 |

## 新候选及冻结身份

原 M3 1794／7 的完整结果、423源码快照和冻结wheel保持不变。本次输入扩展了Incho适配器／020迁移、New API关联头fallback与mixed DR诊断／finally修复；不能借用旧M3结果代替当前全量。

- full manifest SHA：`5e8c9f416ac13478c332e089e3e557257fc4371090bce68c7256bd812f401cae`。
- full source tar SHA：`54ac2460d2a912eeaaa47f0348ba67fd192965202b22db96f7080b0ec5f9731d`。
- 来源清单为原423路径＋8新增路径，原423中12文件变化。完整新增／变更SHA列表见同名safe JSON，所有原路径保留。
- header作者最后放行：16个test_execution*.py合计220 passed／0 skipped，37.15秒；在其91232 terminal和PG释放后才冻结、启动本轮。header源SHA为 `60b803c0c1de11c69cf8f0a4235c58a3eb9b83477d2b82c38596b72cd83d17c7`。
- 001–016及018共17个受保护迁移，与M2原212源码快照逐字节一致；结果保存于protected-migrations.json。

新增路径：

- `services/platform/src/platform_service/media/adapters/newapi_incho.py`。
- `services/platform/src/platform_service/migrations/020_media_incho_runtime.sql`。
- `services/platform/tests/test_execution_newapi_correlation.py`。
- `services/platform/tests/test_media_incho_protocol.py`。
- `services/platform/tests/test_media_incho_runtime.py`。
- `services/platform/tests/test_media_incho_sdk.py`。
- `services/platform/tests/test_mixed_media_restore_apply_diagnostics.py`。
- `tests/pi_incho_worker_fixture.mjs`。

## 单次完整运行及环境

命令：

```text
/usr/bin/caffeinate -i -s /Users/jiandan/Code/GitCode/Easel/services/platform/.venv/bin/python -m pytest services/platform/tests -q --tb=short -rs --junitxml=/private/tmp/easel-central-m3-incho-header-final-20261028/full-central.junit.xml
```

完整运行显式设置 `PYTHONPATH=services/platform/src:services/platform/tests` 和 `EASEL_CENTRAL_TEST_DSN_FILE=/private/tmp/easel-media-review-20261027-second/test-pg-private.json`。使用原专用127.0.0.1:50958测试PG和每测试随机schema；凭据仍由原私有文件提供，没有打印DSN。供应商HTTP为合成fixture，AV解码使用本机真实工具，没有真实POST、SSH、生产操作或调账。

仅pytest进程生命周期使用caffeinate -i -s，退出后释放；外部watcher每0.5秒采样。699次采样，wall elapsed 353.017686秒、monotonic elapsed 353.002999秒；最大相邻wall/mono差0.000642212秒，最大采样间隔0.699786秒，clock discontinuities为0。431输入变化0；未自动重开或并发重复full。

## 额外SDK skip与独立补充

本轮沿用旧全量环境时没有设置EASEL_PI_TEST_PAYLOAD，新增test_media_incho_sdk.py的7个节点因此跳过。此事实在运行中发现并报告，原full日志／JUnit保留14skip，未修改skip条件或重标结果。按主任务后续明确安排，在原full终态后仅补这7个SDK节点。

补充命令：

```text
PYTHONPATH=services/platform/src:services/platform/tests EASEL_PI_TEST_PAYLOAD=/Users/jiandan/Code/GitCode/Easel/outputs/_run/pi-portable-20261028-v26/Easel/Easel.app/Contents/Resources/runtime/pi /usr/bin/caffeinate -i -s services/platform/.venv/bin/python -m pytest services/platform/tests/test_media_incho_sdk.py -q --tb=short -rs --junitxml=/private/tmp/easel-central-m3-incho-header-final-20261028/sdk-supplement/sdk.junit.xml
```

使用作者final-command.json原指定的v26受控payload，其runtime-manifest SHA `8448f44712ed33164b3e04fd2dc198be2b03ba2bd0f62f237aec3c0d4f7d9783` 与主任务指定v27 Pi身份相同。manifest的11,931项全部校验，包含manifest自身的实际11,932文件全库存前后相等。没有读取用户auth或运行真实供应商。

实际Pi为1.4.0、Node24.22.1。SDK入口和package.json都记录SHA：

- `@earendil-works/pi-ai` 1.4.0：入口 `7808fc618b4c816322005628af2726a53c4db678b37fb311163210c4fbab5ed0`，package.json `36df91142c18a5912eafab0f2783bb4d88cdd101c5c5e7fd9173d4387cc5cced`。
- `@earendil-works/pi-agent-core` 1.4.0：入口 `40cb358d7f05594e9b67845a78ae50ad05e8375f0cfa8cee8a74801556c09afd`，package.json `3ee9a0f81587b917e7a010e4391fc10d2b04fa48091b693bffebe3b9b55e9f14`。
- Node二进制 SHA `6f32069480ec14d52b6d7b1f3c7124cfbbc46203abf5ddf5c65b127435f3f329`。
- worker.mjs SHA `8ec881590206c0c487f95469860f04852b9c1c80b4a198a9fd254fcf00768682`。

SDK实际读取的runtime/pi/worker.mjs、package.json、package-lock.json不在旧423／本次431清单中，因此额外建立434源码manifest与源码tar；不事后声称它们已被full431冻结覆盖。434源码前后全部一致，包含header源；补充使用的Python公共投影／fixture也在同一已冻结431来源中。

## 仍未覆盖的七项恢复测试

- `tests.test_encrypted_restore_drill::test_actual_synthetic_exported_snapshot_dump_and_fresh_restore`：explicit synthetic Docker environment required。
- `tests.test_encrypted_restore_drill::test_public_r2_fresh_restore_keeps_raw_schema_and_all_constraint_attributes[public]`：explicit synthetic Docker environment required。
- `tests.test_encrypted_restore_drill::test_public_r2_fresh_restore_keeps_raw_schema_and_all_constraint_attributes[nonpublic]`：explicit synthetic Docker environment required。
- `tests.test_media_restore_drill::test_full_016_owned_registry_ciphertext_nonempty_fresh_restore`：explicit owned synthetic Docker environment required。
- `tests.test_mixed_media_restore_drill::test_two_pngs_plus_erofs_pending_hold_and_absence_survive_fresh_cipher_restore`：explicit owned synthetic restore environment required。
- `tests.test_settled_media_restore_drill::test_actual_nonpublic_window_resolves_immutable_storage_function_in_scope`：explicit owned synthetic source handle required。
- `tests.test_settled_media_restore_drill::test_two_settled_pngs_typed_receipts_wallet_and_raw_inventory_survive_fresh_cipher_restore`：explicit owned synthetic restore environment required。

这七项节点与原因逐项匹配原M3最终运行。没有显式恢复环境／owned source handle就没有执行，不能算通过；本地测试通过也不能替代真实mixed DR。

## 证据与限制

证据目录：`/private/tmp/easel-central-m3-incho-header-final-20261028/`；SDK补充在其sdk-supplement子目录。完整日志、JUnit、source tar、前后manifest、clock逐样本及唯一工具会话记录均保留。结构化收据见同名 `.safe.json`。

原三次M3 full（包括失败记录）、旧1794结果、r2审查、打包wheel及新full的14skip全部保留。当前真实mixed DR仍为failed／待诊断、models已恢复；本轮未重新执行恢复、部署017–020、开放真实渠道／费率或解决原pending。所验证的是当前本地中央源码与合成供应商协议、另行补足的实际Pi SDK边界。
