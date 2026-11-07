# B2 中央服务候选包与本地完整链路验收

2026-10-31，当前分支 `codex/ui-redesign`。本轮完成了维护控制接口修复、测试覆盖缺口修复，以及固定源码下的完整备份链路和真实安装包验证。两个中央服务候选 wheel 已保存。完整 B2、Linux 运行验收、生产部署和 B3 数据库恢复仍未完成，不能用本报告代替这些验收。

**最新代码状态：** 后续独立 QUALITY 审查提出的 raw OPEN 摘要类型问题已在真实隔离 PostgreSQL 中确认并修复，当前控制 SQL SHA 为 `6ce3762c4f63b81a659738faf7189ce86323cbb7961dd8abfb27708074638bba`。新四例 targeted 验证与完整维护回归 247 PASS／1 个原 skip 已通过并独审关闭。两份包含修复的新 wheel 已生成，两个新 macOS 前缀的实际安装、隔离导入和文件来源核验已通过独审；新包 macOS 两组完整链路已实际 10 PASS 并通过限定独审；Linux 两个候选的实际安装和whole均通过，full020原21个selector及frozen016 storage/regression也已取得原期限内绿色结果。最新结果、Logto失败诊断及开放边界见 [新Q1 Linux验证增量](2026-10-31-media-backup-b2-linux-new-q1.md)。下面既有 wheel `622fedfd…`／`cd0eae92…` 与 Linux r3／r4 镜像仍绑定旧 SQL `8fb19df2…`，保留为修复前证据，不当作新修复的包验收。原 379 项零漂移结论也只适用于其固定旧版本，本次新增一个测试文件并仅修改控制 SQL 的 transition 函数。

本轮没有新增前端解释文案，没有切换原 17868 工作台、读取真实业务配置或密钥、调用真实供应商模型、修改真实用户或钱包，也没有远程维护、提交、推送或合并。macOS 工作台继续使用已交付的 v35；本报告的两个包用于中央服务。

**实际修复与独立复核。** 专用控制 SQL 仅修改 `begin_window`、`finish_permit`、`observe_window`。窗口在持久进入 DRAINING 前检查完整身份、设备／inode、原配置、版本和 JSON 类型；同一窗口重试必须保留原上下文。许可完成明确拒绝空 epoch，并在同一事务实际消费恰好一个 active 许可后写审计。原 P1 和 P2 已独立关闭；10 个新合成数据库的原始 SQL 复测确认非法请求不改变控制状态、许可、审计或序列，合法重试／完成保持正确。SQL SHA 为 `8fb19df2dc5b74446da0dcbefad78b5d447f81076ee0b5ac503a92c35e31e418`。

图像负例现通过真实许可、stage intent 和 fence 进入实际存储入口；六例入口计数均为 1，失败保留 unknown，零文件／资产／产物且钱包摘要不变。语音历史 SQL 负例携带原 job 许可，实际命中原 017 的不可变／延迟历史约束，不能再由缺少许可满足预期错误。原 TRUNCATE 语句先被 PostgreSQL 外键预检拒绝；新增相同许可下的 CASCADE 语句才命中 B2 无条件禁止，分别记录来源。两处测试有效性 P2 已独立复跑 20 例并关闭。

测试夹具另有一个最小修订：迁移文件从实际导入包的 `MIGRATIONS_DIR` 加载，去掉 checkout 路径注入；DSN、文件权限、回环端口及新数据库／角色的定向清理规则均保持。源码目录之外的真实安装环境中，原夹具实际加载 0 份迁移的 RED，修后实际加载 16 份迁移的 GREEN 均保留。

**固定版本与实际结果。** 379 份公共源码、测试及运行文件在本轮开始和结束时 SHA 全部一致。下列矩阵按各自范围记录，部分用例重复执行，不能将各行直接相加作为独立测试总数。

