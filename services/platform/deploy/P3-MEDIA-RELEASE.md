# P3 媒体候选版本上线清单

目标：生产从迁移 016 升到 020，媒体维护许可使用默认的本地模式（[决策 0006](../../../docs/decisions/0006-media-maintenance-optional.md)），然后逐个开放视频、音乐、语音模型。通用顺序见[部署手册第 8 节](../../../docs/handbook/2026-11-05/03-deployment.md)，本清单只补本次特有的检查。

## 已在隔离环境完成（无需生产权限）

- 从锁文件带哈希干净安装 wheel，`prepare_deployment.py` 通过。
- PostgreSQL 16 先迁到 016，再用候选版本自己的 `migrate` 升到 020，`grant-runtime` 通过；全程没有 `easel_backup_control`。
- 带媒体配置启动 API，媒体运行时以本地维护模式启动，`/health` 正常。
- 017–020 对生产已有表只有一处改动：018 给 `media_provider_associations` 加唯一约束 `(job_id, attempt_id, binding_hash, provider_scope_hash, provider_job_id)`。该表主键是 `(job_id, kind)`，只有 `kind='job'` 行的 `provider_job_id` 非空，其余为 NULL，因此已有数据不可能违反该约束。其余语句只新建表、函数、触发器，或修改 018 才新建的 `media_work`。

## 部署前只读预检（需要你授权连接 154）

1. **迁移校验和**：生产 `schema_migrations` 的 001–016 必须与候选文件逐个相同，否则 `migrate` 会以 `migration_checksum_mismatch` 拒绝。本地只能确认 016（`a5a1dcfd…`）与 10-27 发布记录一致。
   ```text
   生产：SELECT version, checksum FROM schema_migrations ORDER BY version;
   候选：shasum -a 256 services/platform/src/platform_service/migrations/0*.sql
   ```
   任何一行不同都先停下，查明生产当时的文件来源，不能改生产记录。
2. **媒体配置**：用候选 venv 以服务用户身份只读加载生产 `media.json`（`MediaSettings.from_env`），确认新版本接受原文件；`maintenance` 不写即为本地模式，`storage_root` 必须存在、为目录且不是符号链接。
3. **在途媒体任务**：`media_jobs` 中非终态数量为 0，或已有明确处置计划。
4. **备份**：P1 部署后至少一份新备份已同步到 R2，并通过一次恢复演练。

## 正式升级（需要你逐步确认）

按手册第 8 节：关闭新准入 → 停 API 写入 → 新备份收据 → owner 执行 `migrate`（只会追加 017–020）→ `grant-runtime` → 新 release 目录安装并切换 unit → 启动 → `/health` 与 `/v2/capabilities` → 本人登录，先用已上线的两款图片模型做一次真实扣点回归，再逐个开放新模型。

## 逐个开放新模型

每个模型（视频、音乐、语音）单独一次：发布定义 → 启用 → 一次真实调用走完“提交—轮询—下载—解码—计量—扣点” → 核对账本与产物 → 在 `docs/STATUS.md` 记一行。真实调用会产生供应商费用，需要你逐个确认。
