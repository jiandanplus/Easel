# M3 r2 完整中央套件：临时防休眠复验

本次仍未全绿：**1792 passed、2 failed、7 skipped、0 errors，320.94 秒**。唯一工具会话 `70290` 观察到终态 exit 1；无自动重开，没有重复 focused。原 session 61826 的 90 failed / 7 errors 收据、输入快照与日志全部保留。

## 输入与执行环境

运行前再次逐 SHA 比较原 423 个输入，全部一致。复用相同源码内容和同一测试命令（完整 services/platform/tests、-q、--tb=short、-rs、JUnit），仍用原本机127.0.0.1:50958、每项随机schema。未修改业务代码、lease时长、登录新鲜度、测试deadline、Docker/数据库/系统时钟或永久电源偏好。

只在pytest生命周期内使用 `/usr/bin/caffeinate -i -s`；测试结束后该进程已退出，临时断言随之释放。外部观察器每0.5秒计划采样，只保存时间差。**636次采样无时钟discontinuity**：wall总321.878296秒、monotonic总321.877082秒，最大相邻差23.2377微秒。最大调度采样间隔1.419202秒，两种时钟同步推进，没有触发暂停阈值。

结束后423个输入SHA仍全部相同。manifest为 `6bfc0f19c59b4a26c1e649c824d6518ad9fe9e5cf8c0f85e6762abd948f83933`，source tar为 `d25a547af5061b5ca44b31c44390b3833afe09d668256ae5aad8e6267cc6929b`。证据目录 `/private/tmp/easel-m3-final-central-awake-20261028/`。

## 两项失败及最小修改范围

同一个测试的两个参数分支失败：

- `services/platform/tests/test_media_storage.py::test_migration015_backfills_legacy014_job_budgets_and_replays_idempotently[False]`。
- `services/platform/tests/test_media_storage.py::test_migration015_backfills_legacy014_job_budgets_and_replays_idempotently[True]`。

两个分支均到达 `test_media_storage.py:501`：expected迁移列表硬编码到018，而实际返回正确新增的019_media_voice_runtime.sql。最小修复是显式expected增加019，保留其余回填/幂等/钱包断言，不修改任何迁移原bytes或业务期限。当前尚未实施该修改，本次不能作为发布通过收据。

本次没有再出现execution/media lease过期或console_authentication_stale。结合原full期间真实Sleep事件、当前无暂停且同源码复验结果，可将原大面积过期错误与主机休眠方向收敛；不将原失败改标为通过。

## 七项跳过逐项保留

- `services/platform/tests/test_encrypted_restore_drill.py::test_actual_synthetic_exported_snapshot_dump_and_fresh_restore`：explicit synthetic Docker environment required。
- `services/platform/tests/test_encrypted_restore_drill.py::test_public_r2_fresh_restore_keeps_raw_schema_and_all_constraint_attributes[public]`：explicit synthetic Docker environment required。
- `services/platform/tests/test_encrypted_restore_drill.py::test_public_r2_fresh_restore_keeps_raw_schema_and_all_constraint_attributes[nonpublic]`：explicit synthetic Docker environment required。
- `services/platform/tests/test_media_restore_drill.py::test_full_016_owned_registry_ciphertext_nonempty_fresh_restore`：explicit owned synthetic Docker environment required。
- `services/platform/tests/test_mixed_media_restore_drill.py::test_two_pngs_plus_erofs_pending_hold_and_absence_survive_fresh_cipher_restore`：explicit owned synthetic restore environment required。
- `services/platform/tests/test_settled_media_restore_drill.py::test_actual_nonpublic_window_resolves_immutable_storage_function_in_scope`：explicit owned synthetic source handle required。
- `services/platform/tests/test_settled_media_restore_drill.py::test_two_settled_pngs_typed_receipts_wallet_and_raw_inventory_survive_fresh_cipher_restore`：explicit owned synthetic restore environment required。

这7项及原因与原失败运行完全一致，均不算通过；没有为执行它们创建Docker或读取真实source handle。旧229/243/247和原full收据全部保留。下一步由根任务决定最小测试合同更新与新的验证，不部署。
