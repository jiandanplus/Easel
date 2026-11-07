# 2026-10-27 运维交付与备份恢复进度

继续`codex/ui-redesign`，无提交／推送／主分支合并。本轮不调用真实模型或支付，不修改中央业务数据、原服务配置或原备份timer，不传出备份明文、DSN、配置和私钥。

**当前结果：v6 已通过本轮真实 R2 双库 fresh restore 和独立后置核对。** 运行时间为2026-10-27 02:28:39–02:31:30 UTC（171秒）；nonce `1d69fabc6ee045b4a7538d056f4264c4`，plan SHA `f29c85d968aa7f750301adcfc910c4f69f907ff9711e06d1fbdc41df6119743c`，冻结工具SHA `4d3c4210469d6630dd4cbc71d8d5327e62aa972eedb7084685bb0df85a2c88ea`。

| 数据库范围 | 表 | 行 | 验证 |
|---|---:|---:|---|
| 中央 R2 | 57 | 711 | 原raw schema、全部约束metadata、数据、角色／成员grantor、ACL、序列、DB metadata／设置全部匹配；lots／journals均balanced |
| Logto 身份库 | 79 | 446 | 同样完整严比匹配；没有应用启动或对外发送 |

实际从新落盘密文重新读取、认证、恢复到两个全新无网隔离目标。密文1178443字节，SHA `8690d3856c0ce78904160138a1765a0d49cb3481dfb83df48486bdbb1d31b51e`。独立后置核对两个目标的精确guard通过，均stopped、networknone、无端口、卷保留；源两PG IDs及镜像仍match并running，Logto running，API／Console／隧道／Nginx均active，原backup timer active，新密文root0600及大小／hash复核相符。工具还核验原服务PID／启动时间、原生产容器和原backup定义未改变。production writes=0。

[真实safe回执](../../outputs/_run/operations-readiness-20261027/evidence/restore-v6-safe.json)，SHA `0ac8db04128ff78f3de014693faf55cd77d47b2265b0e66bf4998012c2c42161`。原失败nonce、旧密文、工具、intent、unit、容器及卷保留。原backup timer没有替换为本次冻结工具。

这是当前R2数据库层的一次真实恢复证明：两库分别导出只读快照，不是跨库原子性或PITR。没有副本应用登录／整机／异地私钥恢复、性能／两小时测试或多节点验收；也不外推待部署M1／016和媒体blob范围。下面保留各历史阶段及当时失败／候选状态，不能把历史“尚未通过”当作本轮v6结论。

## 本机运行告警已安装

`check_service_health.py`与独立health service／timer经过根任务完整审阅，写前确认目标路径和unit均不存在。精确安装计划SHA为`6f42e5c26625fe6f8f126d36a75187e39aacb4b799da21a9850fc8860234a7df`；root私有helper目录0700、文件0600，两个unit root0644。systemd-analyze verify通过。

首个真实service probe exit0、status=ok、alerts=[]；新timer active/enabled。原API／Console／供给隧道／Nginx的PID与启动时间、原备份源码/service/timer摘要保持，原备份timer仍active。检查只覆盖服务、容器、私有health、备份结果及新鲜度、磁盘／内存和业务证书，写root私有状态并只在告警代码/等级变化时写journal。没有邮件／聊天／短信／webhook，没有新监听端口。详见[运行手册](../../services/platform/deploy/HEALTH-WATCH.md)。

安装后已实际观察timer触发与状态更新（新InvocationID），probe仍exit0、statusok；这不保证未来所有周期成功，也不证明真实故障告警演练或备份恢复通过；备份文件新鲜度与实际恢复是两种证据。

## 历史：v2同快照备份与fresh恢复失败

冻结工具SHA为`cd463029cfb7463705031f086f49c0f62ad71576838fad56f64d91bbd3c5872b`。16项本机验证包含固定PG16真实合成同快照dump→独立fresh restore、原owner／ACL／角色／grantor／sequence／extension版本等严格比对和错误SQL日志隔离。独立审查发现的SQL日志泄漏与extension版本缺口已经修正，未因此放宽比对。

原SSH调度曾在进入意图前断连；两次精确只读核对确认没有进程／意图／新目标／密文，未自动重跑。根任务随后明确批准重验原计划并使用单一可跟踪systemd unit调度。

