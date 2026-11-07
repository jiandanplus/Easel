# B2 Logto 与媒体流程验证增量（2026-10-31）

当前目标仍在开发和验证中，不能作为完整 B2／生产 B3 或新的桌面交付包通过证明。本轮限本机隔离合成数据；没有修改前端、原工作台、真实账号、供应商配置或生产服务，仍保留订阅和自定义接口两种模式，分支为 `codex/ui-redesign`。

## 当前状态（R6 实际运行后）

固定 Logto 1.45.0 合成基线已在新 Linux 容器实际恢复并完成 full020 whole 与独立 fresh B1 文件恢复。恢复前后19类catalog摘要逐项相等，原角色／SCRAM／79表比较通过。原37个媒体节点现实际通过17个（14最小权限＋3旧宽fixture），剩余20个；慢请求phase及恢复单层guard的新样本均实际失败并已停；missing-tools的R2／R3失败保留，R4唯一新实测已通过并完成Root独立终态／原期限核对，其余19个音视频节点仍有工具链验收缺口。以下各节保留过程证据，较早的“尚未启动／尚未通过”指当时批次，不覆盖本段最新结果。

## 已实际通过

- 新 Q1 包的两个 macOS profile 共 10 项 whole 检查、full020 原 21 个 Linux selector，以及 frozen016 whole／storage／regression 已通过。详细范围见 [新 Q1 验证记录](2026-10-31-media-backup-b2-linux-new-q1.md)。
- Logto 1.45.0 的合成账号库 r3 portable 导出通过：79 张表、8 个 enum、4 个受管角色、5 条角色成员授权；401,428B custom dump 和完整源事实的长度／哈希已核对。此项证明导出，不证明 Linux 恢复。
- Logto Linux r2 新镜像实际完成离线安装：33 个依赖加产品包、124 个模块／原生组件检查；builder exit0/noOOM，原 360 秒期限内接受。镜像为 `sha256:f738ff0daa18b205326cba9edf94f976cbab092f3bd4b3dacfd9293bd112206d`。
- Family 新镜像 `sha256:8d1cd59ea59d76beffcbad724006f378458edac0fcbb539ff086428b82a8b870` 完成实际离线安装。文件写入的 incomplete、fresh、promote 三个变体均通过原期限内实际容器验收；PG 存活、runtime app 权限、原 permit 和物理操作状态由真实运行核对。
- 25 个专用卷元数据统计与 f50 指定组件预算已经实测并独审通过。298,110,976B 小于 512MiB，此口径不代表全局 Docker 物理占用或可释放空间。

## 本轮发现且仍在处理

1. Logto 公开镜像回执的小数时间戳误用私有整数解析器的问题，已修正并用实际回执及独立负例验证。新 r3 镜像成功构建，首次新容器也完成私有输入发布，原 60s 窗口不变。实际 `pg_restore` 随后 exit1：实际归档目录包含 `SCHEMA public`，而新数据库也有空 public。保存的120B错误 SHA 与“public已存在”的公开错误文本完全匹配。正在修正新建隔离数据库的空Schema处理；原账号数据解析、密码哈希、owner／ACL和79表完整对比保持。旧失败、容器和输入保留，未重跑。
2. Family 第四个 slow-get 变体 exit1/noOOM，未超时。限定只读标准 traceback 指向原 `test_media_async_worker.py:227` 的 `requests==['GET'] and cancelled` 联合断言；没有读取 locals，所以不能确定哪一部分不满足，更不能先归因于模拟执行速度。原 200ms deadline、20ms stream、1s timeout、资源限制和测试断言都保持。第五、第六个流程没有启动；正在准备极窄的安全诊断，原失败现场保持。

## 尚未完成

Logto Linux 的实际角色／ACL／密码哈希／79表恢复、恢复后的 whole 与 fresh B1 链路；Family 其余未通过流程和原 22 函数／37 节点的完整覆盖；完整音视频依赖链；最终 T14 质量审查；生产 B3 与桌面交付门槛均保持开放。AV 的外部动作授权不由本轮本地开发授权替代。正式支付依用户要求保留未配置状态。

原失败、镜像、容器和数据卷没有删除；本轮没有合并或推送，也没有生成新的桌面 ZIP。

## 后续范围核对

