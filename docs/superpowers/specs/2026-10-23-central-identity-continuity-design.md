# S5-A2 身份延续与隔离恢复设计

日期2026-10-23，分支`codex/ui-redesign`。用户在S5-A1交付后回复“继续”，批准继续账号／管理员身份迁移能力与隔离恢复验证。沿用已确认的三个HTTPS域名和115.190.211.112目标，不合并主分支，不自动提交／推送。本批不改真实身份、钱包、Logto或服务器配置，不调用真实模型／支付。

## 路线与信任

比较逐用户双边OIDC证明、手工映射、受控身份库恢复三种方式。本批选择**同一Logto完整恢复、保留subject和应用ID**，这是原本本机Logto迁到服务器的范围；手工JSON不证明身份，逐用户双边验证另有适用场景但本批不开放任意subject合并。

Logto官方资料将sub定义为user ID；本批要求源、目标身份库相同版本和完整数据连续性。collector通过两个明确受信的只读连接，在REPEATABLE READ READ ONLY中比较实际完整schema表结构／数据摘要、用户ID和应用ID，要求**不同实际数据库、相同schema名**，不通过字符串替换重写namespace。比较包括普通表列／默认值／约束／索引、RLS policies、functions／triggers、sequence定义与状态及普通view定义；不能只支持没有这些对象的简化假Logto。明确不支持的partition／foreign／materialized或其他对象应拒绝而非静默遗漏。源／目标连接来自私有0600文件，不接受网页参数。SQL标识符受控、查询参数化、输出只含计数和摘要，不能将用户、口令哈希、签名私钥或整行内容写入日志。

collector还核对批准的备份文件SHA256、固定environment／source issuer／target issuer／tenant／必需应用ID。原中央external身份与Console canonical身份的subject必须确实存在于该tenant的两份Logto users.id中。RLS不可见、缺表／缺列、空或不完整清单、表／数据差异、缺应用ID、备份不符、source与target相同或过期证据全部拒绝。连接需实际具备读取全部目标schema数据的权限，并显式检查row_security关闭／表权限；不能把两个被RLS过滤的空结果当成完整身份库。

这是**受控数据库连续性证明**，信任根是配置受保护的维护者所指定源／目标DB与批准备份，不是新OIDC登录证明，也不能防御恶意数据库owner。本批不把“两个同名用户”或“复制文件”推断为签名登录通过；真实新issuer登录仍须切换后本人验证。任意不同subject、不同IdP、重新注册用户归并不在本批能力内。

## 核心合同

新增migration010，001–009不得修改。五张新表：`identity_restore_proofs`（不可变、限时恢复证据和受核证候选主体）、`identity_restore_candidates`（由proof写入触发器一次规范化的候选索引）、`identity_migrations`（不可变批次／幂等／proof唯一消费）、`identity_migration_entries`（不可变主体级明细）、`console_identity_aliases`（不可变目标身份→原admin）。全部禁止UPDATE／DELETE／TRUNCATE，Web角色不得写；proof、规范候选和审计完全owner-only。

独立审查发现逐条deferred trigger重复全批校验的复杂度问题，本轮修订为规范候选索引、逐条即时索引绑定和每批一次最终集合检查。规范候选只可由新proof触发器生成，不能提前SET CONSTRAINTS后直接追加；entry／alias必须绑定同事务新批次和精确候选。操作设statement／lock timeout及总期限；这些边界不等于已通过1万用户生产性能压测。

`MigrationPolicy`固定字段：environment、source_issuer、target_issuer、tenant_id、client_ids、approved_backup_sha256。目标HTTPS，源仅HTTPS或显式literal loopback HTTP；issuer精确比较且不同；client_ids非空唯一；policy来自受保护文件。

`VerifiedRestoreEvidence`由可信collector在实际查询后构造，包含policy摘要、备份SHA、schema结构／数据摘要、两份DB上下文摘要、该tenant原始user ID集合与应用ID集合，不承载口令／token。仅实际schema owner且有`identity:collect`capability可以通过`IdentityMigrationService.record_proof(evidence, operator)`持久化，1小时有效。该方法在中央DB读取并冻结候选：产品原user、subject、唯一owner tenant/wallet；Console原admin与canonical subject。不信任请求自行填入对应关系，无候选或多owner-wallet歧义拒绝。

`IdentityMigrationService(db, policy).inspect(proof_id, operator)`为只读冲突检查；`apply(proof_id, key, reason, operator)`仅实际schema owner且`identity:migrate`capability可用。服务自己从证据读取候选，重新核对原映射／wallet，不接受任意target subject或principal映射。apply单事务插入同subject的新issuer映射和审计；任一冲突全批回滚。

