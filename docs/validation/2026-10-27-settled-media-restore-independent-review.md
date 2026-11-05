# 已结算图片恢复演练：独立只读审查

结论：在本次冻结的本机候选范围内，没有剩余已知阻塞。独立运行 **46 passed、1 skipped、0 failed，33.73 秒，exit 0**。源码与测试九份输入在运行前、运行后及报告生成时 SHA256 全等。实现者的既有测试次数没有与本轮相加。

本轮没有 SSH、生产 SQL、真实供应商 POST、服务器配置修改、源码修改或发布动作。f50 图片候选与旧初始空窗口 DR、旧计划输出 wrapper、R2 严格恢复核心保持不变。本结论允许继续审核新的服务器私有计划，不代表已经在服务器完成实际两库非空恢复。

## 冻结对象

| 对象 | SHA256 |
|---|---|
| verify_settled_media_restore_drill.py | `49df4b72db760f88022c0dd3e7b4d8e566463fb12ab1a6044a8a2b59bf7f857d` |
| capture_settled_media_restore_plan.py | `356667ad623b31739af2c6431312f7f5c250b9f40265edd79c453becab93b1a3` |
| test_settled_media_restore_drill.py | `fc919f41d67f0e563cc186e0b979ebd7b123ef98cca7536331fd57dd72ce1397` |
| test_settled_media_restore_plan_capture.py | `6b43e1605645b45148215d059ec588486f3cc8defd29a7bed714dbe15c8ef764` |
| settled_media_restore_fixture.py | `65ebab81664f1df36fda2443af637496917d5c6d804ea707a3a71155424ae51c` |
| test_settled_media_restore_fixture.py | `b62bacfd0d2f79db4d26a5f317b7534d58bb82037df2a6a7f5ded24ac6c9eb48` |
| 原 verify_media_restore_drill.py | `68dccf3699a3c9b06d79e5cf87609dcf567b2d6c93c8b3c6186cc40c3474e1d3` |
| 原 capture_media_restore_plan.py | `af1906bf0a0d012f3033407e4a41ac136b3b37c5746dd2891ebf29a6ba43bd2b` |
| 原 verify_encrypted_restore_drill.py | `4d3c4210469d6630dd4cbc71d8d5327e62aa972eedb7084685bb0df85a2c88ea` |

图片服务轮子继续固定为 `f50e52511e941d3bea8e724eddbd68330e20eaa6339c5e2437b3880ca9705abf`，fixture 在导入前核对全部 76 份 Python/SQL 与轮子逐字一致，含 001–016 共 16 个迁移。没有把当前开发树里的 M2/M3 包含在此候选证明中。

## 关键检查

- 新 DR 的 `settled_window`（287 行）和 `window_sql`（346 行）只接受固定两款模型各一笔、两张真实注册的 1024×1024 PNG、同一归属、每笔 1,000,000 微点、已结算且完成且可用、live funding、零输入素材、n=1。要求两个 invocation held=0、spent=1,000,000，两个存储预算 released 且剩余额度为零。数量、状态、未知字段或整数类型漂移均拒绝。
- 两套 terminal、receipt、HTTP association 与 typed reference 必须存在；无 body object ID 时保持 NULL，固定 `newapi_x_oneapi_request_id_v1`。数据库本身的外键、不可变约束、预算与账务守卫由冻结 001–016 保留。全量 inventory 比较涵盖行内容及账务平衡，未把成本未知伪造成供应商已知费用。
- 模型必须 disabled；拒绝 reserved、dispatched、pending_reconciliation、pinned blob、GC token/lease、额外素材/文件/预算。PNG artifact 与 blob 必须未清除且至少还有 30 分钟原 TTL。脚本没有延长 TTL 或改变钱包、会员、订单和点数。
- 数据库 capture（422 行）在导出快照前、快照内及之后核对 settled 窗口和 registry；实际 pg_dump 与 inventory 使用同一导出快照。文件使用 NOFOLLOW、受限目录/文件权限、单硬链接、长度/SHA、inode 与前后元数据验证；读取后重开复核，拒绝 .part、未知文件和目录漂移。
- 恢复流程（446 行）从已落盘密文重新解密，严格验证 manifest、schema、约束原定义/flags、roles/ACL、全部行/序列与资金平衡后，再在新目标查询同一 settled 窗口、registry 和迁移。`SET LOCAL search_path` 明确使用恢复 schema，避免 immutable storage 函数内部未限定表名在非 public schema 中解析错误。原 R2 核心和比较器没有放宽；七条旧 CHECK 仅沿原精确模板规则修复自有隔离目标。
- 全部 11 份原配置、media.json、两份主 unit 与两份 drop-in 的 bytes/SHA/UID/GID/mode 加密保存并在私有恢复树复核；不自动启用恢复配置。来源仍检查实际 MainPID 的 cwd/argv/exe 与有效 MEDIA 路径，避免只凭 systemd 属性把旧进程当新 release。
- 最终成功前再次检查当前来源窗口、registry、文件、配置、实际进程、迁移、原服务/容器与旧备份定义。非空 proof 仅在实际两个文件、目标 settled/typed 查询与两库 matched 合同全部满足时生成。
- 新计划 wrapper 仅调用固定 DR 的 `--nonce`，沿当前已选 venv 的 sys.executable；主入口仍要求固定 root/host/文件 SHA。完整私有计划由 0700 目录下 0600 O_EXCL/NOFOLLOW 文件与 fsync 保存。外发显式白名单；数据库名、用户、role、DSN、配置内容/字典 key、完整 argv/environ 和子程序诊断不输出。旧空窗口计划不能重命名为新 settled 证明。嵌套哨兵测试通过。