对尚未适配的20函数／33节点已按真实fixture和产品调用追踪：9函数／13节点无需codec，可继续做真实runtime／root隔离适配；另有1个missing-tools负例节点可独立输入；其余19节点需要实际音视频工具。它们不会被统一描述为“都被AV阻挡”。下一小批为scope原函数（固定并发10）和共享钱包并发原函数（固定并发1）；目前仅代码准备，尚未声明通过。旧fixture的宽泛runtime全表DML不能移植到产品最小角色。

本轮安全报告与独立只读复核已归档到 `outputs/_run/b2-development-20261030/logto-family-progress-20261031/`，清单SHA256为 `5befd31ca69d57e6714ac1ec88659640c4c48e0a2556822a885afb0c9550e4a8`。共30份报告、124,925B，不含密码、bootstrap、dump、媒体正文或原始stderr。

## 最新验证边界补充

- Logto r4 已在真实隔离容器中完成受限空 Schema 处理，`pg_restore` 实际 `exit0` 且 stdout／stderr 均为空；后续源事实检查仍报 `B2Error`，所以完整恢复与 whole 尚未通过。正在增加有界安全帧和根私有 catalog 检查点，保留原 pin、ACL、SCRAM、79表比较，不猜测差异、不把工具成功当整体通过。
- 当前 R8 夹具实际给公有表较宽 DML／TRUNCATE 和序列 USAGE／SELECT；已通过结果限定为非 owner、OS 和维护控制面隔离，不等于正式角色的完整最小 DML 验收。下一版 scope／wallet 两项将在 root setup 后真实应用产品 `grant_runtime_permissions`，并在 root 与 app 验证角色。
- 新 THREE 流程的首个维护测试实际失败为 TypeError，原句柄保留，语音与慢请求未启动。源码审查确认新增 coordinator 把 `observe_window` 的 `(binding,state)` 当作 dict 使用，这是验证夹具的 API 契约错误；尚未从父进程实际堆栈证明本次失败的具体行。新版本将修正解包并保存无参数／locals 的安全帧，不改原业务断言与期限。

## 已补齐的最小权限实测（Next2）

`scope` 与 `wallet-concurrency` 两个原节点已分别在新 full020 Linux 环境实际通过，退出码0、无OOM，均在原1800s期限内接受。它们使用真实产品权限函数：原宽授权先被拒绝，再提交正式最小授权；root再次核验，app在启动generation前也执行核验并绑定实际角色摘要。没有额外GRANT或owner RPC。

权限撤销与跨用户／产品拒绝、文本和媒体共用并发名额、三笔待核算上限及原冻结金额，均由未修改的原函数和根只读资金／许可／审计检查执行。scope保留1个原unknown许可；wallet只完成已释放的稳定媒体许可，另外2个原媒体许可保持unknown。它们是合成隔离环境，不涉及真实账号余额。

Root另行核对了18个固定安全文件的哈希、两个容器的实际终态和原期限回执。21份安全资料归档于 `outputs/_run/b2-development-20261030/next2-minimum-actual-20261031/`，清单SHA256为 `7feeb0d83a425dd158289484144862163e3b847a3553e17e904ba9c34df72925`。没有为此读取完整业务快照或重新运行用例；资金行事实来自已冻结实际检查器，不冒充新的独立DB逐行复核。

此项只关闭这2个原节点的真实最小权限执行缺口，其他旧R8宽授权结果不外推。完整22函数／37节点、剩余GC／wallet／AV、Logto全事实恢复、T13／T14／B2／B3继续开放。

## 最新复核：维护流程、慢请求与 Logto 差异

THREE reviewed002 的 `maintenance-async`、`maintenance-voice` 已实际通过，分别约9.0s、10.3s，退出码0、无OOM，均在原期限内接受。每项保留1个原unknown许可，物理操作计数0，应用退出后PG仍可用于检查。语音上传的原helper明确使用 `transient=True`，成功时完成上传许可；这里只保留原job许可，不能按两个残留许可解释。此前关于两个许可的源码推断已撤回，没有采用或运行相应改动。

同批第三项 `async-slow-get` 退出码1、无OOM、未超时。新增的极窄诊断确认原227行断言时GET计数0、取消计数0。它说明断言的实际输入，尚不能证明具体原因；原200ms deadline、20ms流、1s外层timeout和25ms HTTPX配置没有更改。失败后停止该批，没有再次运行、清理或放宽断言。这两项维护结果仍使用原R8较宽公有表夹具权限，不能与Next2的正式最小授权证明混同。

