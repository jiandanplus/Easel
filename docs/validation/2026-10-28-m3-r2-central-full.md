# M3 r2 + M4 review-r1 完整中央套件失败收据

本次完整中央套件未通过：**90 failed、1697 passed、7 skipped、7 errors，317.33 秒，退出码 1**。同一工具会话 `61826` 从启动观察到终态；未重启、未重跑、未修改输入。失败不能被先前 focused 247 passed 或独立 8 passed 覆盖。

## 输入冻结

运行前冻结 423 个源码/配置输入，共 4,715,113 字节，覆盖完整中央 Python/SQL、全部中央 scripts/tests、live runner、桌面/auth/Pi 代码及相关测试与项目依赖声明。是原 219 输入清单的严格超集；未读取真实认证或密钥。运行中和结束后逐 SHA 核对，**423 个均一致**。

- input-manifest SHA-256：`6bfc0f19c59b4a26c1e649c824d6518ad9fe9e5cf8c0f85e6762abd948f83933`。
- input-source-snapshot.tar.gz SHA-256：`d25a547af5061b5ca44b31c44390b3833afe09d668256ae5aad8e6267cc6929b`。
- 证据目录：`/private/tmp/easel-m3-final-central-20261028/`。
- 同步保存 Python/依赖版本与真实 ffmpeg/ffprobe 二进制 SHA。

## 命令与环境

```text
rtk proxy env EASEL_CENTRAL_TEST_DSN_FILE=/private/tmp/easel-media-review-20261027-second/test-pg-private.json services/platform/.venv/bin/python -m pytest services/platform/tests -q --tb=short -rs --junitxml=/private/tmp/easel-m3-final-central-20261028/full-central.junit.xml
```

使用既有 literal loopback PostgreSQL `127.0.0.1:50958`、受保护文件提供的测试 DSN、每项独立随机 schema。DSN 内容和凭据未写入本报告。没有创建 Docker，没有 SSH、部署、生产修改或真实供应商请求。

## 失败类别

| 类型 | 首行错误 | 数量 |
|---|---|---|
| failure | `platform_service.errors.PlatformError: execution_lease_expired` | 30 |
| failure | `platform_service.errors.PlatformError: media_lease_expired` | 26 |
| failure | `platform_service.errors.PlatformError: console_authentication_stale` | 12 |
| failure | `platform_service.errors.PlatformError: media_execution_failed` | 7 |
| error | `failed on setup with "platform_service.errors.PlatformError: execution_lease_expired"` | 6 |
| failure | `AssertionError: assert ('released' == 'pending_reconciliation'` | 4 |
| failure | `assert (0 == 1)` | 3 |
| failure | `platform_service.errors.PlatformError: media_asset_unavailable` | 2 |
| failure | `AssertionError: assert ('0' is None)` | 2 |
| error | `failed on setup with "platform_service.errors.PlatformError: console_authentication_stale"` | 1 |
| failure | `platform_service.errors.PlatformError: task_token_invalid` | 1 |
| failure | `TypeError: unsupported operand type(s) for +: 'NoneType' and 'datetime.timedelta'` | 1 |
| failure | `assert 0 == 1` | 1 |
| failure | `AssertionError: assert ('0' == '1'` | 1 |

全部 97 条失败/错误的精确 nodeid 和安全错误首行见同名 `.safe.json`；原 traceback 保留在受控证据目录的 `full-central.log` 和 `full-central.junit.xml`。主要表现集中在 execution/media lease 过期及 console authentication stale，但本次尚未证明共同根因。

终态后只读时钟采样显示 PostgreSQL 比宿主 UTC 中点快 **0.003007 秒**，采样往返约 **0.0181 秒**。这只能证明采样当时接近，不能排除运行期间时钟变化，也不能据此把这些失败标记为环境问题或忽略。

## 七项明确跳过（不计通过）

- `services/platform/tests/test_encrypted_restore_drill.py::test_actual_synthetic_exported_snapshot_dump_and_fresh_restore`：explicit synthetic Docker environment required。
- `services/platform/tests/test_encrypted_restore_drill.py::test_public_r2_fresh_restore_keeps_raw_schema_and_all_constraint_attributes[public]`：explicit synthetic Docker environment required。
- `services/platform/tests/test_encrypted_restore_drill.py::test_public_r2_fresh_restore_keeps_raw_schema_and_all_constraint_attributes[nonpublic]`：explicit synthetic Docker environment required。
- `services/platform/tests/test_media_restore_drill.py::test_full_016_owned_registry_ciphertext_nonempty_fresh_restore`：explicit owned synthetic Docker environment required。
- `services/platform/tests/test_mixed_media_restore_drill.py::test_two_pngs_plus_erofs_pending_hold_and_absence_survive_fresh_cipher_restore`：explicit owned synthetic restore environment required。
- `services/platform/tests/test_settled_media_restore_drill.py::test_actual_nonpublic_window_resolves_immutable_storage_function_in_scope`：explicit owned synthetic source handle required。
- `services/platform/tests/test_settled_media_restore_drill.py::test_two_settled_pngs_typed_receipts_wallet_and_raw_inventory_survive_fresh_cipher_restore`：explicit owned synthetic restore environment required。

## 后续边界

保留本次失败和冻结输入，交由根任务定位具体根因后决定修复与新验证。原 229、243、247 收据及各自冻结包均不覆盖。本次不能作为最终发布通过证据；017/019 未部署，缺真实渠道/费率的能力继续 closed。
