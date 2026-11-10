# B3-L：完整隔离恢复设计

状态：PROPOSED，等待用户确认；尚未开发本设计的新恢复入口。当前 `codex/ui-redesign`，不提交、推送或合并。本文只申请本机合成资料的开发与隔离验证；不申请生产恢复、实际业务密钥读取、远端传输或定时器部署。

## 目的与既有证据

完成已承诺 B3 的本地完整恢复链：实际落盘密文重新认证 → 新 PostgreSQL 实例中的角色、密码验证器、数据库、权限、结构、数据、序列 → 私有配置与完整媒体 → 新维护控制 → 恢复后应用检查及源端保全。不能以私有文件解包或同集群新数据库替代全新实例恢复。

当前中央候选 wheel 为 `a6d5553286c69b3a62f8ae6d7f67f8a43b615b0cd2c0ef67bce13c45cb59e268`。其源码外安装80项及六组合实际通过，只证明已记录的捕获／加密／提交／私有文件恢复，不包含本设计的数据库恢复。

已核对的代码事实：

- `capture.py:_roles` 在受限源管理员连接中取得精确角色集的 `rolpassword` 与 xmin，加入 `credentials`；`roles_export` 已将它们随角色／成员／owner／ACL 封装进原 `easel-roles-v1` JSON。不是缺少角色密码的备份，也不需要为此新增捕获格式。
- `source_facts.py` 绑定真实角色导出、dump、原快照、序列、固定目录与配置。七项归档逻辑文件保持。
- 原 pg_dump 仅导出 public schema；其维护触发器引用 `easel_backup_control` 的受信函数。
- `maintenance_snapshot.py` 明确是历史记录，没有 live-control 恢复接口。原 installer 需要既有业务迁移与表，因此不能直接对空数据库调用后假定恢复准备完成。

## 路线比较

| 路线 | 完整性与成本 |
|---|---|
| A：本机全新无网络容器／PG 实例，严格恢复与比较（推荐） | 复用已验证的本机隔离工具链，能核对独立集群中的角色及权限；完成本地 B3-L 后再形成具体生产演练计划 |
| B：全新隔离虚拟机执行相同流程 | 可进一步覆盖整机与宿主边界；需要先确定资源、磁盘和镜像，不代替相同数据与权限核验 |
| C：原测试 PG 内只建新数据库 | 可作为开发组件反例，但共享角色和实例配置，不能满足本设计的完整验收，不作为最终路线 |

推荐 A。它不取消后续生产 B3、真实旧密文／私钥恢复、异地、整机、压力、多节点或正式桌面发行要求。

## 1. 输入、信任与输出

入口只消费独立受信 RootAnchorIntent、其绑定的 commit／两个密文对象、私有恢复 key 和受信 RestoreProfile。先核 commit、对象长度／SHA、认证 FINAL／EOF，再重算计划与全部内容；完整验证成功前，不把任何归档内容交 PG 执行。

RestoreProfile 在归档之外固定：源 profile／schema／镜像／工具版本、允许角色集合与权限上限、目标 bootstrap 身份、目标数据库名、UID/GID／配置路径映射、资源和期限。归档不能自行授权新角色、提权、宿主路径、SQL命令、环境变量或更大限额。账号、密码、verifier、DSN、SQL正文和媒体内容不进入公开结果或前端。

新目标的 bootstrap 管理员由操作器新生成，与待恢复角色集不重名；与源管理员没有共用连接或认证回退。恢复角色使用原名字，但只存在于新的隔离实例中，从而保留 owner／ACL／成员关系中的引用。若原名字或权限与独立受信 profile 冲突，拒绝；不自动改名或降权后宣称等价。

本地成功回执为 `isolated_restore_verified`，同时明确 `production_ready=false`、`timer_changed=false`、`provider_called=false`。这是本地恢复工具，不新增公开网页端恢复按钮。

## 2. 执行前完整核验

在受限、无外网、只读源材料的解析阶段，由固定 PG16 工具重新读取实际 dump：