首次实际执行nonce为`083b087376364b99961b3126c364bce9`，plan SHA `97efa75e06fc55b31a3ec8f4c944a6afd9921d44a13d7f6d0a2d0da76a8e72cc`，unit InvocationID `0210a33b01f74b919cfeb16d56b2ac43`。真实运行101.9秒后exit1；完整源采集／加密落盘阶段完成，新密文953162字节、root0600，SHA `e6102315fe5862fcf7096201c28a267a54d893ef7969a230c9521dc7c6fcdca3`。

中央隔离目标创建后失败并停止保留，network=none、无发布端口，唯一nonce持久卷保留；身份隔离目标未创建。恢复成功回执不存在，故该轮当时只能标记`central_restore_unverified`，不能宣称后续运行备份fresh restore验收完成。原生产四项服务仍active，密文／intent／失败unit均保留；没有重apply同nonce。

根任务批准对已停本次中央目标做只读诊断：同机内存解密baseline，不输出原行值、角色密码或配置；只报告存在与差异字段／摘要。首个诊断发现roles不一致，但诊断自身EXISTS布尔结果未编码为JSON，停在数据库存在检查，容器已再次停止。修正版诊断已完成：目标业务数据库不存在、角色数量及集合不匹配、同名角色只有rolpassword属性不同；容器再次停止保留。由此将失败收窄到角色重建前或过程中，尚未确认具体根因。这不是再次恢复，也不降低比对要求。

## 安全证据

`outputs/_run/operations-readiness-20261027/evidence/`包含脱敏只读计划、失败现场、health安装计划及实际安装收据；manifest列出每份摘要。私有角色SQL、inventory、应用配置、原始归档明文和密钥不在此目录。

异地备份、私钥异地恢复、整机reboot、PITR、性能压测与高可用仍未由本轮完成。本文件只记录已观察结果，不把纯测试、启用timer或加密自检代替实际生产恢复。


## 后续v3与离线扩展修订

新nonce `b547406d5dbb48a980c14c3d97e0fbd5`在根任务核准新计划后单独执行，原nonce未重apply。工具SHA `f47ee363d2c5ac3c876d1114225175728d1b11c023e387fc9f3223514f630bcc`加入最终PID1就绪与固定phase。该次已经完成角色／建库／pg_restore／权限和完整inventory，在`restore_central_compare`严格比对失败；失败safe回执确认central目标停止保留，身份目标未开始。资金恒等式在inventory采集中通过，但整体恢复仍未验证，不能因阶段推进把该轮标绿。

新密文963402字节，SHA `f9268acee8c171fbee7ac710a8986f539c2568e8a916a495eb68b7579c1db7f2`。该nonce的字段差异诊断已准备，尚未执行；仅固定nonce／cipher／tool／plan等值换为本次，原只读诊断逻辑和结束stop边界保持，等待根任务核准。

离线合成夹具独立复现非bootstrap扩展所有者的默认pg_restore缺口；不能据此断言真实v3原因。候选最小修订只针对新空隔离库，在主用户对象恢复前按baseline显式owner／version／已存在schema预创建扩展，必要时对initdb seed plpgsql做RESTRICT事务重建，临时CREATE／USAGE权限同事务撤销，不提升角色属性、不CASCADE、不REASSIGN OWNED。独立只读审查认可这条标准路径；缺自定义schema时应另加TOC步骤，当前不会无声猜建。

候选工具SHA `55bde7568c29211798641e593b3906049c6013be6b8ae8635d1d5dab23db8542`，21项合成／真实隔离PG验证全部通过，包含非bootstrap plpgsql／普通pgcrypto owner、带哈希bootstrap与成员、严格全量inventory、非空及缺schema拒绝、SQL日志隔离、坏dump事务回滚。候选尚未上传、未形成新真实计划、未执行。后续真实完成必须仍由新nonce、审阅计划与完整restore-safe收据证明，旧失败记录继续保留。


## v3只读诊断与v4待审计划

获批单次diag-v3已完成，InvocationID `99f3c7fd2a5344a386eebad568b6ca75`，unit active/exited、MainPID0、exit0。角色集合／数量／属性完全一致，所有表数量和资金恒等式一致；真实差异字段仅`extensions`与`schema`，扩展差异属性仅`owner`。不能据此假定schema摘要没有额外问题，仍须完整fresh恢复严格比对。目标已停止保留，独立复查networknone／无发布端口，原四服务active；无reapply。safe收据已归档。