| 范围 | 结果 | 实际覆盖 |
|---|---|---|
| 精确 30 个 legacy 媒体模块 | 580 passed，0 failure/error/skip，197.99s | 原财务／历史／权限／存储／真实本地 AV；7 个实际 Pi SDK 用例先执行并包含于 580 中 |
| 导出快照、序列、完整控制历史矩阵 | 72 passed，72.70s | 真实 PostgreSQL 工具和合成 Logto fixture，以及严格正反例 |
| 当前源码完整链路 | 6 passed，62.99s | 001–016／001–020，空／非空媒体，公共 identity／Logto |
| 源码目录之外安装 current 001–020 wheel | 6 passed，63.01s | 产品与 B1 实际从新安装前缀导入，无 checkout PYTHONPATH |
| 源码目录之外安装 frozen 001–016 wheel | 4 passed，32.75s | 原发布兼容候选，公共 identity／Logto，空／非空媒体 |
| 专用 maintenance 修复回归最新补证 r7 | 243 passed、1 个已有 f50 opt-in skip，48.49s | 61 个源／helper／测试的前后清单字节相同；f50 安装例另行实际执行 |

producer 的 72 例同时包含纯数据校验和实际数据库／工具案例，不把每一例都称为真实导出。独立候选证据审计另对两个 wheel、17 份证据、379 份源文件及五组 XML 逐项核验，限定接受本地候选；不代替尚未运行的 Linux 或生产验收。

完整链路实际经过导出快照、pg_dump／pg_restore 验证、raw facts／序列核对、V2 流加密、落盘重开自验、独立锚点、无覆盖 COMMITTED、原 owner 恢复 OPEN，以及新进程认证和 B1 私有文件恢复。供应商使用合成 transport；这里的文件恢复不包含新数据库与角色／ACL 的 B3 完整恢复。macOS 下显式本地 UID 策略不代表 Linux root／app 分离运行已经验证。

580 相对旧 577 净增三个可观测场景：limits 原合并用例拆为三个，另分离一个 TRUNCATE 合同用例。保留 9 个 record_property／xunit2 元数据警告，实际属性已进入 XML；maintenance 的 13 个既有多线程 fork 弃用警告也保留。没有新增 skip/xfail 或降低权限断言。更早 19 个旧测试的原始 before 源码仍不可得，只有其哈希；本报告不声称已重建该旧语义 diff。

独审补查旧 maintenance r5 时，实际日志／XML 仍能核定 243 passed、1 skipped、49.72s，但作者报告所述路径下的 start／after 指纹文件未找到，不能用文字叙述代替两份文件。root 因此按该 XML 中原 18 个模块新跑 r7：243 passed、1 个相同 collection skip、13 个相同 fork 警告、48.49s，退出 0。新 start／after 清单字节相同，61 项均与原 manifest SHA `e155efe220347b8ba5427d93528af9d7a4a5a5e5d365399dbf2e220ce998dd72` 一致，材料保存于 `outputs/_run/b2-development-20261030/maintenance-root-r7-20261031/`。前置 r6 在默认沙箱中得到 111 passed／79 failures／43 errors／1 skipped，其中 121 个非绿由回环数据库连接 EPERM 引起，另一个为系统身份读取被拦；其失败和前后相同指纹保留。r7 在已授权隔离权限下先核回环连接再运行，没有改产品、测试或断言，也不算 Linux 实证。

首轮清洁测试环境同时漏了 TMPDIR 和实际 FFmpeg／FFprobe 路径：临时目录继承 GID0，被实际 GID20 的产品权限校验拒绝；广泛回归的 268 个初始化错误含两类原因。只为新测试目录配置正确实际组，未放宽产品检查。第二轮完整链路通过，但 PATH 缺口仍导致 123 例在 AV fixture 初始化时报错。补入已核验的现有公共工具 `/opt/homebrew/bin` 后，同一源码 580 例全部通过。前两轮日志与 XML 保留在 `/private/tmp/easel-b2-current-final-regression-20261031-r1`、`r2`，最终回归为 `r3`；没有将环境错误冒充产品缺陷或覆盖失败结果。

**候选包。** 文件清单、SHA、XML 和独立复核报告位于 `outputs/_run/b2-wheel-candidate-20261031/manifest.safe.json` 及其 `evidence/`。本轮对两个 wheel 全部 RECORD 条目和 9 份冻结 B1／legacy／许可证资源重新校验。

| 候选 | 文件 | 大小／SHA-256 |
|---|---|---|
| current 001–020 | `outputs/_run/b2-wheel-candidate-20261031/current-001-020/easel_central_platform-0.1.0-py3-none-any.whl` | 495,926 B；`622fedfd834881e4737ef6777f4acc2746fffe2bdaa3c26b3094050bff00d434` |
| frozen 001–016 | `outputs/_run/b2-wheel-candidate-20261031/frozen-001-016/easel_central_platform-0.1.0-py3-none-any.whl` | 437,419 B；`cd0eae922fa5edebbed05224062b5e5377338f751b88affacc254b433a44ee23` |

