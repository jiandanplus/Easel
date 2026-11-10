# B2：受控捕获窗口与流式认证备份

状态：新设计待用户批准；零 B2 实现、零迁移、零生产维护。上一阶段 B1 的源码包／229 项验证及 SHA 保持，B1 的批准不扩大为本阶段或 AV 运行批准。本设计覆盖 B2 本地开发和隔离验证；真实停写、旧密文验收与生产部署另以确切载荷／窗口审阅。

## 1. 由当前代码确定的接入缺口

- `media_runtime.run_once` 不依赖模型 enabled，仍推进 async／voice／financial／GC。`prepare_gc`／`claim_gc` 不取得现有 storage advisory lock；`delete_exact` 在事务外先 unlink，之后才由 `finish_gc` 校验 token／取得该锁。关闭模型或单独持有此锁不能形成完整窗口。
- stage、物理写、part promotion、attach／discard 分离；每个 API lifespan 会启动 worker，进程内 task set 不能代表全员排空。`finish_gc` 保留历史 token／lease，通用活跃 GC 判定不能使用全表 token 非空。
- F1／F2／旧 mixed 是固定 16 migrations、固定窗口、只读 producer 与 schema-only dump 合同，不能静默改成通用数据备份或调整原失败结果。B2 新 producer 独立版本，不修改这些冻结源。
- 旧公开 helper 为 `EASELST\x01`／v1／RSA-OAEP-SHA256+A256GCM，32 MiB、整体 bytes、header 预知整体明文 SHA。本轮合成诊断：32 MiB 对照到达替换的 key loader；33 MiB 在任何 key loader 前拒绝。没有加载实际 key 或执行加解密。旧内层 archive 也不是 B1 格式。
- B1 的 `write_archive` 接受 plaintext sink；只结束 USTAR，不负责 crypto finalize。`restore_archive` 的最终 `read(1)` EOF 必须同时代表 final-auth 与密文 EOF。decoder 不暴露 ciphertext fileno 为明文 fd。

依据为两份独立静态报告及 `outputs/_run/media-backup-b2-preparation-20261030/legacy-cap-observation.safe.json`。它们不是生产重现、旧密文恢复或新密码协议验证。

## 2. 路线选择与边界

| 路线 | 当前效果 | 代价／扩展边界 |
| --- | --- | --- |
| A，推荐：持久维护状态＋完整操作许可＋同主机文件锁，接新版认证流 | 日常服务保持运行；短窗口只关闭媒体 mutation，已准入操作先排空，复用 B1 文件层 | 要求全部 writer 接入、低层 DML／文件操作双重覆盖及实际 Linux／PG 验证；本机文件后端只接受单个存储主机 |
| B：受控离线停止全部 writer 后捕获 | 可作为明确维护窗口的 fallback | 有 API／控制台停机；仍需全部进程退出、自动重启抑制、GC／partial 状态证明及恢复 CAS，不能仅 stop 一个 service |
| C：分布式 storage broker／版本化对象后端 | 后续跨主机可提供真正的存储 fencing／版本一致性 | 需要新增存储或 broker、数据迁移和独立性能／故障验收；不把本地 flock 当多节点方案 |

选择 A。当前存储主机为既有 154.217.245.179；部署时仍须重新核对所有实际 writer、namespace／inode、文件系统与 release，设计不证明线上现状。不同主机／NFS／SMB／未知文件系统、未知 writer 或不完整覆盖直接拒绝捕获；多节点目标保留，不因 A 完成而勾选。

本轮拟批准整个 B2 的本地代码／合成与隔离验证：维护状态／许可、完整 runtime 接线、新 capture producer、密码封装、self-verify 和可安装候选。默认不激活真实维护；不改前端、不读真实账号／密钥／归档，不发模型，不改余额、TTL、实际费用或旧 pending。

## 3. 完整窗口：数据库许可与物理互斥共同成立

新增独立维护 schema `easel_backup_control` 及其专用、checksum 保护的 migration 账，不修改 public 的 001–020 或调用通用 `Database.migrate()` 顺带安装待验的 017–020。控制状态、worker generation、operation permit、intent/result audit 均在该 schema。schema 初始化是未来明确部署动作；runtime 只有读非敏感状态和调用受限许可函数的权限，不能 OPEN／接管 owner 或直接更改控制表。