## 独立实际运行

先只读核对磁盘约 5.18 GB 可用、Docker 健康（8.32 GB 内存、8 CPU），再使用新的随机 nonce。未使用或启动旧的稳定 handle，未改变原应用容器。

```text
rtk proxy env PYTHONDONTWRITEBYTECODE=1 EASEL_SETTLED_RESTORE_DOCKER=1 \
  EASEL_SETTLED_RESTORE_EVIDENCE=/private/tmp/easel-settled-media-independent-review-20261027/synthetic-settled-nonempty-independent-safe.json \
  services/platform/.venv/bin/python -m pytest --noconftest \
  services/platform/tests/test_settled_media_restore_drill.py \
  services/platform/tests/test_settled_media_restore_plan_capture.py \
  services/platform/tests/test_settled_media_restore_fixture.py \
  -q --tb=short -p no:cacheprovider
```

实际执行由私有 runner 清除旧 handle 与外部 DSN 环境变量、核验输入哈希、记录日志，并在退出后核验自有容器状态。唯一跳过项要求显式已存在的合成 handle；本轮刻意未用旧 handle。相同非 public `window_sql` 已在完整回环的新来源与新目标实际执行，不是只检查 SQL 字符串。

真实合成回环结果：75 张表、529 项约束、82 行数据；两张 1024×1024 PNG 共 **2,101,644 bytes**；两个 typed reference/terminal/receipt，object ID 保持 NULL；钱包 spent=2,000,000、held=0、available=8,000,000；两个输出预算已释放。RSA/AES 加密归档 **2,704,203 bytes** 落盘后重新读取，恢复到全新无网络 PostgreSQL 目标，完整数据 inventory 相等；两张图与 11 份私有配置复核；篡改密文被拒绝。

本轮创建两个合成 source 和一个 restore 目标，均 512 MiB 资源限制；source 仅随机 loopback 端口，restore 无网络、无发布端口。三个容器均已停止、无 OOM、保留容器与数据卷，不删除审计对象。

## 证据与限制

- 私有日志/runner/输入哈希：`/private/tmp/easel-settled-media-independent-review-20261027/`。
- 本轮实际恢复的是**合成中央数据库**，包含中央身份映射表，但不等于已在本机恢复真实 Logto 账号库。服务器正式入口仍要求 central、identity 两个 matched 结果；本次未执行该服务器入口。
- fixture 使用 live 语义与 MockTransport 的假供应商，两次合成 POST，不含真实业务 Key、真实供应商费用或实际客户内容。真实图片/扣点的 M5 与服务器新 nonce 两库回环应各自留证。
- 本机配置恢复使用本机 UID/GID；服务器 root/997 的权限、实际进程与路径由正式入口门槛验证，本机未冒充该环境。
- 此工具是有界、固定两笔、模型关闭状态的演练；不是跨数据库原子备份、持续媒体备份或 PITR。30 分钟 TTL 或精确两笔窗口不满足会闭锁，必须重新审核新的范围，不应删记录、延长 TTL 或绕过约束凑数。

安全摘要 SHA256：`e4450eeb0513280077c75b60d6cb562b90866d29a8f9b5669a9207b13b48759f`。