Root独立核对24个固定文件哈希、3个实际容器终态和原期限回执，归档23份安全资料于 `outputs/_run/b2-development-20261030/three-reviewed002-root-20261031/`，清单SHA256 `b9a1e20b695a79f65f604dd426372e4c379d9e7a008193f04f4865e9a1a2a30c`。没有新增DB读取、私有业务快照、模型调用或容器执行。

Logto R5R2 已实际定位到完整catalog验证：19类中仅columns的表ACL表示、namespace ACL及两条邮件日志索引表达式不同；另外16类摘要一致。缺失PUBLIC schema USAGE是真实权限差异，不能忽略；两条索引的数组cast写法也不能直接规范化后跳过原pin。正在准备小型公开DDL单元，验证从原始固定IN语句重建索引以及精确恢复ACL的行为；它尚未执行，更不代表Logto全事实恢复通过。完整catalog、原角色／ACL／SCRAM、79表与后续whole检查仍保留。

跨产品存储3个原节点已完成独立静态／纯函数复审，并进入新的最小权限实测；GC6仍为代码准备。实际结果需由各自新句柄和原期限回执确认，不能提前列为通过。当前磁盘实测约9GiB可用，原空间阻挡已解除；不再要求用户重复清理。

## Storage3 已完成真实最小权限验收

后续首次顺序实测三项均通过：`same-owner-cross-product` 10.73s，`foreign-global10False` 8.26s，`foreign-global20True` 8.23s。各容器exit0/noOOM，均在原1800s期限内接受；应用退出后PG存活，OPEN、physical0，原upload许可按1／1／2保留unknown。三个root安全收据及app检查均使用真实产品最小权限，没有额外GRANT或owner RPC。

实际冻结检查器验证了成功blob的五字段 `blob_staged` 审计与原nonce／epoch／invocation绑定、拒绝nonce的不可变完成审计，以及post-seed资金不变。Root再次只读核对30个固定文件哈希、3个实际容器终态、最小权限及原期限收据。29份安全归档位于 `outputs/_run/b2-development-20261030/storage3-reviewed001-root-20261031/`，清单SHA256 `f7682df1515a34185158b84c21eb976cc5932d23adcc65c436029fc04a5364e0`。

本批是metadata-only存储意图和配额验证，不声称物理图片写入或解码，也没有另行重读私有业务账本。原始主函数、17份公开源文件、权限约束和未知意图保留规则均未修改；完整family／T13／T14／B2／B3仍未关闭。

## 公开 DDL 单元的首次预检

小型DDL单元经18项纯检查和独立limited READY后，实际首次在 `preflight` 失败关闭，仅完成一条公开镜像GET，没有创建卷、容器或执行SQL。API v1.45的无平台inspect返回当前Mac默认ARM64，而原R8验证器会显式选择amd64。这是新验证脚本移植时遗漏的平台选择，不能作为PG／ACL失败的证据。

已按项目要求读取当前Docker官方文档：自API v1.49，image inspect支持JSON编码的OCI platform参数。Root另行只读指定 `linux/amd64` 查询同一缓存引用，确认原固定ID `sha256:f9d1e581cde3e4c8bc696b07e6f7b7aea1d41014b33a52b27990762daae61beb`、正确RepoDigest和PG16.15存在，没有拉取镜像。新R2仅准备补齐平台选择、固定来源与ID绑定；旧预检证据保留，新版本尚未运行。

本次独立小单元的源包与新run目录分别上限256KiB、合计512KiB；原192MiB容器逻辑预算和原120s期限不变。原文“source/stage合计256KiB”的歧义已在root授权文件澄清，没有改变产品预算或把逻辑限制当作已测物理占用。记录位于 `outputs/_run/b2-development-20261030/pg16-public-ddl-unit-20261031/`。

## GC6 已完成真实隔离验收

GC6的正常删除、过期租约、错误token、错误metadata、另一个generation不可重领、unlink提交回执丢失六项，在新reviewed002批次顺序首次通过。全部exit0/noOOM、原1800s期限内接受，正式最小权限root和app检查通过。原PNG写入／检查、上传与scan完成审计、GC claim、文件删除或保留、原unknown许可及删除历史均由实际原函数和根检查器核验。未知状态未被自动结算或清除。

