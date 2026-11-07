# 媒体备份 B1：本地文件归档与恢复

B1 提供 import-only 的 Python 文件处理模块，不接生产路由、数据库或备份定时器。它验证受信捕获计划与实际 dump／配置／媒体字节是否构成完整归档，并把归档恢复到全新的私有目录。实际捕获窗口、认证加密、数据库执行／完整恢复和调度接入属于后续 B2／B3。

代码在 `services/platform/scripts/media_backup/`。运行模块只依赖 Python 标准库；本轮使用 Python 3.12 在 macOS 本地验证。测试另需现有 pytest，不代表已验证 Linux、其他 Python 版本或生产环境。

## 调用边界

调用者提供完整、可信的 normalized plan、已打开的七项固定文件 FD、私有平铺媒体目录 FD、原媒体目录 owner 和有限 IO stream。JSON 不接受宿主路径。调用者须在整个操作中排除并发源／目标变动，并保证 IO 有界、不会无限阻塞；B1 的前后检查用于发现观察到的变化，不代替快照、GC 屏障、跨库事务或加密认证。

计划中绑定中央与身份 snapshot 标识、source/schema SHA、完整 registry SHA、每个文件的大小／SHA／原始 UID/GID/mode，以及每个 blob 的 scope／operation 关联。完整计划是受信捕获器的责任；B1 不会因输入自报一个 digest 就证明真实数据库已捕获。恢复时仍必须传入同一份受信 expected_plan，不能仅信归档内的 manifest。

固定逻辑文件名为：

- `db/central.dump`、`db/identity.dump`
- `db/central.roles`、`db/identity.roles`
- `config/central`、`config/identity`、`config/media`

这些名称定义归档成员，不要求文件内容为 JSON，也不会执行 SQL、配置或归档内容。blob 成员为 `media/<32或64位小写hex key>`。租户／产品边界由计划的 references 与 registry 绑定，不按随机文件名前缀推导。

attached 必须存在；staged 若缺失，必须在同一受信计划中有精确 evidence SHA；deleted／purged 必须缺失，墓碑保留在 registry。未知文件、`.part`、孤儿、额外缺失、GC 状态或不匹配 scope 均拒绝。不会补占位文件、复活删除项或修改余额、预留、actual、有效期。

## API

```python
from media_backup.contract import Limits
from media_backup.closure import write_archive, restore_archive

# plan/media_fd/fixed_fds/restore_parent_fd 已由调用者提供；不从请求取路径。
# archive 是调用者独占创建的 0600 regular file，以 w+b 打开。
written = write_archive(
    plan, media_fd, fixed_fds, archive,
    owner=(source_uid, source_gid), limits=Limits(),
)
archive.flush()
os.fsync(archive.fileno())
archive.seek(0)
restored = restore_archive(
    archive, restore_parent_fd, "restore-001", plan, limits=Limits(),
)
```

上例的 `os` 由调用脚本 `import os`。模块路径为 `services/platform/scripts`，可由现有应用入口加入 `sys.path` 后导入。没有新增可在服务器直接执行的生产 CLI。

调用方继续持有传入的目录／固定文件 FD；模块不会关闭它们，固定文件使用 pread，不改变其读取位置。归档 sink 也由调用者持有；B1 不宣称 sink 已 durable 落盘或已经加密。写入失败可能留下未完成 sink；只以成功 receipt 为完成，不自动重跑或发布部分文件。

恢复 parent 必须为当前 UID/GID 所有的 0700 目录 FD，目标 basename 必须全新。模块以目录 FD 创建固定子目录和 0600 普通文件，重读落盘文件验证 SHA、fsync 并检查集合／元数据；不覆盖已存在目标，不根据 manifest 任意 chown。源 owner／mode 保留在 manifest，实际角色映射留到受信 B2/B3 恢复计划。失败的私有 partial stage 保留，不自动删除。

错误统一为 `ClosureError`，code／消息 `invalid_closure`；没有账号、原路径、key、SQL 或响应正文。成功 receipt 字段为：`status`、`file_count`、`blob_count`、`archive_bytes`、`archive_sha256`、`manifest_sha256`、`registry_sha256`。状态只可能是 `closure_files_verified` 或 `private_stage_restored_verified`。

## 格式与资源限制

| 限制 | 默认最大值 |
| --- | --- |
| 单文件 | 4 GiB |
| 物理归档总长 | 32 GiB，含所有 header、manifest、padding 和尾部 |
| 数据成员 | 100,000，另加首个 manifest header；registry 行数也受限 |
| manifest | 32 MiB，registry 编码前另有逐行预算 |
| 单次 IO 块 | 1 MiB |

`Limits` 只允许收紧，整数 bool 不视为合法限制。上限是格式／拒绝边界，不是当前机器可承载全部上限的承诺；现有验证仅覆盖下述实测规模。调用者仍须核对库存、内存、磁盘多份副本及期限。

仅接受规范 USTAR 普通文件：原始 512 字节 header 先完整验证，manifest 必须第一项，成员名／顺序／尺寸／SHA 必须与 expected_plan 完全相同。拒绝 PAX、GNU、目录、链接、设备、prefix 扩展、坏 checksum、重复／额外项、非零对齐填充、截断和额外零／非零尾。两个强制终止零块后，只读取补齐 10240-byte record 所需的零 padding，并最多多读一个字节确认 EOF，不扫描无限零尾。

## 已验证与后续接入

在本地全新合成目录上，已有 33 MiB 单文件和 34 MiB 总量的实际归档写入、读取和文件恢复，最大 pread／archive read／archive write 均 1 MiB；Python tracemalloc 峰值约 3.2 MB，未测全部进程 RSS／内核页缓存。输入为稀疏零数据，但归档和恢复路径确实逐块写出并重读 SHA，不仅是虚拟 stream 或文件尺寸声明。

运行新模块的测试：

```text
rtk proxy .venv/bin/python -B -m pytest --noconftest services/platform/tests/test_media_backup_contract.py services/platform/tests/test_media_backup_ustar.py services/platform/tests/test_media_backup_files.py services/platform/tests/test_media_backup_closure.py services/platform/tests/test_media_backup_resources.py -q
```

既有 mixed DR、F1／F2、AV 载荷和桌面包源码不变。B2 接受控 write／GC 屏障及有界认证加密；B3 再做实际数据库／媒体 fresh restore、源后验和精确定时器部署。B1 的成功不改变那些门的状态，也不替换旧真实失败记录。