状态为 `OPEN → DRAINING → CAPTURING → VERIFYING → RECOVERING → OPEN`，异常进入 `NEEDS_REVIEW`。每窗有随机 window_id／owner nonce、递增 epoch、固定源主机／boot／进程 start identity、control inode／source root identity、单调 deadline 和原始配置状态。所有 transition 使用 owner＋window＋epoch＋expected-state CAS；审计为追加记录。过期不自动 OPEN，重复动作仅观察已存 intent/result。

operation permit 为服务器内部 opaque capability，不从 HTTP JSON／任意 GUC 的布尔值构造。256-bit 随机 token 只存摘要，绑定 epoch、operation kind、对象／attempt、worker generation 和状态；读取视图不泄露 token。新许可只在 OPEN 发行。DRAINING 只允许原先准入的、同一对象／attempt 的 continuation，不准派新 generation 或替换上游 ID。低层 DML guard 校验真实许可摘要／状态／epoch；不是信任客户端自报 paused=false。控制 schema 与表／函数 privilege、固定 search_path 和缺省拒绝均需隔离 PG 反例验证。

活跃许可表在最终业务状态稳定、全部实际物理引用退出且 exact permit／epoch CAS 成功后收口已完成的临时行；完成结果由原业务持久状态及维护窗汇总审计绑定。unknown／失联／未排空许可不得因TTL自动移除。控制状态transition／owner操作审计保持追加、不可由runtime删改；本阶段不增加自动删旧审计／备份的保留策略。

控制 inode 位于独立 root 管理目录（部署候选 `/var/lib/easel-central-control/media.lock`），不放入 B1 平铺媒体目录。inode/root chain 不可由 runtime 替换；runtime 可只读打开，root controller 取得排他能力。每个已准入 mutation 自开始至 DB／物理／finally 稳定退出持共享能力；controller 取得排他能力并保持全窗。父路径、dev／ino、owner／mode、namespace和文件系统逐项绑定；同路径不同 inode 不视作同一锁。

共享能力使用进程内线程安全引用计数配合同一 open-file-description，跨进程由同 inode 的 flock 互斥。嵌套 continuation 复用原许可／锁，不在 drain 时重新准入。async cancellation 不得使仍运行的同步／thread worker 提前失去共享能力；每个实际物理操作有自己的存活引用，未启动子工作在许可失效后不得写。最后一个实际操作退出才关闭共享 FD，不以 parent task 已取消代替排空。进程死亡／FD 关闭也不自动清理 DB permit；残留状态必须待核。