v4候选55bde…通过根任务与独立实现复核后上传全新私有文件，原tool／密文／失败记录未覆盖。新nonce `946efa02f3dc46e2b0dfd5f6db7e4934`，只读plan SHA `742d06e7a6aaf0d6d3da49a4ed43620ed0381785cb7126dfc2e3186e2593e7e7`。计划保留两DB分别快照、32MiB明文合计上限、顺序512MiB／0.75CPU／128PID、networknone／无端口、停止保留。预检新unit／workdir／两容器卷均不存在，磁盘约43.4GB可用、MemAvailable约2.8GiB，原服务active。该计划尚未apply，仍等待根任务确认固定nonce与plan摘要。


## v4真实结果与schema诊断待审

获准的新nonce946efa…4934实际单次执行，InvocationID`3db5ce3fcaea46c190614d0ba8742660`，完成新快照／密文／extensions／pg_restore／权限／inventory后仍在`restore_central_compare`失败，中央目标stop保留、身份目标未开始。新的963402字节密文SHA`71e3410f7bcccf0fdc25e74dd1333b5dabb991822dfc6753bf891aee13ece9a2`保留，原生产4服务独立复核active。

后续获准diag-v4单次只读查询已完成，InvocationID`b822411664894638b5673a224b6adbc8`、exit0，差异已缩小为仅schema摘要；extension owner及所有其他inventory字段匹配，资金恒等式匹配，目标再次停止保留。没有reapply或改变schema hash比较。

更细诊断脚本`diagnose_restore_schema_difference.py`（SHA`d72a7a830a2bcaa8e8e2e763cbb647851cb12fe993898ccd8776620e81013311`）已备妥、未上传执行，等待根任务审阅。源customdump仅在服务器内存渲染，若渲染hash不等原baseline则仅对精确原source容器只读schema dump，并要求其schema hash严格等于archive baseline才使用；不等即拒绝。target也严格匹配上次inventory，再只输出差异行数、固定前缀类别计数及摘要，DDL、函数体、默认值、对象名不出服务器。该功能11项合成验证通过，含hash gate、隐私输出和dump失败finally stop；不能凭line multiset相等宣称SQL语义相同或恢复成功。


## schema摘要合同修复与实际7行差异

第一次schema细诊在target gate拒绝，收据／unit均保留。根任务定位到诊断工具误把raw schema digest与旧inventory诊断的SHA256(canonical JSON(raw digest字符串))比较，这两层摘要不能直接相比。新v2按真实typed-summary合同红测修正，保留raw与typed明确字段，schema改变仍拒绝；未修改恢复工具的schema比较。

获准v2单次诊断InvocationID`8a546d2c26c04c64b45a09ff6a23c6d2`完成。archive渲染本身不匹配baseline，因此只读精确原source schema，并严格确认其raw hash等于归档baseline；target的typed摘要也等于prior inventory。源／目标各5886行，仅各7行在文本多集中不同，初级类别均body_or_continuation，故并非只凭行重排即可解释。原SQL／对象名／函数体／默认值没有外发。自有target停止保留，原4service独立复核active。

下一份v3候选（SHA`49e2d888ef92d6ac4085ec7e100b21b7ba00658ec52c5af0a8442474c9898ac8`）仅扩展内存中的固定TOC Type／行首／关键词提示计数，不新增SQL、不改变hash gate或恢复compare，14项合成验证通过；待根任务审阅，未上传执行。计数是提示，不是SQL语义等价证明，fresh restore仍未通过。


## CHECK定义差异已可在公开本机夹具复现

获准schema-v3单次只读诊断完成（InvocationID`b4b39da8b45746c6b4c684c61b78f222`），7行均为TABLE中的CONSTRAINT CHECK行。两个hash gate保持，通过后仅输出白名单类型／行首／关键词提示；没有外发原SQL／真实名称或修改compare。独立后置核对目标停止／原四服务active，safe已归档。

本机同固定PG16镜像的简单text／bigint／json／boolean IN与numeric范围合成CHECK回环全部严格匹配，未支持简单IN折叠假设。进一步使用公开001–013迁移的空数据库复现了schema唯一差异，377约束定义未完全匹配。根任务授权优先在公开夹具回传定义与对应迁移行定位，避免再读取真实SQL正文；当前仍没有形成恢复修法或接受差异。


## v5精确保留原CHECK语法的候选已完成本机验证

公开夹具定位到7条BETWEEN范围检查：源存储为嵌套AND；pg_dump输出表达式后重新parse会扁平化AND。全部377约束的validated/local/inheritance/noinherit/deferrable/deferred属性相同。对应公开migration011:14、012:41/42、013:10/19/20/34。