Root另行只读核对6个实际已停止容器及28个固定安全文件，30份安全材料归档于 `outputs/_run/b2-development-20261030/gc6-reviewed002-root-20261031/`，清单SHA256 `1e7d38e6895a2266ddaa372962b27cfae7ccf028297cea84d036300b0af1a392`。此前reviewed001默认沙箱的socket权限失败发生在create之前，原现场仍保留；reviewed002通过正常升权工具执行，没有审核拒绝。

截至GC6批次，原37节点实测通过14个，其中11个采用产品最小运行权限；附加2个维护用例单独记账。完整范围见[覆盖账本](2026-10-31-media-backup-b2-family-coverage.md)。Wallet2两项已完成独立代码审查，尚无实际执行；slow发现原业务pool被移植为每connection上下文新连接，正在仅修订这一夹具变量，原200ms等限制不变，也尚未证明旧失败根因。

## DDL 实际观察及证据限制

R4公开DDL实验的reviewed003实际backend已退出0、无OOM，约1.20s在原120s期限内观测终态，六个SQL阶段均生成输出。但整体reader仍拒绝：server-side COPY产生的六个CSV为0644，而原reader要求0600；父目录仍0700、UID/GID999，原SQL仍0600。没有修改原结论、放宽原时限或重跑SQL，整体unit继续记为 `accepted=false`。

Root对同一个已停止CID做了一次有界只读归档读取，32,256B的SHA256与原执行回执逐字一致。随后仅离线解释这份公开合成输出，原语义检查通过：original与rebuilt两条完整index数组相等，variant不同；直接owner GRANT即生成显式七项owner权限；REVOKE/GRANT亦相同；schema权限恰为owner UC与PUBLIC U。语义报告 `reviewed003-semantic-observations.safe.json` 的SHA256为 `f555416e168365cbe7842e59d75c3d3505d2f9f5abd757e16c91421462c06914`，位于前述 `pg16-public-ddl-unit-20261031/` 目录。

Root还把实际rebuilt的两条完整DDL与源catalog中对应的固定public索引字符串逐字比较，均一致，摘要分别为 `b3990071ffbff1bfe296440b125790dbcb5fc8f9a8b756b810243ced75f9d538` 与 `8b9cb9a37c8d83ad393abae8969513d906ed6b599f0eca0ad7c2b6d22ed74eca`。这支持在已验证Logto1.45 profile的新恢复目标中修复这两条对象，不支持修改SOURCE、忽略原pin或省略完整catalog／角色／SCRAM／79表比较。

截至DDL语义复核时，新的Logto R6仅在代码准备：将严格限定的索引重建和ACL恢复放在pg_restore之后、原完整事实检查之前。没有执行新Logto恢复，没有读真实凭据、模型调用或生产维护；公开单元的权限模式失败也不会被追认成整体通过。

## 截至公开构建批次：Wallet2通过、SLOW继续诊断、R6仅公开构建通过

Wallet2的独立连接池竞争与原lot过期结算两项已首次顺序实测通过，均使用正式最小权限。双池本次自然赢家是text；没有重试选择赢家，也没有对称admission barrier的声明。过期后的真实release拒绝、固定唯一lot到期操作、原批次结算、journal和nonce审计均由冻结root检查器实际执行。Root另行核对2个已停止容器及12个安全哈希，14份归档清单SHA256 `ef2b6031454182f9ba7b910a39aba6c93390df6748647973630ab73b8775ebb4`。

原37节点当前实际通过16（正式最小权限13、旧宽fixture物理节点3）；附加维护2项另算。恢复业务pool的SLOW新样本仍失败在原227行，GET0／cancel0，不能宣称已修好或此前根因已证实；原时间、双层guard、控制连接工厂及失败均保留。实际post-app资金和许可状态没有成功回执，不能填写预期unknown1／physical0。

R6固定Logto145合成基线适配候选经独立32项纯检查与源码审查后，仅完成公开构建：33个输入哈希一致，124个模块、34个安装包检查通过，builder在原360s期限内exit0/noOOM。Root复核33个stage源码、18个固定安全文件、1个实际停止builder并归档19份资料，清单SHA256 `9410e8a92a81f4ca2979060c993ee1169557bc72523b7df6b7eb18d51315d0c9`，位于 `outputs/_run/b2-development-20261030/logto-r6-public-build-root-20261031/`。派生host pyc另列、未进公开payload；源码和code digest未改。尚未启动R6运行、PG或私有输入，完整Logto／whole／B3恢复仍开放。

