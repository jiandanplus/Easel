# 日常媒体备份：归档闭包与流式恢复接入设计

状态：待用户审阅，尚未开始新实现。沿用完整交付总账工作流8，不以本文或下述本地证据宣称日常媒体备份、F2或生产恢复已经完成。保持当前分支，不提交、推送或合并；不改前端、真实账号、钱包、接口或定时器。

## 已确认的缺口

1. 本机明确引用的公开旧备份源码为 `/private/tmp/easel-restore-drill-review-20261027/easel_encrypted_backup.py`，SHA `d330bccf0c71f9c4d808411ba48d87c217048dde0763023e4e9ca0cb45bb14a1`，与当前DR工具的BASE_SHA相同。其collect只收两库dump、roles、固定配置／公开文件；没有媒体registry、media.json和blob遍历。它是旧固定源码证明，尚未核当前服务器timer是否另有变动。
2. 三套媒体DR确实已有registry、字节／SHA、权限、缺失状态和同snapshot恢复检查；但它们是空媒体、两成品、三任务mixed的固定窗口。F1/F2不是未开发：合同和只读producer已实现，实际Linux／PG／WPD／新mixed DR仍待验。
3. 本轮在既有源码上实跑10项纯文件回归，全通过。另用自有合成33MiB稀疏文件重现：单blob合法上限64MiB允许其元数据，32MiB聚合门却在任何文件内容读取前拒绝。源码未改，物理分配0字节；不是生产备份失败或真实媒体编码验证。证据见 `outputs/_run/media-backup-preflight-20261029/capacity-observation.safe.json`。
4. storage_root为共享私有平铺目录，随机hex键不带租户／产品边界。授权、引用及费用状态在DB。写入、attach和GC/unlink不处于同一原子阶段；单独导出数据库快照不会阻止GC删文件。不能仅把目录打包后称同窗备份。

## 三条路线

| 路线 | 收益 | 代价与适用性 |
| --- | --- | --- |
| A：先补归档闭包层，再接受控静止窗口与加密／恢复 | 复用现有严格恢复约束；可先在本机合成资料上完成流式文件包、集合及恢复证明，不碰生产 | 生产接入仍需F2／WPD、加密、fresh restore和具体维护批准。推荐作为当前一步 |
| B：直接实现在线backup pin／retention协议 | 长备份期间可继续写入，适合后续多worker与大库存 | 必须协调stage、discard、GC claim/unlink、迁移和租约恢复；改动业务运行路径较多 |
| C：转版本化对象存储／存储快照 | 可用版本ID固定对象，缩短本机静止窗口 | 依赖选定存储、版本／保留策略、资源与费用；现有本地文件后端不能凭空视为支持 |

选择A不会把B／C或生产阶段标记完成。本次待用户确认的范围仅为下面B1的本地开发，尚未获得该设计批准；最终日常备份仍按B2、B3验收。

## B1：本地归档闭包与流式文件层

新增独立内部模块和测试，不修改旧DR／F1／F2文件或其冻结pin，不提供生产CLI、不修改timer。

输入是受信捕获器提供的同批次描述：数据库dump文件及摘要、snapshot标识／source和schema指纹、完整registry指纹与逐blob记录、配置／权限清单，以及明确的已知缺失staged证据。模块只证明这些输入之间的归档闭包和实际字节一致性，不认证捕获器是否真正停写／只读，也不因某个JSON布尔值宣称生产一致性。

媒体行不得按文件名前缀筛租户。所有required attached文件必须存在且符合该行的key／SHA／size；staged存在／缺失须与受信捕获计划逐项相符。没有明确证据的missing、gc_pending／活跃GC、不完整registry或未知part／孤儿文件先拒绝；不创建占位成品、不删源文件、不伪造delivery、用量或费用。deleted／purged墓碑完整随数据库保留，其物理key和对应part必须不存在；存在时拒绝并另审quarantine，不归档为可见blob、不删除源孤儿，也不在恢复时复活。原expires_at、actual=NULL、pin与预留不改写或延期。后续如需隔离保存未知文件，另审quarantine协议。

采用有界、固定成员名的USTAR流：manifest优先，数据库／配置使用固定逻辑名称，blob仅使用校验后的hex key；原始目标路径和UID/GID/mode仅作受限恢复元数据。先在原始512字节header层核类型／尺寸／checksum，不让高层解析器先吞PAX／GNU扩展。拒绝绝对／遍历路径、重复或额外成员、链接／设备／PAX／GNU扩展、坏header、超限、截断、SHA不符和非规范尾部。禁止extractall和执行归档内容。

读写按块处理，不把整个archive或整个blob装入bytes字典。初始内部Limits为单文件4GiB、整个物理archive32GiB、10万个数据成员加1个manifest header、manifest32MiB、块1MiB；调用方只可用受信运维配置收紧。32GiB包含全部header、manifest、文件512对齐填充、终止块和record padding，而非仅文件内容总和。禁止未计数的原始header。

规范终止为恰好两个512字节全零块，随后只允许补齐到10240字节record边界所需的全零padding（0–10239字节）。预先按manifest长度与各成员尺寸计算唯一物理总长，独立核实际输入／输出计数；消费完整规范长度并检查EOF才成功。额外零／非零尾部均拒绝，不扫描无限零尾；多读仅1字节判extra。源为受控regular FD时先绑定fstat大小，未来加密reader必须给出认证边界和外层有界执行期限，不能让未知阻塞流绕过时限。

