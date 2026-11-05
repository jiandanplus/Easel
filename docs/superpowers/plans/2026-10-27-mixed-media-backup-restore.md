# 两笔成品与一笔原待核算的有界恢复

本计划只新增独立 mixed-window 演练工具。旧 `49df…` 已结算工具、`68dccf…` 初始空媒体工具、`af190…` wrapper、v6 核心和原备份 timer 全部保留。**本机实现与合成回环已通过，尚未上传或执行 154 新演练。** 服务器的新模型 disabled 窗口、nonce、私有计划及 apply 由 root 后续逐项审核决定；本工具不会关闭模型或处理资金。

## 本次状态合同

冻结代码仍为 f50e size-v2、001–016，不使用共享源码后续的 017 或 M2 工作内容。最新 API `90-m1-images.conf` 只比旧版增加 `ReadWritePaths=/var/lib/easel-central/media`，固定 SHA 为 `527880b3f48ee644fcd71cf6003b8be8fc8c89f1fad8c26ff7cfa6d2feb5976c`。原 main unit、console drop-in、中央和身份配置、媒体配置一共 11 个文件逐字连同实际 UID/GID/mode 捕获、加密和还原；不为捕获修改源文件。

三笔历史精确限定为：

| 任务 | 模型 | 保留状态 |
| --- | --- | --- |
| `9ccc8430-3c29-46cc-9d5d-961be08f6acc` | Sunburst | settled/completed/available，actual 1,000,000，一张 attached PNG |
| `a934ccdf-d78d-446c-95ec-7b8fd028a2fa` | Flare | settled/completed/available，actual 1,000,000，一张 attached PNG |
| `81a6155d-c7dd-4b98-bd3f-5e909a4d3ba9` | Flare | pending_reconciliation/unknown/processing，actual NULL，预留 1,000,000，原 pin 和未释放预算 |

待核算任务已经存在一次 attempt、一个 HTTP200 typed reference 和一个 staged blob 登记，但没有 terminal、receipt、artifact 或物理文件。完整 job/attempt/reference/binding、HTTP association、reservation、invocation、budget、staged 行和真实 storage key 仅保存在服务器私有 plan/归档中。safe 只含这些对象的摘要与计数。没有免单、重放生成、释放预留、修补用量或把缺文件标为成品的步骤。

## 准入与捕获

1. 固定两个模型都 disabled；3 invocations/jobs/attempts/HTTP references/HTTP associations，2 artifacts/receipts/terminal evidence，3 blobs/budgets，1 owner，0 assets，0 reserved/dispatched，0 GC token/lease。只有已知一笔 pending 及其 staged pin 能命中。
2. 待核算的三元关系逐项验证：job/attempt/binding、provider scope、HTTP200 association 与 typed namespace/id/body SHA；reservation status/actual NULL/amount，invocation operation1/held1m/spent0；预算 max20MiB/max_files1、remaining_files0、remaining_bytes=max-size、released_at NULL；staged 的原 owner/scope/invocation/job/attempt 一致，attached_id/purged/deleted/GC 都 NULL。
3. 两笔成功任务保持每笔 1 点、1024² PNG、原 typed evidence 和已释放预算；不改变原 TTL。三个 blob 的 expires_at 在每次窗口查询时距当前至少 30 分钟，为 15 分钟有界演练留出余量。
4. 唯一 missing-staged allowlist 精确绑定旧 pending 的 storage key、SHA、size、job、attempt、binding、typed reference 摘要和完整 staged 行摘要。目录必须恰好只有两张 attached 原图；允许的 staged key 必须不存在。任何额外 missing、出现的 staged 文件、`.part`、未知文件、符号链接、硬链接、GC 或额外任务都拒绝。
5. 用 dirfd/O_NOFOLLOW/O_NONBLOCK、原 UID/GID 997、文件0600/目录0700、单链接、inode/mtime/ctime/size/SHA 与目录前后签名验证两图。capture 前、同一中央 exported snapshot 中、dump 后、最终结束前都检查 registry/完整 mixed-window 摘要与原文件。不是按最新文件推测来源。
6. 源 f50 wheel 及 76 个 Python/SQL、16 migrations、私有 central.env 的完整配置与 namespace 双 SHA 全绑定。两真实 unit 的 MainPID/cwd/argv/exe/MEDIA 要与新 release 一致；读取该 PID 的 mount namespace 和 mountinfo，API 媒体路径有效 mount 必须 rw，console 媒体路径和两者 /etc、冻结 release 必须 ro。namespace/mountinfo 只输出 SHA，过程中 PID/namespace 变化拒绝。