同key同规范请求返回原摘要，key参数变化拒绝；一个proof只能被一个批次消费，新key不能复用已消费proof。审计不暴露到普通产品API／Console列表，CLI只输出批次ID、计数、摘要，不输出subject或真实DSN。

## 身份解析与权限

产品继续使用现有external_identities追加新issuer／同subject→原user_id；保留旧身份行和外键，不创建用户／租户／钱包／会员／点数／财务分录。

Console保留canonical字段和原admin_id，新增alias指向原admin。登录和approve共用精确resolver；canonical／alias歧义拒绝，不能任意优先一个。alias没有独立role、active或epoch；原禁用状态和后续撤销对全部身份生效。普通approve遇到alias不能创建第二个管理员或提升／复活原权限。

数据库触发器为canonical与alias写入使用同一身份锁和冲突检查，应用层同时遵守该锁；防止迁移与普通approve并发产生双重占用。alias与对应entry必须在同事务形成一致关系。Console只增加必要alias SELECT列，产品role不读alias，两个既有权限守卫检查新表意外授权及可达角色。

实测一万身份的原逐身份锁触发PostgreSQL锁表资源上限，因此产品首次登录、Console resolver／写入触发器和迁移改用各自schema内固定1024锁槽的共享函数。同一身份必同槽，碰撞只增加串行，不能错配身份；正式切换须停止仍使用旧锁协议的writer。本批对产品和Console各一万候选实际合成应用，验证批次最终校验仅一次，但不由此承诺真实4核4GB生产容量。

## 会话和财务阶段

**映射准备默认保留旧会话与epoch。** 这样本批追加身份不打断正在运行的工具链，也不假装已切流。subject映射不更改role、active、epoch或余额。旧设备仍按现有期限／撤销规则工作；新域名须重新登录，不复制token／cookie或修改Pi会话绑定。

正式切流另行停止新登录／派发，排空或保留在途与pending，并fence旧writer。随后明确撤销旧会话、消费旧handoff和相关登录事务；epoch++本身不能阻止旧handoff按新epoch兑换。当前本批不加入一键切流／全体注销按钮，不把数据库映射当作writer fencing。未知费用及held不得清零、释放或重发。默认仍只有原中央DB／schema作为权威账本；恢复副本仅供演练。

## CLI与隔离验证

新增owner命令`identity-restore-proof`、`identity-migration-inspect`、`identity-migration-apply`；运行时Web不加载这些配置。专用私有配置提供policy及源／目标身份库DSN、schema和备份路径；主CLI只接受文件路径和proof/key/reason/operator，不接受用户subject或映射JSON。固定错误，不打印底层异常／文件内容。

在既有专用合成PostgreSQL中运行：新迁移、实际低权guard、签名合成OIDC登录新旧issuer、同钱包／原admin复用、disabled不复活、审批不重复管理员、proof／key冲突与并发全回滚、同email不合并、审计不可变、旧会话保持和pending不变。collector用独立合成Logto schema，验证不完整和伪造文件不能替代真实DB比较。

恢复演练以随机合成中央schema为源，创建一个本次拥有的新空测试DB，使用真实pg_dump／pg_restore，保留相同schema名，不做字符串替换、不用--clean／--create，不碰真实业务或整个集群roles。核对所有数据／迁移／约束／sequence摘要和原ID，重建仅本次随机低权角色并实连guard。保留dump与私有连接文件0600，公开回执只摘要。若旧专用容器不可用，停止并报告，不重置Docker；本轮已核对并启动该精确专用容器，不操作其他容器。

## 官方依据与交付

本轮Context7核对：[Logto sub为用户ID](https://github.com/logto-io/docs/blob/master/docs/quick-starts/fragments/_scopes-claims-introduction.md)、[Logto OSS部署](https://github.com/logto-io/docs/blob/master/docs/logto-oss/deployment-and-configuration.mdx)。Logto数据RLS及tenant-aware关系来自[官方schema changelog](https://github.com/logto-io/logto/blob/master/packages/schemas/CHANGELOG.md)。这些文档不证明当前真实库已恢复或登录成功。

交付源码、迁移、CLI、隔离测试／恢复记录、独立审查与更新手册。代码验证、合成OIDC、实际合成恢复和真实生产迁移分别记录；S5-A1旧构建包仍是历史产物，新增010后不得当作新版本继续发布。
