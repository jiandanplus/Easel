# macOS 正式签名与公证发行设计

状态：待用户审阅的设计，尚未批准实现。当前没有签名、公证、身份查询或软件上传。本设计属于既有完整交付目标中的发行工作流，不替代 Linux／媒体／恢复／原生交互等其他未完成项。

## 目标和现状

为 Easel 增加可重复、可恢复、失败时不发布的 macOS 正式发行流程：安装包继续内置 Python、Node 和 Pi，保留订阅／自定义接口、现有三栏界面以及账号和历史。前端不新增开发说明。首个目标保持 macOS 15+、Apple Silicon、Python 3.12.13、Node 24.22.1、Pi 1.4.0，不通过升级这些依赖解决签名兼容问题。

现有 `build_pi_macos.py` 生成 unsigned preview。签名会改变某些已登记的二进制字节，直接对旧包补签可能使 `resolve_pi()` 和 `verify_inventory()` 拒绝。本设计保留上游输入验证和现有运行时校验，增加合法签名派生的完整记录；不关闭校验，也不把旧 v36 改称正式发行包。

工作继续在 `codex/ui-redesign`；不 commit、push 或 merge。原 17868 工作台及其实际账号、工作区、历史、接口和钱包不属于本阶段操作对象。未回答的 v36 切换问题不由本设计替代。

## 方案选择

| 方案 | 组织方式 | 影响 |
| --- | --- | --- |
| A，推荐 | 提取可复用的已验证组装阶段，保留 preview，另建正式发行编排入口 | 公证恢复与本地构建分开，便于保护旧包和逐阶段验收 |
| B | 在现有 builder 内加入签名、上传、查询和恢复参数 | 入口较少，但现有单次离线构建函数需要同时管理凭据和长期任务 |

采用 A。共享来源、复制、离线依赖安装及库存工具；正式编排消费自己新建的组装结果，不接收任意旧 App 或已发布 ZIP 原位修改。

## 正式包和启动器

正式分发对象为单一 `Easel.app` 的 ZIP，bundle ID 拟为 `cn.drawbookai.easel`。显示版本沿用当前 `0.1.0`；构建号为发行命令显式输入的版本字段，记录进新来源权威及回执，不从目录名称推断。后续版本可以改变该字段，不覆盖同名已发布目录。

正式路径新增小型 arm64 原生启动器；preview 的 shell 入口保持。启动器通过自身可执行文件位置解析包内 Resources，支持路径中的空格和中文，使用固定内置 Python 执行原 `easel.portable.main`，保留 `--workspace`、`--port`、`--no-browser`、`--stop` 参数语义。保留自然 HOME 和原工作区选择逻辑，清除原 launcher 已清除的 Python 注入变量，使用 `-I -B`；不查找或下载系统 Python／Agent。

