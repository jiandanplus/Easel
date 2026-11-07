# 原账号、钱包与管理员身份连续性

本手册对应 S5-A2。采用同一套 Logto 身份库的受控逻辑备份与恢复，保留用户 `id`（OIDC `sub`）、tenant 和应用 ID，在中央库追加新 issuer 的身份映射。不会按邮箱或用户名合并用户，也不支持把任意两个独立身份库的用户直接配对。

产品自定义接口不依赖这套迁移。迁移既不更换客户端接口来源，也不重发点数、创建钱包或开启支付。

## 权限与证据边界

三个命令仅供维护者在受保护的主机上运行。必须显式配置 `CENTRAL_MIGRATION_DATABASE_URL`，与 runtime DSN 指向同一主机、端口和数据库，且实际连接身份必须是目标 schema owner，不能通过 `SET ROLE` 冒用。常驻 API／Console 角色不能写入 proof、迁移批次、条目或管理员 alias；Console 只读取解析身份所需的 alias 列。

`identity-restore-proof` 从两套实际 PostgreSQL 库收集只读快照，核对备份 SHA256、schema、全部支持的表数据、序列状态、tenant 用户和所需应用 ID，再从中央库读取原身份、钱包和管理员候选。配置文件本身不是身份证明，命令不接受外部用户映射。`identity-migration-apply` 仅接受已保存 proof 的 ID、幂等 key 和审计原因。

信任根是维护者明确选择的源库、恢复库和受保护的策略／备份。这个机制不能防御恶意数据库 owner，也不能仅凭两库摘要证明整个备份来源链。正式迁移仍须保留实际备份恢复记录、冻结写入窗口，并由本人完成新 issuer 的真实登录验收。库比对、合成签名登录、公开 HTTPS 和本人登录是不同证据。

## 准备与支持范围

先在中央库执行新增 migration `010_identity_continuity.sql`，重新运行 `grant-runtime`、`grant-console` 并通过实际低权守卫。001–009 保持原内容和 SHA。不要通过应用启动自动迁移。

身份比对当前支持相同 PostgreSQL 16 版本、相同 schema 名的两个不同实际数据库，涵盖普通持久表、索引、约束、函数、触发器、普通视图、规则、RLS 策略及序列定义／状态。2026-10-24 增加同 schema 的原生 enum 和 PostgreSQL 自动生成 enum[]：比较完整标签相对顺序、逻辑名称及 owner／ACL，包括未使用和空 enum，不比较恢复时可能重分配的 OID 或排序浮点值。用户和应用表需包含文本类型的 `tenant_id` 与 `id`。分区／继承、外部表、物化视图、domain／range／composite 与外部 enum 等未覆盖的结构仍拒绝，不能删掉这些对象来强行通过。collector 记录的 relation／function／enum owner 与 ACL、扩展和数据库 locale 必须一致；schema owner、default privileges 和完整角色成员关系仍须由受控恢复清单另外核对。

真实固定版 Logto 1.45.0 的单源只读指纹已在这次 enum 扩展后通过，发现的 8 个 enum 得到完整比较；这只证明该次源库结构和只读可见性可采集，不代表目标恢复、两库相等、proof 或真实登录已完成。当前证据见[S5-A3 真实预检](../../docs/validation/2026-10-24-central-production-preflight.md)，S5-A2 历史记录保持原时点结论。

collector 只读角色需要 schema USAGE、所有表／序列的完整 SELECT 可见性，以及 `pg_control_system()` 的 EXECUTE 用于辨认实际数据库；遇到 RLS 过滤或权限不足即失败，不自动提权。两库必须静止，独立 repeatable-read 快照不提供跨库写入同步或 fencing。collector 有对象／行数／字节上限；PG 收集阶段总期限 180 秒，备份哈希读取在此期限之外。这不是生产压测或吞吐证明，当前不是任意规模数据库迁移器。

归档与私有 JSON 必须使用绝对路径，各级目录和文件均不能经过符号链接；文件须为当前维护用户持有、权限 `0600`、单链接（不接受硬链接）的普通文件，父目录建议 `0700`。配置不入仓库、不交给 Web 用户。不要将密码 DSN 放在 argv、终端输出或公共报告。

私有 JSON 的六个顶层字段如下；真实 DSN 和 SHA 由主机的安全配置流程填写。下面的占位符不能直接运行，应用 ID 必须同时覆盖产品和 Console 使用的 Logto 应用：