current wheel 由标准 PEP517 后端离线构建，140 个成员、139 条非 RECORD 记录、20 份迁移；frozen wheel 为 120 个成员、119 条非 RECORD 记录、16 份原迁移。frozen 六个最小 guard patch 未变，相对其上一份 B2 候选，共享 SQL 刷新只改变 SQL、profile 和 RECORD 三个成员，仍没有 017–020 或 M2/M3 接口偷渡。frozen 包内的生产兼容标记保持 false；current 标准 wheel 没有内嵌 package_profile，其待生产验收状态记录在外置候选 manifest 中。

实际加载的 macOS arm64 Python 为 3.12.7，PyNaCl 1.6.2、cryptography 50.0.2、Pillow 12.3.0、psycopg 3.3.6；原生文件摘要与 secretstream 32／24／17 常量已保存。安装验证显式复用此前锁定的 T12 生产依赖环境，产品 wheel 安装于新前缀；不把它写成全新依赖安装或 Linux 原生验证。

**下一步。** 33 个锁定 Linux wheel 文件已经按原 uv.lock URL、大小和 SHA 全部核验，约 24.5 MB。公开 Python 树和 83 个 ELF 对象的静态清单完成，但库树仍有未闭合链接与缺失的 Tcl 路径；静态准备没有被标记为 ABI 通过。完整 Linux 离线 harness 正在编写，需完成真实 root／app／PG 分离、实际工具与快照、OFD／线程／进程故障、超过 32 MiB 的实际归档和资源计量，再进行 T14 综合独审。

执行准备中实际复现并修正了两项宿主问题：命令超时后遗留子进程继续写日志，以及公共 pip 在私有 umask 下把安装文件设为其他 UID 不可读。有限本机 RED／GREEN 分别验证完整进程组结束和公开安装文件权限，私有日志保持 0600。独立审查另发现首次观察已超过原期限的 exit0 仍可能被接受，现已先检查原 monotonic 期限，并将 container、boot、原期限及记录 SHA 绑定到 commit 前的验收；受控时钟／状态反例和独审闭环均保留。这些证据未运行真实 Docker commit 或完整 Linux 安装，不计入 T13 通过。材料位于 `outputs/_run/b2-development-20261030/t13-host-preparation-20261031/`。

原公开 Python 树出现 307 份新增 pip 字节码缓存，原有固定文件的 SHA 无变化；保留原树、原清单及漂移记录。新的准备代码只按原 1,721 项清单复制到独立目录，排除已知缓存、拒绝其他未知新增文件，并复核所有 mode／SHA／link。18 份脚本固定并经过独立静态审查后，root 实际执行 full020 prepare：1,721 项投影精确一致、307 项缓存排除、34 个 wheel、18 份脚本，共 1,795 项 rootfs 记录／59,281,965 字节。payload SHA 为 `505410bde0335123c998c115e36823830f661aca042a3d15d10001b6dc4784bb`；这一步未调用 Docker。

随后一次实际受限离线安装在初始化阶段退出 1，未 OOM，容器 `15d30a37bcc8c62b6b1e0991295ff76a7c28f474e89a8698f898a2b410f09e28` 保留，未 commit、重试或启动 PG／业务。安全日志只有 `offline_builder_failed / PermissionError`，回执目录尚未创建。对该停止容器的 `/opt` 只读首归档头核验得到 UID501／GID0／0755，不满足 cap-drop ALL 的 root 写入前提。正在修正公开材料的属主传输；原冻结脚本、prepared context、失败容器及日志均保留。没有把该运行标记为 Linux 安装、native smoke、实际权限或 T13 通过。固定 r2 与失败证据位于 `outputs/_run/b2-development-20261030/t13-r2-actual-preparation-20261031/`。

