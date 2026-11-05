# 图片写入只读文件系统：独立因果验证

已定位并复现本次实际故障路径。冻结 f50 图片代码在取得并完整解码 PNG 后，先提交 `media_blobs(state=staged)`，再创建 `.part` 文件。若 API 的 systemd mount namespace 将存储路径设为只读，后一步报 `EROFS`，任务进入 `pending_reconciliation`，保留原预留且 actual=NULL。它没有伪造成功交付或自动再发付费请求。

root 提供的服务器只读证据为：实际任务已存在一条 staged blob（1024×1024 PNG，518,466 bytes）、图片目录997:997/0700但为空、`ProtectSystem=strict`、`ReadWritePaths`为空，以及实际 API PID 的 mountinfo `/` 为 ro。**本 reviewer 未执行 SSH**，这些来源事实由 root 采集；独立验证使用假供应商与实际隔离 PostgreSQL。

冻结轮子 SHA 为 `f50e52511e941d3bea8e724eddbd68330e20eaa6339c5e2437b3880ca9705abf`，三个相关冻结源码与当前工作树逐字一致：

- media/artifacts.py：`2e7dfa3e236227b617323ce538ea3444adbd8d7a986e22157aa44eb709cb4ad4`。
- media_runtime.py：`b96eb8619c0615d59e9b42f9028f081b9e8c612270b04c22779a51da0ec0dd57`。
- media/adapters/openai_images.py：`b1494b8d51b9e1814546ca342d0775175e1ac1e5b171afafcf9b163794d7da20`。

精确路径：`media_runtime.py:222` 的 callback 在完整 decoder 属性确认1024尺寸后调用 stage_blob；`media/artifacts.py:107` 在 before_write 返回后进入 `_put_verified`；`media/artifacts.py:124` 创建 `.part`，任何 OSError 转为503 `media_storage_unavailable`；`media_runtime.py:262` 将已 dispatched 且无 terminal evidence 的失败标记为 pending/upstream_uncertain。`check_capacity` 只检查目录元数据与磁盘余量，没有实际创建文件，因而宿主目录0700不能证明服务命名空间可写。

冻结 cutover drop-in 只设置 WorkingDirectory、ExecStart 与 MEDIA配置路径；原 unit 保留 ProtectSystem=strict，但没有增加图片目录写例外。这解释了宿主root检查权限正确、服务实际写入仍失败的差异。

独立测试 **2 passed，0 skipped，1.22秒，exit0**。唯一故障注入是对 `.part` 的 O_CREAT 抛出 errno.EROFS；两组都使用真实1024PNG解码、typed header解析和数据库事务：

| 证据 | 只读写入故障 | 可写对照 |
|---|---|---|
| HTTP引用/attempt | 1/1 | 1/1 |
| blob | 1 staged | 1 attached |
| 目录文件数 | 0 | 1 |
| terminal/receipt/artifact | 0/0/0 | 1/1/1 |
| actual/held/spent（微点） | NULL/1,000,000/0 | 1,000,000/0/1,000,000 |
| 包括原键重放后的假provider POST | 1 | 1 |

失败组重放原 operation key 不重新调用供应商；cancel 仍返回 pending，没有释放未知费用的预留。最初测试启动遇到同名 conftest 插件导入冲突，仅将冻结 fixture 原字节复制为独立模块名后重跑；没有改实现或弱化业务断言。

私有复现脚本：`/private/tmp/easel-f50-storage-ro-review-20261027/test_f50_storage_ro.py`；只连接原本机隔离 PG 的随机 schema，未打印 DSN。新 helper 与测试由子agent按 root 的固定最小权限方案另行准备，本报告不将其称为已部署修复。

建议最小修复为只对 API 的固定 `/var/lib/easel-central/media` 增加 `ReadWritePaths`，保留 ProtectSystem=strict、原env、console、f50与016。执行前须具体审核文件原SHA与备份、无活动任务窗口；执行后以实际 API MainPID mount namespace 和 API UID/GID 验证媒体路径 O_EXCL/0600/fsync/unlink 成功，并确认 `/etc` 与 release 仍不可写。仅在宿主检查 owner/mode不足以验收。

现有 pending 记录、staged预算和原预留应保留。文件系统恢复并不会自动补齐该笔结果、核账或允许重发付费 POST；必须另按原响应证据开展可信恢复。旧 DR 严格绑定 drop-in 原字节，权限变更后的恢复证明须显式更新新合同，不能复用旧 proof。

安全摘要 SHA256：`33caf242a9de3c3edc0015b2bef780d91d6b949ae4dcb1c3cf50dbf4674f33b0`。