## 恢复与证明

- 新 nonce、新 workdir `/root/easel-mixed-media-restore-drill-20261027-<nonce>`、新密文目录 `/var/backups/easel-encrypted-mixed-media-drill`，不重用旧 nonce、归档、intent 或目标。
- 总载荷仍限制为 32MiB。两源库分别只读 exported snapshot，不宣称跨库原子；roles/sequence 前后稳定性和所有原 raw schema/完整约束/data/roles/owner/ACL/DB metadata/extension/version/sequence/财务库存严比不变。
- 新密文必须落盘后重新读取并认证。仅在新隔离目标执行原 v6 精确七 CHECK 修复；不写源、不修改原 schema 指纹、不忽略未理解的 SQL 差异。
- 目标严格顺序运行，每个 512MiB memory/swap、0.75CPU、128PIDs、network none、0 published port、禁 SQL 错误内容日志。成功失败 guard 后停止保留容器和卷，不自动清理或启动恢复应用。
- 两张 PNG 原字节与 11 个配置的 UID/GID/mode 要实际恢复；已知 staged key 在恢复目录仍精确不存在，不生成占位文件。目标原三笔、actual NULL、1 点预留、pin、未释放预算和 typed 证据必须与私有计划及同快照 registry 一致。
- success/proof 只有双库全部严比、新16迁移、两图 byteexact、原缺失状态、目标 mixed-window 查询与全部源后验通过才生成。safe 明确 `pending_actual_null_preserved=true`、`pending_hold_preserved=1000000`、`missing_staged_count=1`，这表示保留异常，不表示已经解决异常。
- 原 service/PG/Logto/nginx/tunnel 和原 timer 定义必须不变。新工具不执行 disable/enable、付款、发点、延期、核算或供应商请求。

## 本机验证与后续审查

- [x] 首轮49项：48红/1旧源冻结保护绿；完成后49绿。
- [x] 使用只有两笔 settled 的旧夹具跑实际 PG 先严格拒绝，原对象 finally stop 保留。
- [x] 新夹具固定 f50 stage，以真 runtime/事务/PNG 解码、MockTransport 和仅 `.part` O_CREAT 抛 errno.EROFS 得到真实 pending；不靠 SQL 拼造终态或余额。
- [x] source snapshot → 两图/三登记 → 新密文 durable read/auth → fresh PG/文件/11合成配置回环，完整75表529约束95行严比、缺失仍缺失、钱包 spent2m/held1m；坏密文和准入负例拒绝。
- [x] 新 wrapper 使用调用 venv、完整计划 root0600/O_EXCL/fsync、nonce-only argv 和显式 safe 白名单；不外发 pending对象/真实key/config/DB/user/roles/SQL。
- [x] 新 namespace 判定先红后绿；最新 API90 内容合同、原源冻结、proof 不能缺 pending/hold/absence 等证据均验证。
- [x] 本机完整 **76 passed / 0 skipped / 30.39秒**，无 SSH、真实供应商请求或生产写入。
- [ ] 由独立 reviewer 核对 freeze、全部负例和 fresh 合成回环；实际 154 尚未执行。
- [ ] root 再次核对真实三笔状态和最新配置，决定显式 disabled/idle 窗口与全新 nonce，只读 wrapper 生成私有 plan 后审具体 SHA/资源/源IDs/目标不存在。
- [ ] root 单独批准 exact apply 后才执行真实双库演练和独立后验；如失败只观察原 handle/保存 safe，不重跑、放宽或改源。
- [ ] root 按受审模型操作恢复可用状态。日常媒体 timer、GC/partial quarantine、更大容量/PITR、旧 pending 费用处理分别另审。

本机环境实际是 Docker Desktop 8CPU/约8.3GB engine 内存；运行前宿主磁盘约3.96GB空闲。夹具源与目标各限制512MiB，目标无网；本机只证明合成 central-only 回环，不能冒充真实154双库或现有两PNG的恢复证明。没有删除任何旧对象以释放空间。
