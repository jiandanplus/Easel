# M3 r2 完整中央套件最终验证

**1794 passed、7 skipped、0 failed、0 errors，316.31 秒，退出码 0。** 唯一工具会话 `77597` 从启动观察到终态；本次没有自动重开或重复focused。七项恢复环境skip逐项列在下方，不算通过。

## 冻结输入与唯一修改

本次仍冻结相同423路径，包含完整中央Python/SQL、scripts/tests/live runner，以及已纳入的桌面M4/auth/Pi源码与相关依赖superset。运行前和运行后逐SHA比较全部一致。与前两次完整运行的旧冻结相比，唯一输入变化是 `services/platform/tests/test_media_storage.py:501` 的显式expected列表增加 `019_media_voice_runtime.sql`。列表未改为动态推导；原回填、幂等、钱包与权限断言仍在。业务代码、lease、鉴权新鲜度、迁移字节未因此修改。

- 新input-manifest SHA-256：`a0881006646406dbabd78f112f7b6506c1bafdcb6b205a1a619d5edf434bbeed`。
- 新input-source-snapshot.tar.gz SHA-256：`657d8540c22dfcd1e65724580a4a777ca21ac2663bb7a2e7c6d168931ff12ffb`。
- 唯一变化文件SHA-256：`98fc621d02320c55cd0a84c48c95807ea94d878b2a5dad9369e1f3d63f49e183`。
- 001–016和018共17个迁移再次与M2冻结源码逐字节核对一致。
- 前端TS/CSS及前端测试不属于本中央423输入清单，本报告没有把它们算作中央测试。

## 防休眠与时钟证据

仅在pytest生命周期内使用 `/usr/bin/caffeinate -i -s`，退出时自动释放；未修改永久电源偏好、系统时钟、Docker或数据库配置。外部观察器计划每0.5秒记录安全wall/monotonic差分。

- 采样次数：629。
- wall elapsed：317.360425秒。
- monotonic elapsed：317.357699秒。
- 最大相邻wall/monotonic差：21.1970微秒。
- 最大实际采样间隔：0.521680秒。
- clock discontinuities：0；423输入变化：0；临时断言进程已退出。

## 完整命令

```text
/usr/bin/caffeinate -i -s /Users/jiandan/Code/GitCode/Easel/services/platform/.venv/bin/python -m pytest services/platform/tests -q --tb=short -rs --junitxml=/private/tmp/easel-m3-final-central-final-20261028/full-central.junit.xml
```

使用既有127.0.0.1:50958测试PostgreSQL，每个测试拥有随机schema；凭据只由原受保护文件提供，不写入安全收据。真实ffmpeg/ffprobe验证本地合成媒体，供应商HTTP为受控fixture。没有新建Docker、SSH、生产或真实供应商动作。

## 七项明确跳过

- `services/platform/tests/test_encrypted_restore_drill.py::test_actual_synthetic_exported_snapshot_dump_and_fresh_restore`：explicit synthetic Docker environment required。
- `services/platform/tests/test_encrypted_restore_drill.py::test_public_r2_fresh_restore_keeps_raw_schema_and_all_constraint_attributes[public]`：explicit synthetic Docker environment required。
- `services/platform/tests/test_encrypted_restore_drill.py::test_public_r2_fresh_restore_keeps_raw_schema_and_all_constraint_attributes[nonpublic]`：explicit synthetic Docker environment required。
- `services/platform/tests/test_media_restore_drill.py::test_full_016_owned_registry_ciphertext_nonempty_fresh_restore`：explicit owned synthetic Docker environment required。
- `services/platform/tests/test_mixed_media_restore_drill.py::test_two_pngs_plus_erofs_pending_hold_and_absence_survive_fresh_cipher_restore`：explicit owned synthetic restore environment required。
- `services/platform/tests/test_settled_media_restore_drill.py::test_actual_nonpublic_window_resolves_immutable_storage_function_in_scope`：explicit owned synthetic source handle required。
- `services/platform/tests/test_settled_media_restore_drill.py::test_two_settled_pngs_typed_receipts_wallet_and_raw_inventory_survive_fresh_cipher_restore`：explicit owned synthetic restore environment required。

这7项及原因与前两次full完全相同。它们需要单独、明确的合成恢复环境或owned source handle；本次不补做，也不冒称恢复演练通过。

## 证据与历史保留

证据目录：`/private/tmp/easel-m3-final-central-final-20261028/`。保存完整日志、JUnit、423输入manifest与源码快照、前后hash、工具会话和时钟观察器源码/日志。安全结构化结果见同名 `.safe.json`。

原session61826的90failed/7errors以及session70290的2failed记录全部保留，未覆盖或改标；旧229、243、247定向收据与各候选源码包也保留。原两条迁移expected红测之后仅追加019，单独两节点2pass/0.92秒，再执行了本次完整验证。

本次满足完整中央测试门槛（明确7项skip），并不代表真实渠道、商业费率或生产部署完成。017/019仍未在生产部署；缺真实渠道/费率的能力以及缺安全素材producer的旧DashScope enrollment仍closed。