候选`df5de56dd036e048b6355f5521caf3db7e70f6eb02b2afc1758b144d13a9da94`不改变`schema_digest`任何源码或比较要求，不编写通用SQL规范化器。仅中央固定R2的7个公开源定义及全部属性与已知目标flat形态精确命中时，在本轮全新隔离target事务中ONLY DROP RESTRICT／ADD原BETWEEN语法；任一叶子、名称、属性、缺失或未知变体拒绝且写前核完全部7项。新增所有约束metadata纳入同快照备份与完整严比，plan-v3／inventory-v3明确此策略。

本机公开377约束夹具实际修前schema／constraints不同，修后原raw schema hash、全部约束metadata及所有inventory完全匹配。最终正式定向回归44/44通过、exit0（42.59s），包含公开fresh回环、额外NOT VALID／NO INHERIT属性、函数dollar body内伪TOC／CHECK文字原样保留、名称／属性／叶子变体拒绝、旧扩展owner、密码错误日志隔离与坏dump回滚验证。py_compile／git diff --check通过。所有新本机自有容器停止保留。

候选未上传154、没有新plan或apply；等待根任务完整审阅142行diff。真实恢复仍未通过，不能将这次合成证据代替新nonce的central＋identity完整restore-safe。后续性能／2小时稳定性仅按根任务要求排队，未调度。


## 真实source scope预检与v6受信配置绑定

获批v5只上传新工具并做只读模板预检，没有生成apply计划。真实源7项位于唯一专属namespace，public假设不符；投影namespace后所有已知定义和flags完全匹配。结果仅bool输出，实际schema、名称和定义没有外发。

v6候选`4d3c4210469d6630dd4cbc71d8d5327e62aa972eedb7084685bb0df85a2c88ea`只把namespace绑定到root私有central.env中单一明确的CENTRAL_DB_SCHEMA，不扫描选择namespace。plan-v4同时绑定schemaSHA和完整配置SHA；初始plan、apply重验证、捕获配置及密文重读恢复前均严格校验。固定7项名称／定义／flags模板、ONLY RESTRICT和原raw schema摘要不变。未配置、重复、危险identifier及任意配置byte／schema变化拒绝。

最终本机57/57通过、exit0（70.68s），包含public和随机nonpublic两套完整fresh公开13迁移／379约束回环，schema／metadata／data／roles／ACL／sequence全等，以及原负例。compile／diffcheck通过。候选尚未上传154，等待根任务147行scope-binding diff完整审阅；随后仍须新nonce只读plan、精确批准、真实central＋identity restore-safe。


## v6只读计划已完成，仍待单次apply核准

根任务完整审阅147行v6 diff后批准新root0600脚本上传。真实配置scope预检命中全部7项精确模板，未输出私有namespace。新nonce`1d69fabc6ee045b4a7538d056f4264c4`，plan SHA`f29c85d968aa7f750301adcfc910c4f69f907ff9711e06d1fbdc41df6119743c`；脚本SHA仍`4d3c4210469d6630dd4cbc71d8d5327e62aa972eedb7084685bb0df85a2c88ea`。新unit／workdir／目标及卷均不存在，原四服务active，磁盘43.32GB可用、MemAvailable2919364KiB，源容器IDs／固定PG16镜像及原R2路径不变。

计划、schema/config摘要和资源已保存safe证据并提交根任务；尚未apply。后续仍需精确批准这一nonce／plan后，仅一次新bounded transient unit执行，失败保留且不重派。


## v6 最终真实执行与交接

根任务精确批准新nonce／plan／tool后，仅调度一次 `easel-restore-drill-1d69fabc6ee045b4a7538d056f4264c4.service`，InvocationID `0da756d8888f42a8b0b051def35aa0d1`。terminal为active/exited、MainPID0、Result success、ExecMainStatus0，phase完整到complete并生成fresh_restore_verified。两库各自snapshot window及所有摘要保存在safe中；完整raw schema比较未放宽。

57项最终本机回归、该次真实恢复及独立postcondition证据均已汇入manifest。当前M1之前的冻结R2恢复门槛已通过；后续M1／016及媒体blob属于新版本新范围。按根任务安排结束本轮交接释放slot；性能计划仅排队，没有运行或启动额外PG；没有停止任何source或新testPG，没有删除既有对象，没有提交／推送／合并。
