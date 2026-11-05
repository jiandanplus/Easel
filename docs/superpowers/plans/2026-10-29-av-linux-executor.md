# AV Linux 离线执行器实施账

继续完整剩余交付目标，接续已通过230项离线测试的源码准备部件。本批先落实隔离执行与已签名FFmpeg归档的真实提取；不是编译、依赖安装或生产发布。上一目标轮为progress；当前分支保持`codex/ui-redesign`，不commit／push／merge，不改前端文案、账号、钱包或接口配置。

## 共同约束

- 目标仅为已确认154主机；SSH必须沿用严格主机密钥校验。先完成本地实现、测试、复审和精确载荷清单，再判断／取得这份新载荷与执行范围的授权。先前13文件AV授权不自动扩为任意新载荷，更不扩大F2或真实数据迁移授权。
- 仅使用现有系统Python3.12.3、Git、gpgv及systemd255.4，不安装软件、不拉镜像、不停服务。已签名归档原路径只读，所有新数据在全新root0700专用目录，失败保留，不覆盖／自动重试。
- 离线作业：cgroup MemoryMax512MiB、MemorySwapMax0、TasksMax16、CPUQuota200%；RLIMIT_AS512MiB、CPU30秒、CORE0；RuntimeMaxSec120秒。不能以父进程、socket timeout或只看配置替代实际内核状态。
- `RootDirectory`为全新最小根，只读绑定系统程序／库、冻结代码和三份公开来源输入，唯一持久可写目录`/work`；不绑定宿主业务配置、用户目录、账号库、Docker socket或日志socket。能力集清空，NoNewPrivileges；PrivateNetwork与AF_UNIX限制必须实际验证。
- 运行前核RootDirectory实际dev／inode、与宿主不同的network namespace、cgroup/rlimit/capability/NoNewPrivs、只读挂载与owned0700可写目录；不匹配则在来源处理前拒绝。

## Task A：隔离配置及实际状态核对

所有权：`services/platform/scripts/av_sources/linux_sandbox.py`和`services/platform/tests/test_av_source_linux_sandbox.py`。使用标准库、TDD，无网络／SSH／真实用户数据。

固定接口：

1. `build_command(stage, unit_name, *, root_dev, root_ino, host_netns_ino) -> list[str]`。只生成参数数组，不执行。stage仅`/root/easel-av-source-run-YYYYMMDD-16hex`；unit仅`easel-av-src-16hex.service`且hex与stage相同。拒绝路径空白／控制字符／dotdot、错误名字、bool／非法整数。stage含`jail`、`code`、`work`，输入只读来自固定旧RSA公开归档目录。
2. `collect_runtime_evidence() -> dict`，只读取本进程Linux内核／挂载／目录事实及进行不会发送数据的socket／权限探测；非Linux明确拒绝，不创建输出文件。
3. `validate_runtime_evidence(evidence, *, unit_name, root_dev, root_ino, host_netns_ino) -> dict`，严格验证后返回可序列化摘要。纯函数测试不得冒充实际Linux验证。

证据字段至少含：platform／uid／euid，root_dev／root_ino，netns_ino，NoNewPrivs，CapEff／CapPrm／CapBnd，AS／CPU／CORE soft/hard limits，统一cgroup path，memory.max／memory.swap.max／pids.max／cpu.max，`/`／`/code`／`/inputs`只读和`/work`可写挂载及owner0700，AF_INET socket创建被拒绝，宿主`/proc/1/root`目录不可打开。后两项仅创建／关闭socket或只读打开／关闭fd，不发送网络数据、不读取任何宿主文件正文。

builder使用systemd临时service（wait／pipe／collect／Type=exec），RootDirectory、MountAPIVFS、只读bind、唯一work bind、ProtectSystem=strict、ProtectHome=yes、PrivateTmp／PrivateDevices、ProtectKernelTunables／Modules／Logs／ControlGroups／Clock、CapabilityBoundingSet空、AmbientCapabilities空、NoNewPrivileges、RestrictAddressFamilies=AF_UNIX、RestrictSUIDSGID／Realtime、LockPersonality、UMask0077及上述资源值。禁止挂载／重启／swap等syscall组。不引入新版才有的PrivatePIDs；实际cap/proc权限探测必须失败关闭。

只读bind包括`/usr/bin`、`/usr/lib`和可选`/usr/lib64`，冻结`stage/code`到`/code`；固定旧目录`/root/easel-av-rsa-orig-source-20261028`中的`ffmpeg_9.0.2.orig.tar.xz`／`ffmpeg_9.0.2.orig.tar.xz.asc`／`official-rsa-public-key.asc`分别到`/inputs/archive.xz`／`archive.asc`／`release-key.asc`。`/sys/fs/cgroup`全层次只读可见，以保持`/proc/self/cgroup`路径有效。最小根的lib／lib64兼容符号链接由后续operator根据只读预检建立，不在builder中执行。