正式包不附带包外 `Stop Easel.command`；同一已签启动器继续支持 `--stop`。程序数据仍在包外，退出服务的现有 owner 认证不变，不新增全局空闲服务或擅自取消远端任务。原生入口是本项目为稳定封装选择的方案，不宣称 Apple 强制禁止脚本入口。Apple 说明非 Mach-O 对象的签名可能依赖扩展属性，放在正确 bundle 位置的资源由外层签名覆盖。[签名存储说明](https://developer.apple.com/documentation/technotes/tn3126-inside-code-signing-hashes)

启动器只为本次 App 工作；不扫描或终止其他实例。日志仍在用户自己的 Easel 日志目录，以受限权限写入，不输出认证信息或整个环境。首次登录、会话和模型调用仍由现有产品流程处理。

## 来源权威与构建输入

保留 `verified_sources()`、固定 Python 归档／运行时库存、26 个离线 wheel、Node／Pi／npm lock、前端 receipt、许可证验证。源输入目录只读，所有签名只作用于新 stage 的副本；复制后的字节、文件类型、模式及内部链接必须对应已验证输入和确定的组装映射。

新增启动器 C 源、entitlement plist、发行 policy 放在 `packaging/macos/`，由现有 `build_input_records()` 对该树的递归纳入。新的发行 Python 入口及模块位于 `scripts/packaging/`，需要显式扩充 build inputs；不能依赖当前排除该目录的 runtime source policy。无需为此将所有 C 文件加入运行时公开资源扩展名。

每次重新冻结独立的 source authority，记录新增输入和实际数量；旧 v36 的 authority、库存和 ZIP 不变。原生编译记录实际编译器／SDK 版本、确定 argv、arm64／最低 macOS 15 目标和输出 SHA；不声称未固定的本机编译可复现。编译只属于构建环境，最终用户不需要编译器。

## 签名对象和权限策略

构建阶段按真实 Mach-O 格式及有效 bundle 结构形成有限签名计划，不根据文件后缀或 executable bit 猜测。主入口必须为 arm64；其他已分发的 Mach-O 架构／slices 全部登记并验证，不悄悄删除依赖来通过公证。内部链接保持原 literal target 和范围；Pi 继续禁止链接。签名不跟随外部链接，不通过共享 inode 修改原输入。

对独立 Mach-O 和合法内部 bundle 按依赖从内到外处理，最外层 App 最后签。对一个内部 bundle 封口后，不再改写它覆盖的资源；不能把 Pi 根目录额外封成覆盖随后重写 manifest 的 bundle。不得用 `codesign --deep --force` 一次覆盖整个树来替代计划；`--deep` 可用于验证。[Apple 分发签名流程](https://developer.apple.com/documentation/xcode/creating-distribution-signed-code-for-the-mac)

实际发行要求明确选定的 Developer ID Application 身份、匹配 Team ID、有效签名链和安全时间戳。签名计划中的每个独立进程入口，包括 native launcher、包内 Python、Node，均逐项启用并验证 Hardened Runtime，不能只给外层 launcher 设置。库仍不附加进程 entitlement。不接受 ad-hoc／开发身份冒充正式签名。签名操作不使用 sudo。私钥留在用户自己的钥匙串，公证只引用已配置的 keychain profile；不把密码、私钥、API key 或 Apple 账号写进源码、App 或公开回执。[公证前提](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)

首版精确权限 profile：Node 可执行文件仅增加 `com.apple.security.cs.allow-jit=true`；启动器和 Python 不增加例外 entitlement；库不附加进程权限。禁止 `get-task-allow`，不默认增加关闭库校验、任意未签名执行内存、DYLD 注入或 JIT callback allowlist。`allow-jit` 对应 Apple 的 MAP_JIT 机制，但它不证明当前 Node／Python 和扩展已兼容；真实签名后的 SDK、工具、标准库和扩展测试必须通过。兼容失败保持未通过并定位原因，不能自动扩大权限。[Apple silicon JIT 说明](https://developer.apple.com/documentation/apple-silicon/porting-just-in-time-compilers-to-apple-silicon)

## 无环的库存和封口顺序

1. 验证固定输入，在独占新 stage 完成源码、前端、运行时、离线 wheel、原生入口、plist、许可证和元数据组装。
2. 保存签前树快照，执行已登记的内层签名并实际验证。逐对象记录来源、签前／签后 SHA、角色、身份及工具结果。未登记的代码或资源变化立即拒绝；哈希映射本身不当作程序语义等价证明。
3. 只在签后差异被解释并验证后，调用原 schema 2 的 Pi manifest 生成器，保留固定版本和 worker，重算完整文件集合及 Node 入口 SHA。原输入 manifest 单独保留，禁止反写上游证据。
4. 在 Pi 目录之外写内部派生 provenance，区分上游 manifest／Python 库存和交付的签后文件。随后生成 Resources 库存，**显式排除且只排除库存文件自身**，避免重算已存在库存时形成自哈希。构建阶段核对精确文件集合、类型和内部链接；现有 `verify_inventory()` 只验证已列文件，不能声称它已经拒绝所有额外 Resources 文件。
5. 重跑现有 Pi 及 Resources 校验，最后签最外层 App，验证实际身份与签名。此后不再改写 Resources、Pi manifest 或内部 provenance。内部 provenance 不保存未来的公证成功、Job ID、最终 ZIP SHA 或最终回执 SHA。
6. 后续公证、附票和传输步骤重新核对受保护树及实际签名。发现被保护资源变化时拒绝，不刷新库存掩盖变化。数据哈希、symlink literal、扩展属性／resource fork 和平台签名证明分开记录；不全量清除 xattr 来制造通过。

外层 `Contents/_CodeSignature` 与 Resources 库存不互相哈希；Pi manifest 排除自身；派生 provenance 不回引 Resources 库存。最终回执在 ZIP 和 App 外，不形成 ZIP→回执→ZIP 的循环。

## 公证状态机和未知结果

外置私有阶段记录至少区分 `assembled`、`nested_signed`、`sealed`、`submission_ready`、`submit_attempted`、`submitted`、`submission_unknown_no_id`、`pending`、`accepted`、`invalid`、`stapled`、`final_verified`、`published`；失败状态保留原 stage 和对象，不覆盖旧交付。新 stage 为当前用户独占的 0700 目录，意图、原始工具日志和阶段记录为 0600 普通文件；拒绝链接或归属不符的状态路径。整包构建和解压前重新核对实际空间与余量，不借本设计绕过已经提出的大型验证容量要求。

提交前先独占提交锁并 fsync 意图，绑定发行 ID、profile／来源 SHA、实际提交 ZIP 的大小与 SHA。每个意图最多调用一次 submit，使用 `--no-wait --output-format json`；不使用 `--force` 绕过服务预检。取得有效响应后先持久化原 Job ID，再查询该 ID。CLI 返回 0、上传结束或没有错误日志均不等于 `Accepted`。

提交超时、进程退出、解析或落盘失败后，不删除 attempted 或重新上传。已经捕获可信原 Job ID 的，只查询原 ID；没有 ID 的保持 `submission_unknown_no_id` 并停止，不声称可以查询一个不存在的原 ID，也不把本地 nonce 当 Apple 幂等键。首版不自动扫描整个账号历史来猜匹配项。状态查询和日志仅针对已绑定 ID；查询失败保持 pending／unknown，不回到 submit。

`Invalid` 等明确拒绝保留原请求与诊断；修复后的新载荷需要新发行记录。正式上传前，必须已有实际签名对象、精确载荷清单、大小／SHA 与用户针对该 Apple 上传的明确授权。批准本设计或本地实现不自动授权上传。[Apple 自定义公证流程](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow)

本机只运行了 `notarytool submit --help` 文档读取：确认 `--no-wait`、JSON 输出及 keychain profile 可用；JSON 在操作结束时输出，因此没有假设提交一开始就一定取得 ID。没有读取钥匙串、选择身份或提交软件。

## 附票、最终 ZIP 和发布

仅在原请求实际 `Accepted` 后，对同一已封口 App 执行 staple 并验证票据，随后验证签名、Resources／Pi 库存和封口前后的资源闭包。ZIP 不能直接附票，提交 ZIP 与最终附票 ZIP 必须分别保存和记 SHA。[票据与 ZIP 的官方规则](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow)

最终 ZIP 从附票后的 App 新生成，保留包结构和必要元数据；检查路径安全、唯一成员、CRC、完整内容对应和文件大小／SHA。解压到新的隔离目录后再验证签名、票据、库存与原生启动；不靠最终文件名或压缩成功推断内容有效。macOS 分发策略检查和独立干净设备首启另有实际证据，工具预检不替代真实启动。

最终公开回执只含白名单事实：发行 ID、source／build-input／前端 authority、固定版本、上游及签后库存、签名计划与允许公开的身份、提交 ZIP／原 Job ID／接受证据、附票验证、最终 ZIP／成员检查和实际验收范围。原始工具日志保持本次私有目录；回执不含凭据或自身哈希。

只有 `final_verified` 后，同文件系统、无覆盖、独占发布完整交付目录并同步父目录；最终回执不得先于所有检查标为成功。用户数据、真实接口和工作台不参与这个发布事务。签名、公证、净机、IME、剪贴板和 VoiceOver 分别报告，不能用其中一个替代其他项。

## 代码边界及验证

拟新增 `scripts/packaging/release_pi_macos.py`，以及同目录下 `macos_release/` 的合同、库存／差异、签名、notary 与最终校验模块；它们 import 时不发现身份、不执行工具或联网。原 builder 提取可复用组装边界，preview 默认行为保持。新增原生 C 源、精确 entitlement plist 和 policy 放在 `packaging/macos/` 并纳入 authority。运行时 `resolve_pi()` 与 `verify_inventory()` 不增加签名豁免。

实现先验证这些行为：新增输入未冻结被拒；原输入不被签名改变；库存重算排 self；未登记 JS／Python 改动和 Pi 增删文件被拒；合法签名派生通过原 verifier；封口后资源变化被拒；错误身份／权限、签名失败、拒绝／未知公证不发布；恢复只查询原 ID；提交件和最终件不混淆；最终 ZIP 必须包含已验证的附票对象。模拟工具只验证流程，不能给出真实签名或公证通过结论。

本地阶段通过可注入的路径和 I/O 边界、受控解释器替身验证原生启动器的路径／参数／环境合同；日志、config、cache 和工作区均显式指向本次新建的私有 stage。测试只提供必要的干净环境和具名回环 fixture，不继承真实供应商／账号凭据，不访问真实 HOME 内的 Easel／Pi 配置、原日志、钥匙串或工作区；fixture 不可用时拒绝执行，不回落真实渠道。不改写 HOME 来模拟隔离，正式启动保留自然 HOME 的语义不变。

包内 SDK／工具的既有隔离测试可沿用其明确的合成路径合同；若某条真实默认启动或扩展路径不能完整隔离，留到获准的独立操作系统测试账号或净机中实际验证，不用替身结果关闭该门。原生默认首启、真实签名后的 Pi SDK／Python／Node／扩展和文件工具、签名／公证、Gatekeeper／下载隔离、IME／剪贴板／VoiceOver 与净机验收，仍在真实身份、环境及载荷批准后分别完成。

## 本次拟批准的范围

建议先批准上述设计及**本地代码实现、受控测试和独立审查**：不查询或使用用户的签名身份／私钥，不创建或读取公证凭据，不登录 Apple，不做 Developer ID 正式签名或上传，不修改当前工作台、旧包或业务数据。本地原生编译及新建合成小对象的开发期 ad-hoc 签名可用于工具适配测试，不能访问旧包，也不能作为正式发行成功证据。真实 Developer ID 签名与公证保留为后续明确身份、具体产物和授权后的验收步骤，完整发行目标继续保留。

Linux 原八例、AV、B2／B3、完整恢复、长期性能、第二真实产品和原生 UI 验收均按原交付总账继续；支付按用户决定延期且保持关闭。本设计不将正式发行准备缩成未签名交付完成。