1. `pg_restore --list` 只允许固定 profile 所需对象及原精确维护触发器，拒绝额外 schema／未知对象或数据库创建指令。
2. 实际 schema-only 内容使用原固定 restrict key，核对捕获时已绑定的 DDL摘要；目录固定 pin、image／公开SQL pin、完整库存仍须通过。
3. 实际 data-only COPY 库存与 snapshot_projections 逐表比较，检查列、行数、行摘要；全部序列定义／关联／值／is_called 与 before/dump/after 一致。
4. 角色 JSON 按闭合 schema解析，角色名集合、权限属性、memberships、owner、ACL、credentials 逐项绑定；缺失／重复／额外／未知类型都拒绝。只接受受信 profile 允许的原权限属性，不因归档写有 SUPERUSER 等字段就授权。
5. 不执行归档里的角色 SQL、维护历史 DDL 或任意脚本。恢复 SQL 只由受信代码按已验证数据及 `psycopg.sql.Identifier`／参数组合生成。

此阶段失败时 PG 目标尚不存在或尚未收到任何归档SQL；保留私有材料，不自动重跑。

## 3. 全新目标与角色／数据库恢复

中央与 identity 各用全新 PG 实例，顺序建立，均无外网、无主机业务目录绑定、无端口发布。只允许本进程内／容器内 loopback 或Unix socket连接。严格身份检查须同时绑定完整CID、镜像摘要、精确mount／资源配置、原期限和从未启动事实。

开发准入沿用已审 T13 隔离原则；候选资源上限为每目标1CPU、768MiB memory及同额total swap、PID128、只读根、NNP，数据库存储为新私有本地卷。bootstrap只用原准确五项设置权限，业务进程无cap。运行前至少8GiB宿主空闲并按真实包／媒体／数据库峰值核算，运行保留线2GiB；具体执行计划必须冻结完整payload、镜像和限额，本文不授权真实启动。

角色创建先以 NOLOGIN 建立。对每个受信允许的角色恢复原属性、SCRAM／明确允许的MD5验证器或原NULL、有效期、连接数、role GUC及memberships的admin／inherit／set选项和grantor；原LOGIN属性延迟到数据库内容完整恢复后的认证检查阶段再应用，此时目标仍无外部入口、控制仍closed。原NOLOGIN角色保持NOLOGIN。拒绝尝试修改新bootstrap或未列入的内建角色；遇到不能表达的原属性不跳过。密码验证器只在私有内存／受限PG协议中使用，不放 argv 或公开命令日志。

数据库的owner、编码、locale／collation、连接限额与数据库ACL／设置使用受信源环境上下文及归档已绑定元数据恢复，目标名称由profile指定。使用固定 pg_restore 的 error-stop／事务边界，保留owner及ACL，不以 `--no-owner --no-acl` 的结果作为本设计通过。

源角色名相同不等于角色已恢复：必须直接查询新实例的 pg_roles／pg_authid／pg_auth_members，并通过实际密码正控与错误密码负控、最低权限连接验证。公开只输出布尔结果和摘要。

## 4. 维护控制重建

恢复历史 `maintenance_snapshot` 保留为经认证证据；它的旧 token_hash、worker／generation、window、owner、epoch、permit、audit序列不得成为新目标的运行状态。

恢复前，由当前受信 wheel 的固定控制SQL建立必要函数和空控制结构，绝不执行归档自报的维护DDL。首次建立和转入 `NEEDS_REVIEW` 在同一事务内完成；初始epoch0、无window／owner、零workers／attestations／permits。目标对外不可达，业务角色未启用登录，不暴露中间OPEN。

public dump 的原维护触发器按原定义恢复并引用这些新函数，不筛掉guard、禁用trigger或使用replication模式绕过。角色grant由受信构造器重建，包含原runtime／controller职责分离。历史控制与新控制的差异是显式合同，不混入业务目录等价比较。

成功前维持NEEDS_REVIEW。新的独立root私有恢复作业账绑定新实例身份、初始控制行及restore_id。所有数据库、角色、ACL、媒体、配置、源后验和只读应用检查通过后，才允许显式本地完成：对原新控制行做精确CAS，要求epoch0／无旧window／零worker／零permit／无额外活动，原子写入新的 `restore_verified` 审计并转OPEN；不用旧window的transition，不复用旧许可。并发漂移或未知状态均拒绝，不在finally开门。