入口：`/usr/bin/python3 -I -B -c`插入唯一`/code`并运行`av_sources.archive_worker`，追加四个expected参数（unit_name、root_dev、root_ino、host_netns_ino）。collector不得相信环境变量、自报布尔值或外部文件中的sandbox_ok。无效状态各项独立拒绝；测试覆盖命令参数边界及证据缺项／错误值／错误类型，不仅镜像常量。

报告`/private/tmp/easel-av-linux-taskA-report-20261029.md`，保存真正RED／GREEN、SHA及未验边界；不提交。

## Task B：固定FFmpeg来源worker（root）

新建`archive_worker.py`与独立测试。先收集并验证Task A内核状态，再核固定archive／asc／官方公钥的size／SHA；只在`/work`生成私有keyring与日志，运行有界gpgv核唯一固定VALIDSIG并拒绝过期／撤销／坏签名。全成员检查须符合根`ffmpeg-9.0.2`、10399普通文件／126目录／90358343内容字节，随后新目录提取、完整清单／VERSION与文件hash验证。代码和原归档不改动；源码文件不执行，模式仍0600并记录原mode。

公开输入固定：archive12040788字节／SHA8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e；asc SHAd617fd94ea354dadd2a8bb16243d37c44e1e9729b06b6ad58fe30bfb0fa943f2；官方公钥1709字节／SHA397b3becedcd5a98769967ff1ff8501ddc89f8368b8f766e4701377d7dbaabe5，signer/primary fingerprint FCF986EA15E6E293A5644F10B4322F04D67658D8。

完整清单留目标机，取回小脱敏摘要和hash。`build_executed=false`／`production_changed=false`，成功仅关闭这一份签名归档的实际安全提取，不关闭Git完整来源、递归依赖、编译、profile、真实API或总目标。

## Task C：operator、独立复审与实际执行

生成排他stage布局、精确payload／hash、主机/资源/源输入只读预检、systemd作业启动与同句柄状态查询；保留所有失败，不因观察超时重开。控制器不以shell插值传不可信值，不外传密钥或业务数据。

把具体可审阅的载荷、目标路径、临时单位/限制、来源和新文件范围做完后，再按既有授权边界处理执行批准。实际运行前重核主机、空间、原服务身份及payload；运行后核原来源与生产身份保全，单位终态、无残留子进程及结果hash。若任何门不满足，继续修正或请求确切缺失信息，不以配置/测试绿标记实际成功。

## 进度

- Task A：本地实现和116项测试通过，独立r2复审通过；明确修正RootDirectory中`ReadWritePaths=+/work`及矛盾挂载标记。
- Task B：本地固定来源worker实现和31项测试通过，独立复审通过；真实GPG和Linux提取仍待执行。
- Task C：本地operator实现；原日志上限问题经真正RED修正为合计64KiB有界PIPE转发，65项测试及独立r2复审通过。root三模块联合212项通过。实际Linux门槛尚未验收。
- 精确冻结payload已生成于`outputs/_run/central-av-linux-executor-20261029/payload`：4份代码和1份manifest，共71080字节；manifest SHA256 `c5dd053be8deb630cb80f88ea742f2cd3695700c518df484bda806b94ee5f157`。新目的路径与单位见同级`payload-plan.safe.json`。
- 用户随后明确批准本份5文件及一次离线运行。外层bootstrap r2强制`python3 -I -B`且拒绝优化，SHA256 `821470b4f97a717cd07a6a5c7cdfafe8880a9a66091d45cd934b41f257c4ca70`；先验manifest／代码摘要后才import。实际已传输并仅启动指定单位一次，未复用先前13文件批准、未自动重试。
- 首次实际Linux单位约290ms即以`SandboxError`失败，来源处理前的`verify_sandbox`拒绝；没有result、验签、源码提取或编译。当前输出只披露错误类，尚不能判断collector还是validator及具体根因，不能把候选分支当作已证原因。
- 失败现场保留，单位已收集且cgroup无残留；只读前后核对原4个服务、3个容器、3份公开输入身份／摘要均不变，5文件载荷摘要一致。见同目录`run-attempt.safe.json`、`run-result.safe.json`、`remote-failure-diagnostic.safe.json`、`preservation.safe.json`。
- 下一门槛是补充不泄露宿主／业务数据的阶段和gate枚举诊断，再对新载荷及新一次运行单独处理授权。不能降低守卫或用另一执行器绕过这次“一次运行”范围。真实Linux正向验收仍未完成；212项本地通过不替代它。