**实际 Linux 安装与权限组件增量。** 新 r3 显式 USTAR 属主传输经真实 host RED／GREEN 和独审后，root 实际准备并逐项核对 1,795 条记录、60,620,800 字节归档，SHA `510797004bdcf97980bddd3bfa9548a724a6eb330377f9b0bdd9c5dcfe917d4a`，payload SHA `28239d1da6e629d960d9e3155081d085ff85591ec5ba02fcf7cc8acd8a2f2bac`。新停止容器中三个关键目录的实际 tar header 均为 `0:0/0755`；原 r2 同 CLI 的未覆盖 `/etc` 为 `0:0` 对照，排除了 stdout 统一映射 UID501 的解释。r2 原日志仍不用于断言具体抛错行。

新 r3 安装器实际 exit0／noOOM：33 份依赖及 current020 wheel 共 34 包的锁定 SHA、安装 RECORD、文件／祖先可读权限、109 个产品模块及 15 个 native／stdlib 模块来源、原生加密 API／许可证均核对。实际环境 Linux x86_64、Python3.12.16、OpenSSL3.5.7，cgroup 为 1CPU／1536MiB／swap0／PID128；安装器 UID0、CapEff0、NNP1。可选 `_tkinter` 的 ImportError 明确保留。该步骤未启动 PG 或业务，也未证明 app UID 已运行。

原 commit 已 returned0，但 Docker29.5.3 在唯一 SHA 前输出弃用提示，使宿主结果解析断言失败。root 在原 360 秒内严格核原 terminal／wall fence、已保存的原 commit 回执和原精确 tag，仅做只读 inspect，确认原 14 层加新一层、entrypoint／payload 标签及 image `sha256:6966b70f43e47540366125518503f9d2c69f68c3ae00953dacd4fb814024b948` 后保存接续回执。没有重 commit／安装或重设期限，原失败 traceback 与 warning 日志保持。独审限定接受本次既有镜像及安装证据；通用解析代码的最小修订仍在新 r4 收口。

首个 permissions 容器创建后因 Docker API 将原五项能力写为 `CAP_CHOWN` 等带前缀名称，旧字符串校验拒绝启动。root 保留原 raw inspect，仅对精确五项名字做等价规范化，复核其余原限制后首次接续同一 created handle；无新 nonce、重建或已开始期限的重置。原 driver 实测 Linux ext4、root CapEff `e3`，app UID/GID10001／groups空／CapEff0，九项写操作被内核拒绝、只读 SH 与最后引用期间 EX 阻塞／释放、root0700拒绝及 inode／owner／mode 负例均通过。该组件实际 exit0／noOOM，明确不含 PG、完整备份或其他媒体 family。上述材料位于 `outputs/_run/b2-development-20261030/t13-r3-linux-install-permissions-20261031/`。

T14 逐项映射还确认：现有 21 个 Linux selector 的正向业务以 image 为主，async／audio／voice 的缺 cap 负例和 import 不代替真实 claim／recovery／physical／finally 链；GC 的实际 unlink 后回执丢失和 LOGTO_145 Linux profile 也需另补。已有 Mac／PG 实证保持，但不自动变成 Linux 证据。原 42 项检查表保留并明确 partial，不用本增量关闭 T13 或完整 B2。

**QUALITY Q1 的真实修订。** 原 SQL 用 `->>` 加 hex regex 校验三个恢复摘要，64 位十进制 JSON number 被转成文本后同样通过。root 使用合法 RECOVERING 窗口、授予的 controller 角色和显式 commit，逐字段得到三例实际错误接受：OPEN 审计值为 int，审计序列从 4 增到 5；合法数字字符组成的字符串及其原样重试为正例。新增测试的首轮忘记按 owned fixture 契约显式 commit，得到四失败，初版源码和结果保留；补 commit 后才形成有效的 3 FAIL／1 PASS 行为 RED。

修复仅在 transition 中要求三个 JSON 值均为 string，并将完整 OPEN 形状／NULL／regex 校验移到幂等早退前；其余函数逐字保持。相同四例随后 4 PASS，非法输入核对七个控制表和审计序列完全不变，合法重试不追加审计。两轮各自 62 个文件的前后清单一致；RED 与 GREEN 仅 SQL 一项差异。材料位于 `outputs/_run/b2-development-20261030/open-proof-type-fix-20261031/`。该问题按严格 proof／audit 类型合同 P2 处理，没有扩大为运行时权限或资金绕过，也未更新任何既有真实数据库或服务。

