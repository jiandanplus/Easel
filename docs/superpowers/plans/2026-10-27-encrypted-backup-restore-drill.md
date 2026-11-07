# 加密运行备份 v2 与隔离恢复演练计划

状态：v6冻结R2已通过真实双库fresh restore及独立后置核对，原schema／全部约束metadata／数据／角色／ACL／序列及资金恒等式严格匹配。safe回执已归档；此前失败密文／intent／隔离目标及卷继续保留，不重apply旧nonce。原生产和原备份timer未修改。

## 执行目标和固定边界

- 唯一目标154.217.245.179，SSH别名easel-zhiyin，严格校验现有host key。
- 当前唯一中央release为`/srv/easel/releases/20261026-reconciliation-r2-v1`。
- 固定两个源：`easel-central-pg-20261024/easel_workbench_preview`、`easel-identity-pg-v2-20261024/easel_logto_preview`，只读；不停止源应用或writer。
- 继续使用固定镜像`postgres@sha256:9e2cf6287f25c3151f4c9421ccce2b17f3e60061c111a29165e0d2cdd9ff0a97`，不拉取其他镜像。
- 复用已冻结备份源码d330bccf0c71f9c4d808411ba48d87c217048dde0763023e4e9ca0cb45bb14a1及加密helper 7dd81fbc97d60c782fe2d2c43d81e37fc0b4ea926504e66309a43abc144ad7d6。仅两份不含真实配置或密钥的代码已复制到本机供审阅；私钥／配置／DSN／归档明文不离开154。

## 新文件与接口

工具：`services/platform/scripts/verify_encrypted_restore_drill.py`。

审阅SHA256：`cd463029cfb7463705031f086f49c0f62ad71576838fad56f64d91bbd3c5872b`。

`--nonce <32位小写hex>`只生成计划和计划SHA，不解密、不创建恢复容器。

独立审阅后仅使用`--nonce <同值> --apply --reviewed-plan-sha256 <精确计划SHA>`执行。计划包含工具自身SHA、源容器完整ID／image ID、release、目标名和资源限制；任何变化使旧计划失效。

- 新root私有目录：`/root/easel-restore-drill-20261027-<nonce>`，0700；意图及安全回执0600且O_EXCL，不复用旧nonce。
- 新密文目录：`/var/backups/easel-encrypted-v2-drill`，0700；文件0600、O_EXCL、落盘后重新读取并认证解密。
- 目标名称与卷包含完整nonce；无现有名称复用、无删除动作。

## 同快照采集

1. 逐库打开`REPEATABLE READ READ ONLY`源事务并导出snapshot。数据inventory和完整custom `pg_dump --snapshot`使用同一个导出快照；完成后ROLLBACK。
2. 每条读取statement_timeout30秒、lock_timeout1秒，导出会话idle_in_transaction180秒；pg_dump锁等待1秒，不对生产执行DML／DDL／冻结／停写。
3. 读取所有非系统schema的持久表／分区表／物化视图行数、字节数和全行哈希；最多100000行／128MiB。完整schema-only dump摘要覆盖DDL、函数、触发器、RLS、owner、ACL和default privileges。
4. 序列非MVCC，因此读取前后state必须严格一致；角色SQL前后摘要也须一致。只规范化PG随机restrict令牌，不丢弃owner／ACL／版本／grantor。
5. 记录完整roles属性（敏感值仅进入加密归档）、memberships及grantor、数据库owner／encoding／locale／ACL／settings，以及extension name／version／owner／schema／配置表映射。大对象和foreign table尚未实现时拒绝，不能无声漏比。
6. 核对所有credit lots守恒及journal平衡，源和恢复完全匹配。
7. 两库各自有独立快照与开始／结束时间；不宣称跨库原子、PITR或整机灾备。

## 加密和恢复

- 用原服务器私钥对应公钥加密，沿用认证加密及长度／哈希／成员完整验证，不产生明文文件。
- 从已落盘、重新读取且认证通过的新密文中恢复，不能仅从原内存dump恢复来冒充验证磁盘备份。
- 每次只运行一个恢复容器：network=none、不绑定端口、512MiB内存且禁swap、0.75CPU、128PID、restart=no；只挂载本次独立持久卷。
- PostgreSQL仅Unix socket，`log_statement=none`、`log_min_error_statement=panic`、`log_min_messages=panic`。Cmd与资源／网络／卷／labels精确inspect；源角色或数据库配置若会覆盖日志安全设置则拒绝。
- initdb使用原bootstrap角色名称以保留内置成员关系grantor；只跳过该角色已存在的一行CREATE，全部ALTER属性和原口令哈希恢复。其他角色、owner／ACL均完整恢复，禁止`--no-owner/--no-acl`。
- `pg_restore --exit-on-error --single-transaction`完成后，从目标独立读取全部inventory，逐字段严格比对，不只比表计数。
- 每库完成后验证stop且状态exited；保留容器、卷、新密文和意图／回执。失败或中断只停止通过完整nonce／镜像／资源／卷guard的本次容器，不删除、重置或修改生产。
- 执行前后核对原4项systemd服务PID／启动时间、三个生产容器ID／镜像／启动时间以及原备份service／timer／源码摘要保持。预计新增磁盘小于1GiB；前提空闲磁盘≥1GiB、MemAvailable≥1GiB，低于门槛拒绝。

