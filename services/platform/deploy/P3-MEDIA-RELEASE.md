# P3 媒体候选版本上线清单

目标：生产从迁移 016 升到 021，媒体维护许可使用默认的本地模式（[决策 0006](../../../docs/decisions/0006-media-maintenance-optional.md)），然后逐个开放视频、音乐、语音模型。通用顺序见[部署手册第 8 节](../../../docs/handbook/2026-11-05/03-deployment.md)，本清单只补本次特有的检查。

## 已在隔离环境完成（无需生产权限）

- 从锁文件带哈希干净安装 wheel，`prepare_deployment.py` 通过。
- PostgreSQL 16 先迁到 016，再用候选版本自己的 `migrate` 升到 021，`grant-runtime` 通过；全程没有 `easel_backup_control`。演练使用与生产相同的形态：独立 owner 角色拥有的 `workbench_*` schema。
- 021（[决策 0007](../../../docs/decisions/0007-media-writeoffs.md)）新增媒体平台承担：`media-writeoff-plan` 在演练库上走通 owner 校验并拒绝不存在的任务；runtime 对处置表无权限。
- 带媒体配置启动 API，媒体运行时以本地维护模式启动，`/health` 正常。
- 017–020 对生产已有表只有一处改动：018 给 `media_provider_associations` 加唯一约束 `(job_id, attempt_id, binding_hash, provider_scope_hash, provider_job_id)`。该表主键是 `(job_id, kind)`，只有 `kind='job'` 行的 `provider_job_id` 非空，其余为 NULL，因此已有数据不可能违反该约束。其余语句只新建表、函数、触发器，或修改 018 才新建的 `media_work`。021 放宽 `media_jobs` 的状态约束（加 `waived`）并加一个只约束 `waived` 行的检查，已有数据都满足；其余是新表和替换的检查函数。

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

### 预检结果（2026-11-06，只读）

| 项 | 结果 |
|---|---|
| 迁移校验和 | 生产 16 行，001–016 与候选逐个相同，无多余版本 |
| 媒体配置 | `/etc/easel-central/media.json`（0600，属主 easel-central）；候选与生产代码对去掉密钥后的同一配置算出的两款图片模型 `binding_hash` 完全一致（flare `599e27e6…`、sunburst `da985099…`），已发布的模型定义不受影响；未写 `maintenance`，即本地模式；`storage_root` 存在、是目录、非符号链接 |
| 在途媒体任务 | 共 3 笔：2 笔 settled；1 笔 `81a6155d…` 为 `pending_reconciliation / unknown / processing`，`upstream_uncertain`，预留 1,000,000 点未结。即 10-27 媒体目录只读事故那笔（[交付记录](../../../docs/validation/2026-10-27-newapi-image-workbench-delivery.md) 第 50–54 行）：上游已生成，但写盘失败、用户未收到。升级不碰它。你已决定释放预留：控制台做不到，改由 021 的 `media-writeoff-*` 在升级后处置（见下文） |
| 备份 | P1 已上线；11-06 最新备份已在 R2 并通过恢复演练（账本核对通过） |

## 正式升级（需要你逐步确认）

按手册第 8 节：关闭新准入 → 停 API 写入 → 新备份收据 → owner 执行 `migrate`（只会追加 017–021）→ `grant-runtime` → 新 release 目录安装并切换 unit → 启动 → `/health` 与 `/v2/capabilities` → 本人登录，先用已上线的两款图片模型做一次真实扣点回归，再逐个开放新模型。

### 生产升级记录（2026-11-06）

- 停服 02:07:53–02:10:42Z（约 3 分钟）；停服后先做备份 `20261106T020708Z-19fe2e34…` 并同步到 R2。
- owner 执行 `migrate`，追加 017–021；`grant-runtime` 通过；Console 角色的权限清单与升级前相同，未重授。
- 新 release `/srv/easel/releases/20261106-media-021-v1`（wheel `49e25f31…`，依赖带哈希安装）；两个 unit 通过 drop-in `91-p3-media.conf` 切换，原 `90-m1-images.conf`（媒体配置与媒体目录写入例外）保留。
- 启动后两个服务 `/health` 正常，公网 200，`/v2/capabilities` 显示媒体执行在线，付款仍关闭；巡检 `ok`。
- 账务快照（钱包、额度批次、预留、冻结、凭证、分录、文本与媒体任务、存储预算等 13 张表）在停服时、迁移后、启动后三次完全一致。
- 回滚：删除两个 unit 的 `91-p3-media.conf`，`daemon-reload` 后重启，即回到 `20261027-images-m1-1-size-v2`。`serve` 不校验迁移集合，旧版本可以在 021 schema 上运行。
- 环境变量注意：`central.env` 按 systemd 规则在双引号内转义 `\"`。手工加载时必须同样反转义，否则 `CENTRAL_OIDC_CLIENTS` 会被配置校验拒绝（首次 `migrate` 即因此被拒，未写库）。

## 处置 10-27 待核算任务（升级后、开放新模型前）

**已完成（2026-11-06）**：计划哈希 `03baae0e…6ef0` 经用户确认后执行，操作 `bad9d916-5268-4bf0-922f-164b3d9da960`；任务 `waived / platform_absorbed / unavailable`，`actual` 为空，无 receipt，预留 1,000,000 点释放，存储预算释放；`media-writeoff-operation` 返回同一结果。注意 `--plan` 要写绝对路径，因为主机入口在 `/` 下运行。

用 owner 身份在服务器上运行，命令形状见[核算部署文档](../../../docs/platform/reconciliation-r2-deployment.md)的“媒体任务”一节：

1. `media-writeoff-plan --job 81a6155d-c7dd-4b98-bd3f-5e909a4d3ba9 --reason … --approval-reference … --operator …`，输出写入 root 私有目录（0600）。
2. 你审核计划：1 项、金额 1000000、`subject=media`，记下 `plan_sha256`。
3. `media-writeoff-apply --plan … --reviewed-plan-hash <你确认的哈希> --key <一次性幂等键>`。
4. 只读核对：该任务 `waived`、钱包待核算 0、预留释放 1,000,000 点；`media-writeoff-operation --key …` 返回同一结果。

## 逐个开放新模型

每个模型（视频、音乐、语音）单独一次：发布定义 → 启用 → 一次真实调用走完“提交—轮询—下载—解码—计量—扣点” → 核对账本与产物 → 在 `docs/STATUS.md` 记一行。真实调用会产生供应商费用，需要你逐个确认。