完整 R8 回归使用原 18 个维护模块加新 proof 模块，实际 247 passed、1 个相同 f50 collection skip、13 个原 fork 警告、50.93s，退出 0。62 份 start／after 清单字节相同，SHA 为 `c2bbe4284353286d70ded21f7b506ff5bb60d758cbb6112ae7a6153e7bffd5d2`；独审逐项核对当前源码及 XML，并确认原 244 个案例保留，仅新增四例。两项原测试的 collection UUID 参数 ID 变动按其源码单独规范化，原差集保留。完整材料位于 `outputs/_run/b2-development-20261030/maintenance-root-r8-open-proof-20261031/`，本地维护回归不当作 Linux 证据。

**Q1 新打包候选。** `outputs/_run/b2-wheel-candidate-20261031-q1/` 保留独立 18 文件闭集，旧候选不覆盖。current020 新包 496,024B／SHA `c23cb4cb555e24f96c509abec649fd19fa1e750c2e83b7861d05286eb3a1a6dd`，由相同 Hatchling1.32.4 标准离线 PEP517 构建，与旧包只有 SQL／RECORD 两成员变化。frozen016 新包 437,516B／SHA `2cbd3002bb028b9fddf5f8d9bc154ad451b696887bbfcd4a620abc369c64e861`，实际 parent 是已验证的旧 cd0 候选，仅 SQL、profile 对应单摘要及 RECORD 三成员变化；原 f50 release 只记历史 lineage，没有声称这次重新读取原 base。其余成员、六份 guard、公共迁移及九份冻结资源均逐字保持，production compatibility 仍 false。完整 RECORD／CRC／成员闭集及 380 源文件前后相同已核，构建证据本身不包含实际安装、PG 或 Linux 结果；后续小量安装补证见下文。

**新包实际安装与静态采用补证。** 两个全新 macOS 前缀实际通过离线 uv 安装新 wheel，随后在隔离 Python 中核对产品、db、B1 的来源、全部安装文件与 RECORD，current／frozen 迁移分别为 20／16 份。四项 native 实文件摘要与既有锁定依赖一致；本步复用该依赖环境，没有重新安装依赖或验证新的 ABI。380 项源文件前后不变。独审亲读安装与导入命令、回执、ZIP 和安装文件，限定接受小量安装；origin 回执未单存 cwd，因此不另声称独立证明进程 cwd。五份关键安装材料、两份总审报告及 ZIP 子审已归档到 `outputs/_run/b2-development-20261030/q1-tiny-installed-20261031/`，共 9 件，manifest SHA `207bbf8683a8949a55f0edee5de3551910454a8773cd88cb9749a8d98bb333a3`。新包 macOS 完整链路 current6／frozen4 后续实际通过，见下文。

实际 r4 Linux 安装后首次 whole 在 PG 初始化前因 PermissionError 失败，原容器、日志和期限均保留。相同五项能力下的一次受限 Linux 系统调用探针复现 chown 后 chmod 的 errno1；先 chmod 再 chown 的对照通过。R5 仅调整该顺序、核对最终属主与 0700，并增加固定阶段／数字 errno 诊断。R6 在此基础上仅采用两个新 wheel 的路径／大小／摘要和 smoke 的新 SQL 摘要；其他 17 个 payload Python 与 Dockerfile 保持。独审核对 41 项候选文件、精确新旧 ZIP／RECORD 及所有未完成标记，静态采用审查当时未运行 R6 prepare、安装、PG 或 whole；后续实际结果见下文。报告归档于 `outputs/_run/b2-development-20261030/t13-r6-resource-adoption-review-20261031/`。原 42 个检查 ID、21 个 case 及 family／GC／Logto Linux／32 并发限额缺口仍保留。

此前磁盘不足曾阻挡完整验证；2026-10-31 实测可用空间恢复到 11.16 GB 后，已恢复实际执行。没有为腾空间删除数据卷、旧交付包或失败证据，没有重复重启 Docker。每阶段仍检查至少 2 GiB 保留线与原资源预算；完整 B2 与整体交付目标保持开放。