未通过的目标保留closed；源端控制与当前工作台完全不变。本地完成不是生产启用模型、重启服务或替换定时器的批准。

## 5. 等价比较与读取行为

新目标直接取得全部19类实际目录、raw rows／COPY、序列、角色与权限事实；不能用“pg_restore exit0”替代比较。

- 业务表／行、完整财务图、actual=NULL、预留、来源／关联、账号／会员／订单／管理员／审计均完整比较，不只对媒体子集或总余额。未知费用不结算、不退款。
- 约束、函数／SECURITY DEFINER及search_path、触发器、索引、RLS／policy、types、default ACL、collation、extension、sequence owner／dependency、database配置与权限必须核对；未知对象拒绝。
- 实际目录查询优先使用既有语义名称而不是新OID；允许差异的固定字段仅为 `schema.database[0][0]` 的数据库名（两端必须各自等于受信context的确切名称）、`roles.memberships[*][6]` 的成员行xmin、`roles.credentials[*][2]` 的角色行xmin。角色／成员／grantor／admin／inherit／set、verifier等其他字段逐项相等，不做通用“删掉所有ID”投影。若新增采集字段必须先版本化比较合同，不能静默忽略。每一允许差异都有相邻逻辑字段变化仍拒绝的反例。
- `easel_backup_control` 的目录／owner／ACL与数据不套用业务快照相等：源按原历史校验，新目标按当前受信SQL、确切角色授权和上述全新控制合同独立校验；两者完整记录，不丢弃源历史。public业务触发器与全部其他对象仍严格比较。若工具产生已有冻结R2明确的约束还原差异，只采用对应受信版本的显式修复及完整重核，不推断通用SQL等价。
- 中央001–016／001–020、public identity／固定Logto1.45.0、空／非空媒体、已结算及未知费用、已证missing／deleted不复活均须覆盖。原配置、UID/GID/mode与全部物理文件按受信目标映射恢复，归档自报路径不能落到宿主。
- 恢复后实际启动当前候选应用的只读smoke，所有支付、execution、media provider与后台worker关闭；校验DB连接、健康及受信合成账号的只读数据查询。不能把这写成真实SSO／供应商已验证。
- 激活策略保持本地：验证新worker可登记、旧维护token／generation被拒绝；任何正控写入只在专用新合成验证事务／许可内，并记录预期差异，不改原源数据或用例断言。

## 6. 验收与失败保留

实现先测试再代码，纯parser／比较器不接触PG；Root是唯一真实资源操作者。独立review后才冻结具体Linux载荷及唯一新运行。失败、迟到、未知或观察中断只查询原handle，不自动新建／重放；原失败记录保留。

必要负控：错anchor/key／密文截断／额外对象／角色越权／缺verifier／owner或ACL错误／成员选项变化／序列值或关联变化／RLS与函数体变化／missing复活／scope串租户／金额改动／旧控制重放／错误目标／超限／超时／恢复中崩溃。均不得发成功回执或改变源／旧timer；不能仅用mock替代实际PG拒绝和完整恢复正控。

每例接受需包含新实例身份、已认证对象与源fingerprint、实际恢复与逻辑比较、来源保全、原期限终态、实际工具／应用来源、最终fresh检查和安全回执。读取与执行证明分开，安全manifest不被当作独立运行证明。私有失败目标与日志保留，成功资源仅在完整归档复审后按已有测试清理授权定向处理，不删卷或历史备份。

生产B3继续单独制定真实数据／密钥、源维护、容量、旧格式、私钥恢复、实际服务保全和新timer计划；全部通过后才就确切部署动作请求批准。本文不修改已部署服务，不扩大既有AV网络方案或远端载荷许可。

## 自检与待确认

推荐路线A；本设计只确定完整本地恢复合同，不将B2文件恢复重复记为数据库恢复。现有角色凭据已在密文中，不增补未经证明的“凭据缺失”结论；历史控制与新控制分离，源资金／时间／缺失状态保持。

待用户确认后再写实现计划、代码与具体资源准入。若选择B，先提供虚拟机资源方案再执行，不自行购买或上传材料。路线C只保留为组件验证，不替代完整目标。