Linux flock 是协作式锁；它的共享／排他与 FD lifetime 语义来自[Linux 手册](https://man7.org/linux/man-pages/man2/flock.2.html)。本设计的双层许可、覆盖与 generation 证明是额外应用合同；不声称 flock 单独提供数据库或多节点 fencing。

### 必须覆盖的入口

- `media_runtime` 上传、submit／admitted task、执行、worker循环、GC与finally；runtime启动／关闭的 generation登记与排空。
- service/core 的 mint／admit／dispatch／heartbeat／记录／pending／cancel／settle／recover／register；预算 reserve/release、stage／attach、expiry／financial recovery 的媒体事务。普通读与无关文本任务不强加媒体许可。
- async_store／worker 的 claim／seed／recover／lease／expire／retrieve／partial recovery；audio、voice stage／POST continuation／observation／ready／recover。
- `artifacts` 的 put／`_put_verified`／`_write_source`／part promotion／discard／`delete_exact`／目录创建／cleanup。直接调用低层 API 无有效 capability 必须拒绝，而非只包装 HTTP。
- GC 从 claim 前到验证／unlink／finish 的完整生命周期。真正 unlink 前在受控 row ownership 下复核 state、exact token 和 DB clock／lease，阻止另一 worker 接管；不是先删文件再检查 token。unlink 后 commit 未知则保留 unknown，不为捕获清 token 或伪造 deleted。

原 quota advisory lock、wallet→job 锁序和财务不可变 guard 保留。许可取得先于业务行锁；controller 不持 wallet/job 锁等待 drain。runtime失去 DB 许可验证即不开始新物理操作，仍执行中的线程保有 FS 引用直至退出，controller 无法提前取得排他能力。

## 4. 捕获流程与恢复

1. 只读预检冻结 build／schema、全部 writer generation／权限／root/control inode、private目录和多份磁盘容量。没有可信 control schema／全员覆盖时拒绝，而非临时绕过。
2. 记录 DRAINING intent 并关闭新许可；等待原许可、所有物理引用和活动媒体执行完成。初始 drain budget 为 30 秒；timeout 不取消真实供应商、不删除未知 part／GC，不延长 TTL。
3. 排空且取得 exclusive 后 CAS 到 CAPTURING，再核完整 registry 和身份。必须没有活跃／不确定 GC、未完成物理操作、未知 part／孤儿；deleted 的历史 token 不计活跃 GC。旧 pending／缺失只按独立的完整关联证据保留。
4. 中央数据 dump、registry／关系与来源行事实使用同一 read-only exported snapshot；Logto 独立只读 snapshot，不宣称跨库原子。导出、配置收集、B1内容流及crypto finalize期间保持屏障；初始 capture／verify 总 budget 为 120 秒，超时失败，不能产生新成功。序列、roles与宿主配置不归为MVCC snapshot的一部分，采用下述独立规则。
5. 新 generic producer 明确 schema版本、全部原始行／scope／job／invocation／reservation关联、序列／roles、dump SHA与配置 bundle。原始 canonical source-facts实际存入`config/central` bundle的`source_facts`字段；同时存其SHA。其内容绑定raw registry／关系、四项DB/roles文件SHA、各原配置项bytes的SHA／权限、snapshot／schema／worker与独立sequence观察。它不包含自己的SHA、整个config/central bundle SHA、B1 plan/archive SHA或ciphertext SHA，形成无环依赖；B1 `snapshots.source_sha256` 等于该source-facts canonical字节SHA。self-verify须从归档中的bundle重新规范编码／核SHA，并核其四项DB/roles SHA和各配置项与实际成员一致；单存hash不能代替原始事实。artifact operation 使用 `job:<id>`，asset 使用 `invocation:<id>`，不靠随机key前缀推断所有权，normalized plan不取代原始财务关联。
6. 七项逻辑文件保持；`config/central`／`config/identity`／`config/media` 是版本化固定配置 bundle，记录受信白名单逻辑项、原权限及bytes。新增维护配置包含在中央bundle；不接受归档自报的任意宿主写路径。配置原文件FD在捕获前后须inode／ctime／mode／owner／bytes SHA相同；roles／memberships／owner／ACL canonical inventory及roles导出在前／内／后独立采样相同。B3按受信角色／路径映射恢复。
7. 全程检查owner／epoch／deadline、DB keeper、worker generation、exclusive holder与源身份。源后验覆盖被屏障保护的媒体关系与文件；无关文本事务可继续，中央dump内部的一致性来自同一snapshot，不能把live钱包后来的正常变化误写为备份改账。该新合同不改变旧F1/F2的严格窗口／source-post规则，也不替代其未验门。
8. 加密文件durable重读认证、内容self-verify及源后验全部成功才进入发布／恢复。任何失败不发布成功或自动重试。旧备份不删；恢复CAS仍证明原owner／epoch／源身份且没有未完成副作用，才按原状态恢复。未知进入NEEDS_REVIEW，不在finally无条件OPEN／启用模型／启动服务。

本版本对每个sequence采用严格稳定政策：参数、owner／关联、last_value及is_called在dump前、dump记录值、最终后验完全相同；从实际dump提取序列状态复核，不以row snapshot查询替代。允许文本请求继续并不保证捕获成功：例如共享credit_entries identity被文本事务推进，立即废弃本次新备份，不抬高setval、不忽略变化或改旧F2规则。已证明仅为非媒体序列变化、原window／所有受屏障源和副作用仍可证明时可走原owner的失败恢复CAS；任何未知仍保持NEEDS_REVIEW。实际部署若持续有这类正常推进，须另形成包含相关文本写者的明确短维护窗口或新的安全上界设计，本阶段不宣称高负载在线捕获已完成。

controller崩溃／DB连接或锁丢失、worker换代、控制inode变化、TTL／容量不足均中止。旧worker死后残留许可／part／gc_pending不自动清空。捕获子进程／线程必须终止或保持实际存活引用后才能释放exclusive；控制状态持久closed，不能因OS释放FD就恢复新准入。

## 5. 新认证流：显式 v2，与旧格式分开

推荐 PyNaCl 1.6.2 的 libsodium secretstream XChaCha20-Poly1305，保留现有 RSA public/private key类型作随机32-byte流密钥的OAEP-SHA256包装，新label绑定新magic；不旋转或读取实际服务器key。[secretstream 官方文档](https://doc.libsodium.org/secret-key_cryptography/secretstream)提供逐记录认证和final tag；[PyNaCl绑定源](https://github.com/pyca/pynacl/blob/main/src/nacl/bindings/crypto_secretstream.py)提供push/pull接口。下面是应用封装提案，不是已经实现或被这些文档认证的协议。

新magic固定 `EASELBS\x02`（8 bytes）。wire为 `magic || u32be(header_size) || canonical_ASCII_JSON_header || records`。header≤8192，精确字段为`version,algorithm,backup_id,kind,target,recipient_sha256,plaintext_length,chunk_bytes,wrapped_key,stream_header`，拒绝额外／重复字段。version严格int2，algorithm字面值`RSA-OAEP-SHA256+SECRETSTREAM-XCHACHA20-POLY1305`；backup_id为小写规范UUIDv4；kind仅`central-archive`或`central-context`；target为独立受信部署ID（1–128位ASCII标识）。recipient_sha256是DER SPKI的64位小写hex SHA；wrapped_key、stream_header为严格标准base64、无空白、重编码相同，后者原长24 bytes。RSA3072–8192／e65537、wrapped_key长度等于模数字节数保持。OAEP label精确等于新8-byte magic。JSON为ASCII、sort_keys、紧凑分隔符、无浮点／NaN／bool冒充整数；原始字节须与规范重编码相同。header无用户名、scope、文件key、DSN或manifest内容。reader预期context来自第6节持久handoff，不能仅信header。

每条record为 `u64be(sequence) || u32be(ciphertext_length) || ciphertext`，sequence从0连续。AAD精确为`b'easel-b2-record-v2\x00' || SHA256(magic||u32be(header_size)||header_bytes)的32-byte原始摘要 || record的原u64be(sequence)||原u32be(ciphertext_length)`，不再以JSON/hex重编码末两项。只允许TAG_MESSAGE数据及最后一个TAG_FINAL。v2密码帧的chunk_bytes固定为1,048,576，header自报其他值拒绝；B1可用更小IO块，encoder有界合并到固定密码块。数据plain chunk≤1MiB，ciphertext≤chunk+ABYTES。FINAL是同样canonical ASCII JSON，精确键`plaintext_length,plaintext_sha256,plan_sha256,registry_sha256,data_records`，严格整数／64hex且≤4096bytes。plaintext摘要来自actual bytes；另外plan/registry摘要须与持久独立计划一致，不能只互相校验self-report。不得把不确定的整体archive SHA放入预写header；结束时才认证它。

central-archive的plaintext精确长度来自B1 physical_size≤32GiB；central-context的plaintext为独立B1 plan canonical bytes（`1 byte ≤ plaintext_length ≤ 32MiB`，不设1MiB下限、不添加padding，长度等于canonical plan实际字节数），其plaintext_sha256等于plan_sha256。data records最多ceil(length/chunk)，每块除最后一块必须满chunk；FINAL sequence与data_records相等。header／每record前缀／tag／final均计入密文预算，archive上限为 `32GiB + 2MiB + 8192`，context上限为`32MiB + 65536`；frame长度先核再分配，计数／顺序不信输入。TAG_PUSH／TAG_REKEY、零数据frame、非规范header、重复或跳号、额外记录／密文字节、截断、错key／AAD／final摘要全部拒绝。不会更换算法或降级重试。

writer的write返回接受的明文count，最多缓存一块；B1成功后显式finish(receipt)核整体SHA／长度再写FINAL。close／异常不自动补FINAL、不发布partial。decoder最多缓存一块已认证plain，隐藏ciphertext fileno；B1 EOF probe只有在FINAL、所有摘要、精确计数及ciphertext EOF1全部通过后才返回空bytes。先前逐块写出的私有stage仍是未完成数据，不交应用／PG、不发布恢复成功；任何认证失败保留私有失败stage。

## 6. 依赖与旧格式兼容

固定候选PyNaCl=1.6.2。官方PyPI元数据已只读核实：Python≥3.8；当前3.12需cffi≥2.0，现platform lock cffi=2.1.1／cryptography=50.0.2。候选Linux cp38-abi3 manylinux_2_34_x86_64 SHA `c8a231e36ec2cab018c4ad4358c386e36eede0319a0c41fed24f840b1dac59f6`；macOS universal2 SHA `c949ea47e4206af7c8f604b8278093b674f7c79ed0d4719cc836902bf4517465`。尚未下载／安装。实施先验证wheel bytes／tag／license／实际libsodium版本和已知安全来源，再锁依赖；不能把元数据或主仓库main文档当目标wheel/API运行证明。

新writer只写v2。reader只按magic显式分派：v1走原冻结helper、原32MiB和独立expected context；认证失败不fallback。v1解出的旧inner archive交既有legacy验证／恢复路径，不伪装成B1计划。保留原helper源码pin `7dd81fbc97d60c782fe2d2c43d81e37fc0b4ea926504e66309a43abc144ad7d6`；合成RSA/旧v1 roundtrip与负控在本地验证，真实旧密文仍留后续服务器私有范围验收。

legacy外层入口精确为冻结helper的`unseal(private_pem,envelope,expected_transfer_id,expected_kind,expected_target)`，随后用冻结`easel_encrypted_backup.py`的`verify_payload(raw,expected_manifest)`检查旧inner。现有两份源不独立提供可泛用的真实DB恢复入口；B2只交付此有界legacy认证／payload验证适配，不调用旧main／collect或声称真实旧DB已恢复。B3再以确切旧恢复工具／source pin及独立历史expected_manifest验证实际恢复，不用B1新计划替代旧manifest。

### 持久、独立的expected plan/context

每份新备份交付`archive.easelbs2`、`context.easelbs2`及canonical `COMMITTED.json`，固定名字不由输入指定。context使用独立随机流密钥和新v2 kind=central-context，内容为独立捕获计划的canonical字节；archive使用central-archive，二者共享backup_id/target/plan/registry绑定。真实plan不以明文sidecar外发。

commit record≤16384 bytes，精确键`format,backup_id,target,window_id,epoch,plan_sha256,registry_sha256,objects`；format字面`easel-backup-commit-v2`，UUID/标识与摘要规范同上，epoch严格非负int。objects精确含archive/context两个对象，每个精确为固定`name`、严格正整数`bytes`及64hex `sha256`，分别遵守上述密文预算。不从commit接受任意目录、chown、命令或SQL。

commit本身的SHA／backup_id／target进入独立root-private恢复anchor账：每backup_id独占一份0600文件，所在目录0700，原子不覆盖发布并fsync文件、目录和新增祖先目录项。它在commit发布前durable落盘；若随后commit失败则anchor指向未提交对象，读者拒绝且保留待核。不能把从同一不受信备份包旁边读到的anchor当可信来源。当前本机handoff以受保护root账和明确restore intent提供预期commit SHA；后续异地导出必须通过独立已认证通道转交此anchor，缺少它不自动恢复，不宣称异地／整机门已过。

新进程恢复先从独立受信anchor核commit的完整canonical bytes与预期context，再核context对象ciphertext长度／SHA，完整解密／FINAL／EOF后解析严格B1计划，重算plan/registry摘要；只有此后才将其作为archive的expected_plan。不能从archive首个manifest直接反传“独立”预期。两个对象hash都由已锚定commit约束，RSA public-key密封不单独证明来源身份；来源信任由独立anchor闭合。无producer内存、仅可信anchor＋两个密文对象＋恢复key的fresh-process测试是B2必要交付证据。

## 7. Durable self-verify与交付

输出在全新0700私有作业目录、0600 exclusive partial file，短写循环与fsync、关闭后重新打开同一regular/nlink1/inode/fstat边界；以新decoder重读实际密文并复用B1 contract／rawUSTAR逐成员校验（只丢弃已核字节，不把整个archive入内存），确认FINAL／EOF和archive SHA，并从归档bundle重算第4节source-facts。该self-verify不等于fresh DB／角色／ACL恢复。新建作业目录后必须fsync它在backup root中的目录项：fsync jobdir及backup-root父目录；若创建新的祖先目录，同样逐层fsync，不能只fsync内部文件或jobdir。

仅在所有后验成功后提交新备份：两个完整ciphertext不可变对象durable保存，context的实际解密与计划核对也已通过；随后第6节独立anchor durable，再以原子、不覆盖的commit record引用对象，fsync jobdir及backup root和新增祖先。reader／索引只接受完整commit record及其匹配对象，不假设多个独立rename可原子提交文件组。record内容先在私有临时文件完整写入／fsync，再以支持no-clobber的原子命名操作发布；平台不支持该原语就拒绝，不降级成先exists再覆盖rename。receipt丢失／命名结果未知只查原window及exact文件，不生成新nonce盲重跑；不删除旧备份。源manifest、secretstream／RSA key、真实配置不入stdout／前端／公开包。

生产包必须显式包含B1和B2代码、控制schema SQL、版本／来源／许可证及依赖闭包；现platform wheel不会自动包含scripts/media_backup。不得只加开发机sys.path。候选须对当前生产release做明确backport或证明完整迁移兼容，不能为了B2把尚未验收的M2/M3/Incho一起上线。生产当前001–016与本地001–020、B1文件包与应用wheel分别验收。

## 8. 本地与隔离验收

- 先TDD证明全部service/DML/物理入口无capability拒绝，嵌套continuation可完成；DRAINING新任务与worker claim拒绝；旧许可不能授权新attempt；不持行锁等待屏障。
- 多进程／线程竞态逐项重放stage→write、claim→unlink、hash→discard、part promote／finally；取消parent但线程继续时exclusive仍不得取得，未启动子任务不能在close后写；controller崩溃、DB断连、过期owner、inode替换、新generation均无新success／自动OPEN。
- 独立PG验证维护schema权限／CAS／许可摘要／DML guard、public迁移字节不变、旧钱包／scope／quota／expiry行为；不连接生产。macOS测试不代实际Linux／PG与实际systemd门。
- secretstream本地用合成RSA／key；旧v1回环、v2空／边界／多块／大于32MiB回环、已知向量、跨实现读取、每个header/frame/tag/seq/AAD/final篡改、截断与trailing、短IO／无进展／IO错误、关闭／取消和buffer上限。legacy认证失败不得降级。
- capture producer在合成隔离数据库绑定同snapshot dump／raw事实／normalized plan与config bundle；故意错snapshot、scope／billing关联、遗漏registry、gc_pending、unknown part、owner drift、错误旧schema合同全部拒绝。无实际DB dump时不称producer完成。
- 自验只发布认证完整文件；模拟fsync／rename／receipt失败和原结果只读查询；真实大文件测分块、Python内存及磁盘副本，最大配置并非主机容量承诺。
- 新代码独审、相关回归、源码／wheel精确库存与依赖来源后交付B2本地候选。B3实际fresh restore／source保全、真实Linux／生产维护、旧密文／密钥恢复、timer、异地／整机／压力及多节点继续独立验收。

## 9. 授权与审阅

本文件是路线A／B2的完整本地开发设计，不是生产操作脚本。采用brainstorming流程，待用户批准后才写执行计划和实现；不自动commit。两份静态调查、旧格式size对照、官方文档／发布metadata支持本设计选择，但均不等于B2已通过。待独立文字审阅后一次性提交用户确认，不把自动继续当许可。