上限不是当前主机可容纳的承诺，运行前须按实际库存核源／密文／恢复stage多份磁盘占用、metadata JSON与索引内存放大、文件数量和时限，有效容量取最先触顶者。恢复端独立执行自己的上限，不信归档自报的更大额度；不得以payload sum或小型fakestream替代物理字节与资源验证。

文件操作复用现有dirfd／NOFOLLOW／非阻塞、普通文件／单链接、owner／mode、inode／size／mtime／ctime／hash和目录前后稳定性检查。blob仅从受信私有媒体dirfd打开hex key；数据库／配置只接受信捕获器已经打开的受限文件描述符及固定逻辑名，不接受请求JSON中的任意宿主文件路径。流写失败或源变化不得发布success receipt；不自动重跑。输出给调用者提供的sink；B1只用合成资料，不生成真实明文备份。生产sink必须由B2接受审的加密器。

恢复只允许全新私有隔离目录，文件0600、目录0700，不覆盖现有位置、不启动应用或PG。源UID/GID/mode完整记录，B1恢复为调用者私有stage；不根据不受信归档任意chown。正式恢复的角色映射／权限应用在B2/B3绑定受信恢复计划后单独验证。

成功标记仅为 `closure_files_verified`／`private_stage_restored_verified`；不得出现production_backup_enabled、source_consistency_verified、database_restored_verified或财务／身份恢复成功的虚假结论。摘要仅输出数量、大小、hash和固定错误类别，不输出真实key、用户名、DSN、私有路径、原SQL或媒体内容。

## B2、B3：保留完整生产目标

B2在现有F1/F2合同基础上完成并验收同窗控制器：同时阻止新的媒体write／discard和新的GC prepare／claim／physical unlink／expiry推进，并排空已在途写入及已领lease却尚未unlink的GC。屏障必须覆盖整个捕获窗，含现有worker后台循环；模型disabled、exported snapshot或现有storage advisory lock均不能单独充当此屏障。失锁、worker换代、过期、未排空或超时立即中止捕获，不发布新成功备份，恢复动作只在owner／窗口仍可证明时执行，未知状态保留待核，不盲目解锁／启用。

在此边界内，中央registry与dump使用同一只读exported snapshot，roles／序列／配置按既有规则复核；Logto独立snapshot，不声称跨库原子。F2已有只读producer不等于这一完整控制器或实际WPD已通过。初始停写范围、时限和恢复动作须形成具体计划，不在B1暗中增加维护端点或业务锁。

B2同时接有界流式认证加密。当前旧加密器接受整体bytes，不能直接冒充大媒体流式加密；新封装必须单独版本、保留旧解密能力、认证完整性／成员清单和截断／顺序，不能降低到只校验hash。真实账号、数据库和密钥留在服务器私有范围；未经认证／验证的内容不交PG或应用执行。

B3必须重新读取实际落盘密文并认证，在全新无网隔离目标恢复数据库／角色／约束／数据／ACL／序列／配置和全部媒体、缺失状态、UID/GID/mode，核原source后验和服务保全。fresh restore成功与原timer／容量预检完成后，才对确切新定时器和调度窗口请求部署批准。旧timer保持，不能仅因新文件包自检通过而替换。异地／私钥恢复、整机、压力和多节点仍按总账独立验收。

## B1验收清单

- 正常字节闭包：至少0／1／多blob、超过32MiB的单blob及总量、chunk边界、配置与dump引用、documented missing恢复后仍缺失。
- 集合／来源：缺／多行、重复key、错scope引用、attached missing、未知part／孤儿、GC状态、deleted对应物理文件或part、manifest／snapshot／dump不相符全部拒绝；墓碑与已证缺失不复活、不延期。
- 文件攻击／漂移：symlink、hardlink、FIFO、非私有权限、路径替换、读中变更、目录变更、短读／短写和中断不生成成功。
- 归档攻击：坏checksum／尺寸／类型、PAX／链接／目录／路径穿越、重复／额外成员、截断、错误零块数、超规范零尾／非零尾、无EOF输入、header/padding计入物理总长及各资源上限均在写出受信结果前拒绝。
- 恢复：全新私有目录、字节／hash／集合一致、不得覆盖现有目录或按未受信UID写其他位置；失败隔离stage保留，内容不执行。
- 资源与边界：用合成大文件验证实际分块读取和有界内存，不以一条fakestream代替全部磁盘恢复；无网络、生产DB、钱包／支付／模型、真实配置或旧workspace访问。
- 独立review通过，并按相关回归收口；不把纯文件／合成流成功替代B2/B3、实际PG恢复或日常timer部署。

## 审阅状态

本文已自检：无未定义的实现占位；B1只承诺本地闭包与文件恢复，B2／B3保留最终备份和生产验收；未指定尚无依据的保留天数／费用／商户／云存储。按当前用户工作流不commit设计文档。待用户确认路线A和B1范围后，再写执行计划并以TDD开发；AV第四次载荷仍单独等待其已发出的确认，不复用为备份授权。

措辞修订：R2的“本次请求仅批准”已纠正为“本次待用户确认”，明确不是已获许可。技术设计未变；两份独立R2复审只对应原SHA `f0df55e383e815078e96b926c02322f3dcb617b68df8d332ef3d0ae76c0d5814`，原文件完整保存在 `outputs/_run/media-backup-preflight-20261029/evidence/specs/design-r2.md`。本修订不扩大B1或生产范围，不将目标继续或其他批准解释为本设计批准。