**新 Q1 包首次 Linux 完整链路。** R6 的 41 项固定作者文件与新包摘要逐项匹配后，实际准备两份 1,795 项公开 rootfs；两个全新容器的三个固定目录 tar 首头均为 root:root／0755。两个 profile 均在原 360 秒期限内完成离线安装、全部产品/native 导入及标准 commit。current020 新镜像为 `af4f03914fbe20064fc68fe5e56ec610112921b484ccff4a8b22a4348ef4c3e8`；frozen016 为 `e278e869d6f2d8c1128ccd54549e21d369d0e60fcb4b332123cc67a764785ca9`。两者均使用新 SQL `6ce3762c…`，不是旧 r3／r4 镜像结果。

随后两个首次 whole 均实际 exit0／noOOM，原 1,800 秒 guardian 的 `accepted_exit_zero=true`。各自新 PG 的真实 UID/GID 为999、CapEff0、NNP1，应用以10001运行，介质为本地 ext4；源、加密捕获、COMMITTED、原 owner 恢复 OPEN，以及独立 fresh reader 认证与私有 B1 文件恢复均通过。current020 为12个 blob／41,932,800 B，frozen016 为12个 blob／41,676,800 B。身份源仍为公共 schema fixture，**不包含 LOGTO145 Linux 变体，也没有执行 B3 数据库／角色／ACL 恢复**。所有材料归档至 `outputs/_run/b2-development-20261030/linux-new-q1-whole-r6-20261031/`，83件／2,567,287B，manifest SHA `dd2b2520cf3f3221334c5aad997d614b8d53174526e2b1d5b55177bdd17cd835`。PG 合成管理员 DSN 从内存丢弃，归档仅包含身份摘要。

**新 Q1 包 macOS 完整链路与复核。** 两个实际安装前缀按顺序运行原6／4例，分别 49.65s／33.77s，10 PASS、零 skip／warning。实际捕获与 fresh-reader 文件比对通过，380份源码前后三阶段保持，两 prefix 原151／131份常规文件无删改。原 gid0 临时目录导致的6次早拒绝现场完整保留；r2只为新临时根初始化实际501:20属组，没有放宽 ControlLock。图片检查的真实子进程生成各11份正常 pyc缓存，已只读核 header、marshal／compile与嵌套code及源码对应，不执行缓存；准确结论为原分发与源码 bytes 不变，prefix 集合增加已验证派生缓存。

Mac公开helper／XML／结果与限定独审共71件、806,633B，归档于 `outputs/_run/b2-development-20261030/new-q1-mac-whole-independent-20261031/`，manifest SHA `d59f2aecf5ab84052d4256a17de19248da57a941b44e9c864670eb66a27814c1`。独审实际读取10组COMMITTED、20份捕获／fresh PGDMP及5组非空PNG字节对应；没有重跑产品或读取keys／DSN。Linux独审核安装／源码来源／回执链，未独立提取原volume archive/dump，两个证据边界分别记录。

**实际 quota 与 GC 增量。** R7-r2 full020 regression 已在原期限内 exit0／noOOM：32个真实job／原许可／完成审计，64条reserve/release journal、128条平衡entry，且第33次409拒绝后的业务摘要不变。Root从终止容器的固定合成结果独立重核计数、原摘要和许可。GC首次在 pre-app 财务快照因 sequence 无 composite type 失败；原PG日志及失败保留。R8仅把该一条SELECT改为显式三scalar，READ ONLY与严格全行／序列定义比较不变。后续 full020 storage实际通过：关闭前原GCpermit已unknown/invalid/notclosed/physical0，原5个unknown许可身份一致，2attached/2deleted，20金融表与2序列前后 canonical bytes 相同。兼容f50的新image及两场景仍在验证。

其余 kernel／进程／时限故障按原41文件R6运行，当前指定3个permission/stage-write/claim-unlink与后续9个生命周期场景实际通过，最后6个剩余场景在各自原guardian下串行执行。full020 async/audio/voice、Logto145 Linux 与最终 T14 继续开放。Logto exporter唯一一次尝试在snapshot阶段早失败，未创建Docker Exec或dump；原nonce/output保留，正在准备独立只读诊断，未自动重试。

生产 B3 同窗捕获／定时器、真实旧密文密钥与数据库／角色／ACL 恢复、AV 第四载荷、永久工作区迁移、签名／公证及净机原生验收、第二个真实产品仍各自保留在总执行账。支付商业配置继续遵守用户延期决定。本轮候选不改变这些范围，也未将整体目标标记完成。
