# M1.1 审定源码回填到开发分支

本次只整合本机 `codex/ui-redesign` 分支。真实发布候选仍为独立冻结的 M1.1 size-v2 wheel，SHA-256 为 `f50e52511e941d3bea8e724eddbd68330e20eaa6339c5e2437b3880ca9705abf`；没有从包含 M2/M3 未完代码的工作树重新打包该发布候选。本次回填过程没有执行远程命令。

## 改动及保留边界

同批加入 11 项已审 M1.1 产品增量、017 迁移兼容去重、5 项测试以及本机验收 runner 的两个迁移清单，共 19 个文件。完整文件 SHA-256 和测试数量保存在同名 `-safe.json`，逐行补丁保存在 `assets/2026-10-27-m1_1-source-backport.patch`。

- `contracts.py` 合并 M1.1 的真实图片权益检查，同时保留 M2 的 `input_media` / `output_media` 资源约束及 `audio_clip`。`media_resource`、`media_resources_ready` 两个原函数逐字保持。
- `permissions.py` 合并只读的 `internal_feature_extensions` 以及误授 INSERT 的启动检查，保留原 M3 typed reference 权限，并去除重复的表名；原 INSERT / IMMUTABLE 权限集合均为新集合子集。
- 016 与冻结候选逐字相同。017 去掉重复建立 typed reference 基础表、列、触发器，以及已经由 016 提供的相同检查函数，保留全部声音注册证明、阶段及观测记录。`check_media_reconciliation` 删除前与 016 的函数逐字相同，SHA-256 为 `65d6355e2d8eb4cc7cd96b1322c2eae3211e7dee16da16a092321880610e0fcf`；`check_media_http_reference` 同样逐字相同，SHA-256 为 `5c2256080760ef9f6a8c03bc1eaaf2906e60af6ef0b7d48e76dae867b9e14e7e`。016 的 namespace 约束额外包含已审 New API 分支，因此整个建表前缀不能称为逐字相同。
- 原 001–016 迁移全部与冻结候选一致。共享工作树另有 017；本机 `verify_central_live_text.py` 的必需迁移清单因此为 001–017。发布候选的独立支持树仍按 001–016 验证，未被本次清单更改覆盖。
- `conftest.py` 原本自动发现迁移，不需要修改。014 升级测试的预期新增清单改为 015、016、017；没有删除原 M3 或备份恢复测试。

## 本机验证

使用专用合成 PostgreSQL 和随机隔离 schema，没有访问生产数据库或真实供应商。

1. 首轮混合回归：229 通过、1 失败，52.87 秒。失败为原有尚未交付的 M2 AV 音轨缺尾样本测试，未修改断言或伪称通过。
2. 本机 runner 在清单止于 015 时，`test_prepare_only_real_http_auth_and_zero_provider_calls` 实际失败；加入 016、017 后 runner 全部 16 项通过。
3. 最终定向回归：317 通过，66.75 秒，包括 M1.1 新增 29 项、M3 协议与真实 PG 58 项、M2 资源 21 项，以及图片、存储、账本、原权益、实际非 owner 角色和 runtime 权限验证。
4. M2 AV 已支持范围：16 通过、1 项明确 deselected，1.43 秒。排除项就是第一轮已实际复现失败的 `test_audio_track_declared_duration_cannot_hide_missing_tail_samples_under_valid_video`，并非供应商真测。
5. `git diff --check` 返回 0；回填补丁新增行的尾随空白检查通过；冻结 f50 wheel SHA 再次核对相同。

本报告不表示 M2 异步视频／音乐或 M3 产品集成已经完成，也不代替独立 f50 发布候选的 1322 项回归、真实服务器维护和媒体供应商验收。M2 音轨缺尾问题后续修复需另记证据。

后续该 M2 红测的修复与独立计数记录在 `2026-10-27-m2-av-duration-fix.md`；本报告保留回填当时观察到的失败及排除证据。