## 验证

- [x] 纯失败测试先运行，再实现；reviewed plan CAS、SQL quotes、镜像／资源／网络／卷guard、schema令牌、归档成员、序列及inventory严格匹配、owner／ACL参数、失败时只停自有容器。
- [x] 本机固定PG16真实合成库：enum、serial序列、文档表、两张资金表、原角色、ACL、default privileges和数据库settings；同快照dump→新容器恢复后完整匹配。
- [x] 合成非法ALTER ROLE带独立合成标记，确认目标Docker stdout和stderr日志均不含该标记，随后inventory保持；所有本次合成容器停止且保留卷。
- [x] `EASEL_RESTORE_DRILL_DOCKER=1 .venv/bin/python -m pytest --noconftest services/platform/tests/test_encrypted_restore_drill.py -q --tb=short`：16 passed。
- [x] py_compile与git diff --check通过。
- [x] 根任务及独立审查员审阅冻结源码与原backup/crypto依赖。
- [x] 上传仅工具代码；只读plan，复核计划SHA和资源。
- [x] 在154执行唯一受控apply并保留失败；新密文已生成，中央恢复未验证，未产生restore-safe。只取回安全现场／诊断字段，没有取回归档、inventory或配置。
- [ ] 独立核对目标停止、卷保留、生产服务／容器／原备份定义保持及新密文权限／哈希，出具当前时点验收报告。

原定时备份程序和timer保持原样；本演练的新v2不自动替换生产备份。异地备份、私钥异地恢复、整机reboot和HA仍是独立门槛。


## 历史：后续修订及当时待执行状态

原已审工具SHA `cd463029cfb7463705031f086f49c0f62ad71576838fad56f64d91bbd3c5872b`保留在154原review目录。其唯一nonce `083b087376364b99961b3126c364bce9`已在受控unit执行，失败状态和数据不覆盖。静态阶段定义与目标角色存在性诊断均只读，不重试恢复。

本机工作树后续新增最终PostgreSQL进程就绪闸（PID1 comm=postgres且pg_isready）、固定白名单phase journal与失败安全回执，避免把entrypoint临时PG误判为最终服务，并让后续失败可精确定位。合成坏dump路径验证事务回滚、阶段停在pg_restore，18项测试通过。当时这些修订尚不能证明已修复真实根因，未覆盖原远端冻结代码；后续已遵循根因确认、独立审阅及全新nonce／计划执行，最新结果见下方v6验收。


## 当前v6／R2真实验收结果（2026-10-27）

The original raw schema fingerprint is unchanged. Public R2 migrations reproduce seven nested-AND CHECK differences after a SQL dump/reparse. The repair reconstructs exactly those original BETWEEN definitions only after all seven names, definitions and attributes match the fixed allowlist; one isolated-target transaction uses ONLY DROP RESTRICT and ADD. All source/target constraints metadata is now also captured in the same snapshot and strictly compared.

The real central database uses its configured private schema rather than public. No actual namespace, SQL or private definition was emitted: the source probe returned booleans only. V6 binds the known seven definitions to the explicit CENTRAL_DB_SCHEMA in the root-private central.env. It never chooses a schema by scanning. The plan binds both schema SHA256 and the entire central.env SHA256; apply re-plans, verifies captured config, and verifies the durable ciphertext's config before restoring. Any scope/config-byte change rejects. Plan format is v4, inventory version is 3, encrypted envelope/member format remains v2.

Frozen candidate SHA256: 4d3c4210469d6630dd4cbc71d8d5327e62aa972eedb7084685bb0df85a2c88ea. The local v5-to-v6 review diff has 147 lines. Final synthetic regression: 57 passed, exit 0, 70.68s; public and random nonpublic full fresh R2 schemas, 379 constraint metadata records including NOT VALID/NO INHERIT, opaque dollar function body, data/roles/ACL/sequence and raw schema digest all match. Compile and diff checks pass. This is local evidence, not real dual-database recovery acceptance.

根任务完成代码与只读plan审阅后精确批准nonce `1d69fabc6ee045b4a7538d056f4264c4`／plan SHA `f29c85d968aa7f750301adcfc910c4f69f907ff9711e06d1fbdc41df6119743c`，仅单次执行新unit，InvocationID `0da756d8888f42a8b0b051def35aa0d1`。02:28:39–02:31:30 UTC完成：中央57表711行、身份79表446行，完整严格恢复匹配。两新目标停止保留，源PG IDs／镜像、原四服务、Logto及原backup timer独立复核正常。safe回执SHA `0ac8db04128ff78f3de014693faf55cd77d47b2265b0e66bf4998012c2c42161`，见 `outputs/_run/operations-readiness-20261027/evidence/restore-v6-safe.json`。旧失败记录均保留，原backup timer未修改。该冻结R2验收不外推M1／016、媒体blob、整机、异地、PITR或性能范围。