```json
{
  "policy": {
    "environment": "production-20261023",
    "source_issuer": "http://127.0.0.1:18763/oidc",
    "target_issuer": "https://login.drawbookai.cn/oidc",
    "tenant_id": "default",
    "client_ids": ["REPLACE_PRODUCT_APPLICATION_ID", "REPLACE_CONSOLE_APPLICATION_ID"],
    "approved_backup_sha256": "REPLACE_APPROVED_BACKUP_SHA256"
  },
  "source_dsn": "REPLACE_PRIVATE_SOURCE_DSN",
  "target_dsn": "REPLACE_PRIVATE_RESTORED_DSN",
  "source_schema": "public",
  "target_schema": "public",
  "backup_path": "/var/backups/easel-private/logto-before-cutover.dump"
}
```

JSON 必须恰好包含上述字段，不能有重复 key。DSN 须显式指定 host／database／user，不支持 service、passfile、options 或多主机的间接解析。远程连接使用受验证的 TLS。源与目标的 issuer 以 discovery 返回值为准，精确保留字符串；不要自行去掉 `:443`。

## 操作顺序

先在隔离目标实际恢复批准的身份备份，保留原用户 ID、应用 ID、角色与必要 ACL，并核对固定版本。确认中央库目标、备份和身份恢复目标后，通过安全配置加载运营 env。以下路径是部署示例，不代表本次已经部署：

```bash
rtk proxy uv run --project services/platform --frozen --env-file /etc/easel-central/central-operator.env python -m platform_service identity-restore-proof --config-file /etc/easel-maintenance/identity-restore-private.json --operator migration-operator
rtk proxy uv run --project services/platform --frozen --env-file /etc/easel-central/central-operator.env python -m platform_service identity-migration-inspect --config-file /etc/easel-maintenance/identity-restore-private.json --operator migration-operator --proof-id REPLACE_PROOF_ID
rtk proxy uv run --project services/platform --frozen --env-file /etc/easel-central/central-operator.env python -m platform_service identity-migration-apply --config-file /etc/easel-maintenance/identity-restore-private.json --operator migration-operator --proof-id REPLACE_PROOF_ID --key migration-20261023 --reason "Approved same Logto restore"
```

proof 落库后有效 1 小时，记录时要求 collector 证据在 5 分钟内。inspect 输出候选数量、摘要、冲突数量和 consumed；冲突不为零或候选变化时停止。apply 在一个事务内重新核对候选和占用，追加全部映射及不可变审计，失败完整回滚。同一 key 和相同请求可安全重试；换 key 不能重复消费 proof。成功后 inspect 的 consumed=true，已有目标占用可能计入 conflict_count，此时不可把它当作待执行 proof。

产品登录解析到原 user／tenant／wallet；Console alias 指向原 admin，实时沿用原 role／active／authorization epoch。禁用管理员保持禁用。已有会话在映射阶段保持原行为，此命令不实现全局退出、停止派发或写入截止。真实切换还要按[部署切换手册](deploy/README.md)停止旧 writer、处理 handoff／登录事务和在途／待核账，再开放唯一权威入口。未知费用仍冻结，不能为迁移清零。

本版本将产品首次登录和 Console 身份写入统一到各自按 schema 隔离的 1024 个数据库锁槽；同一身份总是命中同一槽，碰撞仅增加串行等待，用于控制大批迁移占用的锁数量。不得混跑仍使用旧锁协议的 writer。操作另有 30 秒单条 SQL、2 秒锁等待和 180 秒事务总期限；遇到超时回滚并返回固定错误。这些值是执行边界，不能视为真实生产环境的一万用户容量承诺。

## 本地隔离恢复演练

仓库脚本 `scripts/verify_identity_restore_drill.py` 默认只返回 `not_run`。只有显式 `--execute-owned-drill` 且命中脚本固定的专用合成 PostgreSQL 容器、镜像、loopback 端口和资源限制时才执行。它不是生产恢复工具。

脚本创建随机新数据库和 schema，实际 dump／restore 合成 IdP、收集 proof／应用映射，再 dump／restore 含会员、管理员、已结算与 pending 的中央库到全新空库；比对全表、结构和序列，并用实际低权角色读取。归档与连接文件保留在私有输出目录；公开回执只有摘要和计数。`--cleanup-owned` 仅清理本次随机对象，不停止容器、不删除数据卷；未成功演练不能标记通过。

本批实际结果与剩余正式发布门槛见[身份连续性验收](../../docs/validation/2026-10-23-central-identity-continuity.md)。