## R6 合成 Logto 恢复与 whole 已实际通过

本次新容器自然退出0、无OOM，实际运行约146.48s，并在原1800s期限内完成终态接受和回执发布。没有重置原60s导入、90s恢复或后续各阶段期限。一次性脚本只向新nonce绑定容器发布固定合成数据；旧失败句柄、卷和输入全部保留。

`pg_restore` 实际退出0，随后严格限定的2条索引重建、3表owner默认ACL与public USAGE适配完成；实际非super owner与pg_database_owner的SET LOCAL ROLE接口、事务前后身份和search_path检查通过。完整19类catalog在初次恢复及whole结束后，均与固定源逐项counts／hash及整体hash相等。原角色／密码哈希／成员授权／79表数据比较没有被替换或跳过；4受管角色、5成员授权、8enum与79表恢复验证通过。适配只为建立该固定合成夹具，不是生产恢复器改造。

后续真实运行了中央维护屏障、认证备份与独立fresh B1文件恢复：12个blob、41,932,800B物理恢复体量，controller返回backup_committed／OPEN，原许可、审计转换、worker关闭断言均执行通过。fresh B1仍明确 `database_restore_performed=false`；初次Logto夹具建库恢复为true，二者不能混同。whole安全回执保存的是app关闭前observation；关闭后worker断言的证据来自实际driver代码及exit0，不声称另有独立关闭后DB逐行快照。

Root只读导出同一已停止容器的6个固定安全文件（24,514B），核对catalog和whole字段，并将17份安全回执归档于 `outputs/_run/b2-development-20261030/logto-r6-runtime-root-20261031/`，清单SHA256为 `51fccfe4fb9b418644632ceb6ed9d82ddb7473f47265a43072bb7eb9af4ca74f`。不含bootstrap、账号密码、DSN、完整私有facts、dump、媒体正文或原始stderr。该复核不是新的独立数据库逐行恢复演练。

此结果关闭固定合成Logto的Linux恢复／whole缺口；完整family、T13故障覆盖、T14、生产B3、正式支付和新桌面发布门槛仍开放。本轮未改前端、真实账号／钱包、生产服务、定时器、远端或Git分支，未生成新的桌面ZIP。


R6实际安全回执的独立复审已完成，限定范围内无确认P1/P2。6文件bytes／hash、前后19类完整摘要、媒体与钱包增量、固定公开运行代码及原期限链一致。报告SHA256 `6261b847ed3135522a0046af1fa1ffd19b0b7c0a1a4d8f7c166d25e3a40a2e1f`，归档于 `outputs/_run/b2-development-20261030/logto-r6-actual-independent-20261031/`。复审未重新读取DB或解包root原GET tar；header检查由root原实际export执行，不扩大成第二次独立tar／DB恢复证明。


## 本轮后续空间清理的准确范围

按用户此前明确的无用测试容器清理要求，root在逐项只读核对、108份原case公开构建回执补充归档后，仅删除9个已停止exit0/noOOM的冗余公共installer builder容器。不使用force或volumes选项；删除前逐CID再次核对无volume／bind／port、固定installer与提交镜像，删除后逐CID404且原提交image仍200。原失败runtime、所有数据卷、运行镜像、host证据／旧失败报告均保留，没有Docker重启、prune或生产操作。

这是此前“资源保留”历史记录之后的一次明确清理动作；不能将旧时间点记录回写，也不能再称所有公共builder容器都仍存在。metadata的SizeRw合计1,345,875,968B只是可写层估计，未当作物理回收量。实际host可用空间从3,006,865,408B到4,153,905,152B，仅报告这两个采样值。动作和逐项复核归档于 `outputs/_run/b2-development-20261030/public-builder9-cleanup-20261031/`。

最新missing-tools原负例已在真实最小权限Linux隔离环境通过：7.103579920s、exit0／noOOM、原期限内接受，app0／PG存活／OPEN／unknown0／physical0，完整资金行保持。此项只验证缺工具前拒绝，不执行codec或模型。当前17／37，剩慢取消1与AV19；详细边界与回执见[覆盖账本](2026-10-31-media-backup-b2-family-coverage.md#r4缺少解码工具负例实际通过)。
