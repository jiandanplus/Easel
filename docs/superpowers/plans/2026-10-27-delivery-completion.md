# Easel 剩余交付总执行账

**Goal:** 完成 2026-10-27 进度汇报中的剩余开发与真实验收，提供可交付版本；保留订阅／自定义双渠道、原身份、历史和配置，不合并主分支。

**Authorization:** 用户明确要求“按照上面的回复，完成剩余部分的开发，在前端UI上不要出现解释说明的文字，直接产出可交付的版本”。此授权覆盖此前待确认的创作者任务体验设计及执行计划。前端文案以此新要求为准：只保留操作标签、真实状态、必要错误与用户决策所需的信息，不放开发说明、协议说明或实现细节。

**Architecture:** 延续当前三栏 React/TypeScript 工作台、本地 Python 服务、内置 Pi，以及 Logto／中央 PostgreSQL 的单一账户和钱包权威。现有 New API 保留为当前供应链；增加能力按各自合同和验证开放，不将配置界面等同于订阅接入。

## 不变量

- 在 `codex/ui-redesign` 开发，不回退、清理或覆盖既有未提交工作；本轮不自动 commit、push 或 merge。
- 用户登录、供应商配置、原对话和工作区保留；升级前备份并核对受保护状态。
- 未知提交只查原请求；不会为了界面成功重派模型、抹除账本、伪造用量或付款。
- 供应商及商户密钥不进入浏览器、源码、日志或分发包。
- 正式售价、退款商业规则、商户身份、第二个产品及签名身份依赖真实输入，不代为编造。
- 代码、受控协议、真实供应商、浏览器、包内运行、真实支付及生产恢复分开验收。
- 不将首批交付重新定义成整个目标完成。下列所有范围均保留，缺少证据时保持未完成。

## 当前状态摘要（2026-10-31，v35 与本机验收）

- **2026-11-03 最新中央候选完成 Linux 最小权限整链验收。** a6d555 候选与 33 份依赖实际离线安装，34 个 wheel 哈希完整匹配。原 full020/whole 在无网络/只读根/1CPU/1536MiB/PID128 隔离容器自然运行 142.851040 秒、退出 0/noOOM；实际 Root/app 最小角色摘要一致，app 精确 42501 和 owner 精确 23514/消息/公共及 control 库存不变均通过。6 次合成生成、12 个 dense blob 共 37,774,332 字节，合成 spent+12/reserved0；备份提交、OPEN 回收及新进程认证文件恢复 41,861,120 字节通过。29 份安全材料和公开 wheel/来源经独立有限审计 PASS。成功后定向清理 2 容器/1 镜像，全部 210 卷和旧失败保留，其他容器元数据及镜像 ID 不变。该结果不是新库导入、数据库 crash recovery、生产部署或全 T13/T14/B3/AV19/发行完成；原 17868 和桌面版本未切换。[实际报告](../../validation/2026-11-03-central-catalog-linux-minimum.md)。

- **2026-11-03 新中央服务候选 wheel 已生成并实际安装验收。** 标准隔离PEP517／固定Hatchling1.32.4离线构建，496,103字节／SHA `a6d5553286c69b3a62f8ae6d7f67f8a43b615b0cd2c0ef67bce13c45cb59e268`；140成员／139RECORD／20迁移完整核对，与旧c23候选仅两个目录修复源码与RECORD变化。新外部前缀实际80项pure＋原六组合49.34秒通过，同pytest进程来源固定新包，139分发文件前后不变；共享锁定依赖并非净机安装，新增29份派生pyc单列。独立包／公开回执审查PASS，未部署或切换桌面，`production_compatible=false`及其他交付门保持。[候选产物与报告](../../validation/2026-11-03-central-catalog-candidate.md)。

- **2026-11-03 当前备份源码实际回归。** 首次用例因测试目录继承 GID0 而在捕获前失败并保留；新 GID20 私有目录完成 attested_capture 1 项、facts／Logto 2 项及原 integrated 六组合。六组合实际 50.03 秒，136 份源码／SQL 前后哈希一致，覆盖真实 PG 捕获、认证加密与提交、新进程私有文件恢复；仅使用合成账号／媒体／点数。运行后原合成基库 public 关系0、测试活动连接0、原 PG 容器 running／noOOM／restart0。未执行新库导入／恢复后应用启动，不关闭 Linux 固定来源 factory、生产 B3／定时器、完整 T13/T14、AV19 或正式发行。[当前源码实际报告](../../validation/2026-11-03-backup-capture-current-source.md)。

- **2026-11-03 备份目录损坏输入修复。** 原目录／身份库列记录在空行或截断输入下会抛出 IndexError 等结构异常，现统一拒绝为 B2Error，并先验证身份库固定目录再提取库存。20 个反例实际 RED 后修复；61 项定向、65 项相邻、80 项含认证归档自检均通过。目录摘要算法与三个固定 pin 不变。此为当前源码修复，未打包或上线；旧冻结 Linux 证据不自动继承到新代码，sourcefacts／完整捕获／FWPD／DR 与发行门仍开放。[验证报告](../../validation/2026-11-03-backup-catalog-validation.md)。

- **2026-11-02 两条拆分取消路径实际验收通过。** 按用户选择保留旧200ms FAIL，新增C1派发前过期/零HTTP与C2已进入GET后原deadline取消；实际最小角色/PG/原worker、心跳与租约、资金/存储预留及精确辅助许可审计均通过，37安全文件独审PASS。网络为MockTransport，不能当真实供应商连接验收。修订family映射19/38（原17＋新2，AV19仍开）；原37历史账仍17/37。3成功容器和1专用镜像已清理，209卷及旧失败现场保留。[实际报告](../../validation/2026-11-02-media-cancellation-functional.md)。

- **2026-11-02 三种文件写入场景最小权限实际通过。** 原 incomplete／fresh／promote 三节点分别首次运行，真实 worker／PG／文件断言及资金预算 oracle 通过；47份安全材料经独立一致性审计。历史17/37不变，覆盖由14最小＋3宽权限升级为17最小＋0宽权限；旧200ms失败和新两条取消路径独立保留。4个成功测试容器及1专用镜像已定向清理，205卷保留；私有SQL与最终发布调用时序的证据边界见[实际报告](../../validation/2026-11-02-physical3-minimum-runtime.md)。

- **最新：2026-11-02 PostgreSQL16 组件九场景实际通过。** 固定公开源码的专用 Linux ARM64 镜像已构建；单次无网络／非root／只读根运行实际 exit0、无OOM、无重启。原快照完整清单一致、新快照可见提交、ReadPolicy、真实25006／25006／22023、序列变化拒绝及异常 keeper 清理均通过。26项执行器纯／模拟测试和独立代码审查通过；15份实际安全材料经独立严格重放／原180秒同boot和终态发布链复核通过。成功容器已定向清理、202卷保留。该组件使用明确的测试专用Unix socket适配，不关闭生产root／Docker／socket／sourcefacts／capture／factory／FWPD／完整DR或其他发行门。[实际验证](../../validation/2026-11-02-f2-pg-component.md)。

- **当前：2026-11-02 原始 Linux 进程八场景实际通过。** V2校验分别验证实际规范路径与argv0，并增加固定二进制及别名文件身份检查；保留全部原非init约束、三份载荷和原八项断言。新容器实际1.448秒exit0／noOOM，完整执行器accepted=true，三帧／同进程前后证明／输出hash／fresh终态／原期限围栏均通过。4文件实际安全证据已归档。这关闭进程八场景子门；PG组件由上述最新结果单独关闭，sourcefacts／FWPD／完整DR及其他发行门仍未完成。[实际报告](../../../outputs/_run/f2-native-init-alias-20261102/ACTUAL-V2-REPORT.md)。

- **2026-11-02 初始化诊断阶段（历史）：** 新身份探针经31项纯／模拟测试及独审，在全新隔离容器实际完成一次。实际规范路径为 `/usr/sbin/docker-init`，argv0为 `/sbin/docker-init`，两个固定路径的文件身份／只读／root安全模式相同，二进制为aarch64。该阶段确认原字符串假设的根因；其后V2实际结果见上条，探针本身不计为八场景。[证据](../../../outputs/_run/f2-native-init-alias-20261102/REPORT.md)。

- **2026-11-02 真实 Linux 接入更新：** 一次性接入版18项纯回归及独立复核通过，随后实际运行一次。镜像映射、容器完整参数、从未启动状态及真实manifest描述符通过；容器唯一启动后0.138秒exit1／noOOM，未取得原八场景结果，未重试。后续R3前置诊断及主机集成经纯回归和独审，在新容器实际运行一次，诊断exit0且严格报告有效，定位原运行时 `init_prefix:153` 的PID 1可执行路径成员检查拒绝。当前原生目标身份仍未记录，尚未修改白名单或运行原八场景。归档4份公开安全材料，原始守护日志未导出。[实际记录](../../validation/2026-11-02-f2-native-manifest-once.md)。

- **2026-11-02 原生与 Linux 更新：** 旁白四项及恢复关闭已获用户手动确认，但同一测试页无复制文档GET、按钮仍idle／焦点composer，人工报告与受控页面关联不充分，完整旁白验收仍未关闭；专用服务实际exit0并保留[续验记录](../../../outputs/_run/native-voiceover-actual-20261102/REPORT.md)。主机空间恢复后，固定官方 Python3.12.13 ARM64 镜像已实际下载；当前Docker/API1.49以manifest摘要表示Id，旧配置摘要门禁拒绝。已取得本地manifest→config原始字节证明，新增纯映射模块Root fresh8项通过，独立有限复审无阻断，实际API1.49镜像只读映射通过；一次性执行器集成待完成。此8项不是原Linux进程八场景，尚未创建／启动测试容器，不放宽旧运行时断言。[获取与诊断](../../../outputs/_run/f2-native-acquisition-20261102/REPORT.md)。

- **2026-11-01 原生交互更新：** [系统输入验收](../../validation/2026-11-01-native-input-verification.md)记录 v36 合成页面的系统剪贴板用户手动3／3通过，助手和交付文案换行保留；剪贴板自动化原生0／3未验证。新增当前macOS／Chrome系统拼音的原生OS按键自动化用例：组合Return不误发、Space中文候选和Shift+Return两行保持、正控只产生1条精确公开请求；模型及业务受理0。原人工输入报告与当时空框未对应，仍为inconclusive，不与自动化混算。21文件证据冻结并独审，IME临时Tab／服务正常退出；原17868未切换。VoiceOver仍未完成。

- [macOS v36 增量包](../../validation/2026-10-31-workbench-v36-delivery.md)已离线构建，复制回复缺失API不再虚报成功；完整前端589／0、新包静态10组通过。ZIP129751204B／d618b5c3…78fb7bb。原17868仍v35：发现旧停止保护不能证明完整空闲，切换须用户明确确认当前无任务；本轮未修改真实工作区或账号。

- 最新媒体原节点17／37（14正式最小权限＋3旧宽fixture），剩20＝slow取消1＋AV19；额外维护2、固定合成Logto R6恢复／whole另计。见[覆盖账本](../../validation/2026-10-31-media-backup-b2-family-coverage.md)。全T13／T14／B2／生产B3未关闭。
- [前端文案与交付身份](../../validation/2026-10-31-front-ui-copy-and-identity.md)只读核对未发现应删除的长说明；100份source、13份包内输出及11个已定义静态HTTP路由匹配。原生交互门未据此关闭，没有为凑修改而改变UI。
- 已找到B2不覆盖的F2原Linux进程8-case缺口；首次外层调用在API前退出，reviewed002仅创建容器；新固定CID首次启动续接已执行，exit1／noOOM／未超时，原8项未验收通过。空间经已授权的公开成功builder清理恢复；当前只诊断该公开合成载体的启动失败，不重跑容器，详见[本机实际状态](../../validation/2026-10-31-f2-local-process.md)。不借用旧154／AV授权、不重跑旧main，不改原8个场景或期限。

以下v35与更早条目保留各自批次证据；不将旧“正在开发”措辞覆盖以上最新计数。

- **2026-10-31 B2 本地集成更新：** [中央服务候选与验收](../../validation/2026-10-31-media-backup-b2-runtime-integration.md)记录了已独审关闭的两个 SQL 缺陷和两个测试有效性缺口。统一379文件不漂移，legacy30为580PASS／0skip（含7个实际PiSDK）、producer72PASS、源码whole6PASS、源码外安装current020 whole6PASS及frozen016 whole4PASS。两份中央候选wheel及17份安全证据已保存，不替代Mac桌面v35、不部署生产。33份Linux依赖文件已闭合，真实Linux/root-app/资源故障harness在编写；T13/T14/B3、AV、永久工作区／签名／净机验收等完整范围保留，不因本地绿色而标记整体完成。

- **当前桌面权威为 [macOS v35](../../validation/2026-10-30-workbench-v35-delivery.md)**，已在原17868／原工作区激活。ZIP129,751,187字节，SHA256 `984cc3060091a00babcfdd6f90205e5b92ab698a24adf3ae7321797ffec975d9`。相对v34仅三个后端来源变化，每次local_status共享15秒预算；R1 DNS清理P2已修复并独审关闭，最终143项回归通过。10组静态包校验、31文件闭集复核、150份现场保护文件／账号语义／历史／自定义配置保全通过；钱包在after对比保持，终检未重读。实际原用户卡片只读重试恢复已登录。没有新模型发送／资金写入／前端文字／提交推送合并。v34及更早摘要均保留为历史，不替代当前v35。

- **B2 本地开发与隔离验证已获用户明确批准**，同一R3设计SHA `f9614a317705fd25663bd942e847759910a8d1c4fcf9c9a70e9107b8d8603cff` 保持。完整[15任务计划](2026-10-30-media-backup-b2.md)已启动，范围包括维护控制／全部媒体写入和GC屏障、真实导出快照生产器、v2流、独立锚点、落盘自验及wheel。当前依赖T0、流与legacy已形成独立验证；durable／reader及维护控制正在复审，不称完整B2完成。没有生产维护／定时器／真实密钥和业务数据读取／模型调用。AV第四载荷和永久工作区迁移仍独立pending，支付继续延期。

- **上一桌面版本为 [macOS v34](../../validation/2026-10-30-workbench-v34-delivery.md)**，已在原17868／原工作区激活。ZIP129,749,024字节，SHA256 `4baed43e680341f73a57a728bc9600ab13684ba9a05f99066a268f6a5e53742c`。相对v33仅六个前端组件变化：Sidebar读取30秒截止／受保护未知态恢复，以及五文件六处文案收短；通用API、backend、90秒任务恢复不改。完整前端585／0、定向账号63／0（重叠不累加）、TypeScript／Vite构建及独立包23项核验通过。六个纯合成浏览器案例＋原页面实际卡片／设置导航已验，未发真实模型。新备份887普通文件、15,539,620字节；本次150保护文件在R2after全部原SHA，账号／历史／自定义配置／scope／钱包相同。UI后终检只有正常token／expiry字节刷新，账号语义相同；终检不重读钱包。R1在停止旧实例前因10秒状态GET超时退出，11.238秒只读成功后R2将该项等待改30秒，再审／新备份／认证切换成功，原失败保留。未签名／公证或净机验收，不把本增量当完整目标完成；以下v33及更早数据是历史快照。

- **上一桌面版本为[macOS v33](../../validation/2026-10-29-workbench-v33-delivery.md)**，已在原17868、原工作区激活；ZIP为129,749,097字节，SHA256 `6af10003c9b176ae9fb8ecbf03ba77d0a66e20a2dcf1fed420f121d82b91a195`，未签名／未公证。573项完整前端回归、17项定向媒体／认证预算验证与构建通过；实际GUI完整状态查询显示同一任务两行“已结算／结果可用”，关闭[v32的20秒总预算缺口](../../validation/2026-10-29-workbench-v32-delivery.md)，原失败过程仍保留。原174份保护文件、账号语义、历史和自定义配置保全；只读查询后钱包及55条观察记录不变，可用6.931866／预留1.009728点、pending2／active0。本轮0.002948点真实Pi＋DeepSeek验收是在升级前v31执行的单次UI任务、1 invocation／2 operation，1326＋1622microcredits，不能写成v33新发模型；后续切换／查询无新增真实模型发送，也未回填旧真实回复的artifact metadata。下面较早摘要及历史日期条目保留当时证据，该段仅为v33历史收口，当前版本以v34增量为准。
- AV最新已批准的6文件Linux运行已越过可见挂载collector，随后在ROOT可写后代策略门拒绝，尚未进入GPG／提取／编译，未自动重跑；ROOT分类候选370项本地验证及独审通过，第四份具体载荷仍待单独批准。既有上游RSA来源证明、算法重放和离线工具通过均不能替代失败记录或完整AV验收，其他未完成门保持。

- [AV源码准备工具](../../validation/2026-10-29-av-source-tooling.md)新增归档、原始Git对象链、v0协议与固定HTTP获取四模块；独审发现的五／二／三类问题均经TDD修复与限定复审闭合。root最终六文件组合230 passed／0 failed、3.12秒，10个实现／测试输入前后SHA相同，包含两次真实本机Git严格导入／完整合成树回放。154只读核实FFmpeg实际10525个GNU普通头和工具资源，修正tar头metadata从不足的4MiB为8MiB；未上传新工具、提取真实源码、在线取pack、编译或部署。Linux OS／网络隔离、完整真实对象／签名绑定、依赖和构建门仍未过，不能由230离线通过替代。

- AV r2已完成明确授权后的固定Linux隔离算法重放14／3／3；普通 `6.1.1-3ubuntu5` 的安全修复级别仍不合格，150包实际安装未执行。[9.0.2上游RSA归档认证](../../validation/2026-10-28-av-upstream-rsa-source.md)实际通过，旧ED过期结果保留。[公开构建输入锁定](../../validation/2026-10-28-av-build-input-locks.md)现已补官方Ubuntu OCI index／amd64 manifest／2052字节config链；实际daemon只读确认cgroup2／限制能力。签名Noble子集证明libplacebo／Vulkan头包过旧，已研究官方libplacebo7.387.0＋配对Headers1.4.345，并实际验标签GPG及三负控。完整源码树、递归依赖／镜像layer、编译／profile／实际API运行未验；没有拉镜像、安装、编译或部署。
- [F2-r3独立复审](../../validation/2026-10-28-restore-capture-f2-r3.md)限定本地源码／合成合同及本机进程测试PASS；实际Linux／PG25006／source facts／WPD和新成功真实DR仍未验，旧mixed DR仍failed。AV Linux不能补F2 Linux证据。
- composer开发源码的实际CUA／合成后台双路径及[A2内容指纹修复](../../validation/2026-10-28-workbench-query-hash.md)已验证；[A1订阅引导分类修复](../../validation/2026-10-28-subscription-onboarding-fix.md)541前端／42后端／tsc及独立limited PASS后，已进入[macOS v31](../../validation/2026-10-28-workbench-v31-delivery.md)。v31完整库存、包内运行、匿名订阅合同、localhost-only实际工具闭环和JPEG／WEBP通过；在原17868激活时159保护文件exact、原workspace／source scope及钱包保全。本人重新登录后，[v31真实验收](../../validation/2026-10-28-workbench-v31-live.md)完成一次UI提交、一个Pi invocation、两笔已结算模型执行、18字节文件及下载验证，实际扣0.003300点，余额6.934814，原pending2／预留1.009728点保留。刷新后登录、结果、链接和未发送草稿保持，无自动设置／画像弹窗；未重试旧请求。旧中央2468／7skip绑定旧App，不是新前端／包的全树快照。以下历史条目保留当时状态，当前以本摘要及末尾收口为准。
- [实际草稿迁移预检](../../validation/2026-10-28-workspace-migration-preflight.md)只证明真实v29 store／hook在合成scope／origin间的隔离与返回后保留，taskPOST0；不等于真实迁移。既有默认目录只读lstat已存在，内容未读未改。[正式工作区接续设计](../specs/2026-10-28-managed-workspace-continuity-design.md)经两轮独立审读闭合原2P2，待人类选择；推荐新永久legacy子目录＋保留17868＋一次性草稿恢复，零实现／真实迁移。支付、第二产品、签名和原生净机等边界不变。

## 交付账

| 工作流 | 实施与证据 | 状态 |
| --- | --- | --- |
| 1. 任务体验 A1/A2 | 统一版本／来源就绪判断、持久 accepted／rejection 决定、原轮查询与撤回、停止确认、刷新恢复；修复模型目录后台刷新失效；定向回归与 v20 真实 Pi 文件任务／扣点通过 | 已随 macOS v21 交付；不是全部目标完成 |
| 2. 草稿与导航 B | 按工作区、账号来源、会话保存文字和附件；revision 安全清理；附件归属验证；浏览器实测附件刷新及两份未发送草稿；URL 往返与输入焦点恢复；当前Mac／Chrome系统拼音原生OS按键、零误发与单请求正控已验 | 代码与本机浏览器已交付；IME技术用例通过，人工先前输入报告仍未对应，不扩展为全部环境或目标用户通过 |
| 3. 成品与首页 C/D/E | 实际文档复制接口、独立下载、来源与带文件继续修改；原生模态预览；首页输入优先；实际 333 CSS 像素窄屏和键盘验证；真实文档下载 hash 一致；v36合成页面系统剪贴板用户手动3／3、两项换行保留 | C/D 本机交付；系统剪贴板人工验收完成，自动化原生未验证；E 的 VoiceOver／目标用户仍未完 |
| 4. 商业账号／支付 | 账号邮件恢复投递；商户安全配置；真实套餐条款；微信／支付宝实际支付、到账、幂等回调及退款；禁用未验收能力 | 用户明确暂不配置支付，保留实现并保持关闭；邮件流程单独核对 |
| 5. 订阅媒体与云执行 | M1／016与桌面图片链有真实验收；M2／M3／Incho仅中央候选。AV r2本机独立复审通过，已获明确授权的固定Ubuntu6.1隔离重放完成14正例／3原负例／3短rate输入门；证据见[AV r2 Linux](../../validation/2026-10-28-av-native-r2-linux.md)。冻结中央完整回归2468 passed／0 failed／7 skipped，绑定当时旧App | 算法兼容及本机全量通过；普通6.1.1-3ubuntu5与USN修复级别存在安全补丁差距，150包安装未执行。9.0.2 exact archive已通过固定上游RSA签名，尚未编译、解依赖或验全profile；cleanenv／API UID／systemd仍未放行。017–020未生产部署，视频／音乐／声音无真实供应商验收，缺渠道或费率保持closed；旧Flare预留与actual=null记录保留 |
| 6. 第二个真实产品 | 独立产品会话及权限、共用 SSO／钱包、跨产品并发与明细；不以合成第二产品替代 | 等待产品位置 |
| 7. 发行 | [macOS15+arm64 v33](../../validation/2026-10-29-workbench-v33-delivery.md)已构建／原17868激活，ZIP129,749,097字节／SHA6af10003…；15402资源／904公开source／60inputs，174保护文件、原workspace／登录／历史／配置保全。573完整前端及17定向媒体／认证预算验证通过，完整GUI状态查询成功；继承v32实际匿名App／UI准确产物卡证据。本轮真实Pi任务是在v31单次UI发送／两结算，共2948microcredits，后续不重发 | 本机未签名包、文本工具及完整恢复状态路径已交付；原生剪贴板／IME／VoiceOver、永久工作区接续、签名／公证、独立净机及发行渠道仍未完成。旧v27及Flare未知不重放，Windows／Linux随后发布 |
| 8. 运营与性能 | [B1本地文件归档／恢复](../../validation/2026-10-29-media-backup-b1-delivery.md)新增229项联合验证及33／34MiB实际磁盘实验，独审原P2修复后通过；R2双库／初始M1证据保留。F1-r2／F2-r3冻结与本机合同已[归档](../../validation/2026-10-28-restore-capture-f2-r3.md) | B1不接生产timer／加密／真实DB；B2／B3仍待。旧真实mixed DR仍failed、未重跑；F2仍approved=false／actions=[]，实际Linux waitid／PG25006／source facts／capture／WPD未验，不能借AV结果。原B单独失败仍为非阻断，不称所有probe绿；媒体日常备份、异地／整机／容量／两小时压力及多节点未完成 |

## 本批实施顺序与所有权

1. 后端任务契约：`web/app.py`、独立 `easel/workbench*.py` 及任务契约／回执测试。接受持久化后发可校验回执，同 ID 不同内容拒绝，查原轮不派发。
2. 前端纯状态模块：`workbenchReadiness.ts`、`taskLifecycle.ts`、`composerDrafts.ts`、`workbenchNavigation.ts` 与实质行为测试。
3. 成品交付：`DeliverableActions`、OutputsPage、ArtifactGallery、文档读取／下载和键盘模态行为。
4. 主集成：App、ChatPage、Dashboard、API 桥与模型选择器。保持原三栏和所有权边界，新增状态用简短文案。
5. 独立审查、相关回归和浏览器实际任务，再构建并启动匹配包。真实费用请求保持原授权和原请求保护。
6. 持续推进工作流 4–8；运行环境和用户业务输入未具备的门槛保持显式，不以未验证结果完成总目标。

每个实现先新增失败行为测试，确认失败原因再实现，随后跑对应回归。已有详细步骤沿用 [创作者任务体验实施计划](2026-10-26-creator-task-flow-rollout.md)。先前的 v18 查询只是 A2 子集，本批不将其当作完整 accepted／草稿交付。

## 当前证据与待补输入

- 基线：中央 release `20261026-reconciliation-r2-v1`；桌面 v18；总目标开始时中央 API、Console 健康且 17868 返回 HTTP 200。
- 已询问正式套餐条款和商户私有配置路径、第二个真实产品位置及首批发行平台。没有请求任何密码或密钥。
- 用户后续明确：支付“先保留，暂不配置”；“先交付 macOS，其他平台随后发布”。真实收款不属于本次可上线验收，支付继续 disabled；不以虚假套餐或模拟付款冒充正式收款。第二个实际产品尚未指定，现有跨产品合同与隔离保持。
- 各工作流完成后在本表追加实际命令、收据、包和验证报告，不只用测试计数标记完成。
- 当前桌面版本为[macOS v33包与完整恢复查询验收](../../validation/2026-10-29-workbench-v33-delivery.md)，ZIP129,749,097字节／SHA256 `6af10003c9b176ae9fb8ecbf03ba77d0a66e20a2dcf1fed420f121d82b91a195`；原17868、原workspace与账号语义保持，174保护文件无变化。此次重新登录后的真实Pi＋DeepSeek任务在当时v31执行，仅一次UI发送、一个invocation、两笔1326＋1622microcredits结算，25字节文件及下载已核对；v32／v33没有为恢复查询重发模型。v33实际GUI两行已结算／结果可用，0.001326＋0.001622点；最终只读查询后钱包及55条观察记录不变，可用6.931866／预留1.009728点、active0／pending2（上限3）。原Flare1点及v27失败0.009728点仍未知；旧版本、失败证据与旧包全部保留，不重放、不免单。2026-10-28的3300microcredits任务是另一次历史验收，保留于原日期段落，不与本次合并。
- 历史桌面交付证据：[macOS v21 工作台验证](../../validation/2026-10-27-workbench-delivery.md)、安全 JSON 同名 `-safe.json`；438 前端、113 入口／打包／旧配置、63 指定实际包内 SDK 定向均通过。真实文本一轮 Agent 的两次模型请求共 1,189 microcredits，当时active／pending=0；真实请求在 v20，v21 仅新安装入口及账号短文案增量，已验证历史成品保留。该历史状态不替代当前钱包与版本。
- M1 最新快照与原媒体复审边界分别见 [图片／存储验证](../../validation/2026-10-27-subscription-images-storage-delivery.md)；不能用被后续修复替代的 176 定向结果或最初 94 独立审查冒充最终全中央回归。全量曾因专用 tmpfs PG 容器 OOM 中断，旧证据和容器保留；已为新全量建立独立磁盘卷 PG，不影响用户原 Docker 应用。
- 运维安全证据见 [运营与恢复记录](../../validation/2026-10-27-operations-readiness.md)及[真实R2恢复safe回执](../../../outputs/_run/operations-readiness-20261027/evidence/restore-v6-safe.json)：nonce `1d69fabc6ee045b4a7538d056f4264c4`、plan SHA `f29c85d968aa7f750301adcfc910c4f69f907ff9711e06d1fbdc41df6119743c`、receipt SHA `0ac8db04128ff78f3de014693faf55cd77d47b2265b0e66bf4998012c2c42161`。实际171秒完成双库严格恢复，两个新目标停止保留，源两PG IDs／镜像与原四服务、Logto及旧备份timer独立复核正常。仅证明本轮冻结R2数据库范围；不能外推M1／016、媒体blob、跨库原子性、PITR或整机恢复。性能／两小时验证未运行。
- M1 最终独立复审：[第二轮存储审查](../../validation/2026-10-27-media-storage-independent-review.md)；1268 passed／1 skipped，退出 0，263.80 秒，冻结七实现 SHA 前后相同。唯一 skip 为原显式 Docker 合成恢复开关；不能说该轮包含它。新服务 wheel SHA256 `b070d12f7c48f65709ebdfb295a76a2e1e45b79b612404893e5e541f00d1dfd9`，73 个 py／SQL 与工作区一致；被替代的旧 176 定向／旧 wheel 不作发布依据。

## 2026-10-27：用户新增 New API 生图模型（当前优先事项）

- 重新从服务器业务渠道读取，实际目录为 deepseek-flash、deepseek-v4-pro、gpt-image-2.5-flare、gpt-image-2.5-sunburst。文本渠道保持。两款图片各一次独立真实 POST；原图分别为 1024×1024 PNG，下载、全解码、视觉核对通过。响应没有 object ID 或 usage，网关关联头为 X-Oneapi-Request-Id。直接探针未写中央钱包。证据见 [真实协议记录](../../validation/2026-10-27-newapi-image-discovery.md)与 outputs/_run/newapi-image-sync-20261027/evidence/manifest.json。
- 用户将体验定价和使用范围交由项目方决定。确定每款 1 点／实际验证交付图片，仅原体验 grant 附加 image；不发点、不延期、不改并发、不修改原 plan/grant/lot/账本，正式支付仍 disabled。
- M1.1 的 typed HTTP 关联、无 usage 按验证交付图片计量、非资金权益扩展、严格运行角色权限和真实 1024×1024 尺寸比对已随 f50e… 服务发布。生产仍为 `20261027-images-m1-1-size-v2`，仅到 016；旧 02626 stage、旧测试及旧包保留为历史，不作为当前发布依据。
- M4 的图片 host/client、持久单轮一次生成、未知只查原请求、短期凭据仅 host、独占输出和账号／停止隔离已交付。M5 两款真实图片及钱包收据见 [实际图片交付](../../validation/2026-10-27-newapi-image-workbench-delivery.md)。每笔仅一次供应商尝试，各扣 1 点；两个成功回合含文本共 2.011003 点，没有重放旧失败任务。
- 首次 Flare 在受限服务媒体目录遇到 EROFS。已按精确受审计划仅扩展 API 媒体目录写权限，其他配置和资金不动；旧任务仍 pending、actual=null、预留 1 点。原三笔 GLM 的平台承担授权不包含它。新独立 Flare 验收成功不能解释为旧异常已解决。
- v23 已交付真实图片，v24 在此基础上仅更新前端组件文案并重新打包，原登录、历史及自定义配置均保留。v24 新 UI 的账号与模型目录仅只读验证，没有新增真实生图。签名、公证、干净设备、原始上游 Python archive SHA 和原生交互证据仍未完。
- 当前 M2 冻结实现为 14 文件 `abb2e0efe34956c834d4a1aa744934114a4c9d6939a73e033a716c2b15411da8`，完整 212 输入为 `223a4c82831216673c4096066223ae64d2911e2e0a4dd3b1de7469cb65a0e5f6`。最终全中央 1710 passed／7 skipped、350.69 秒，前后全部输入一致；独立 PG／全媒体／权限及原两 P1 反例复审 404 passed／1 显式 Docker 恢复 skip、88.51 秒，26 个闭包输入前后相同，限定 M2 候选无已知阻断。两次测试证据分别记载，不累加。旧 9798／1702 次运行保留。该候选没有上传或部署至生产，也没有实际视频／音乐供应商验收；详见 [M2 候选验证](../../validation/2026-10-27-subscription-media-m2-delivery.md)。
- M3 r2 最终证据见 [完整中央验证](../../validation/2026-10-28-m3-r2-central-final.md)及[安全收据](../../validation/2026-10-28-m3-r2-central-final.safe.json)：唯一会话 77597，1794 passed／7 skipped／0 failed／0 errors，316.31 秒；423 输入前后逐 SHA 一致，时钟连续、无休眠中断。独立 [r2 定向复审](/private/tmp/easel-m3-independent-review-r2-20261028/review.md)另为 8 passed／5.44 秒，28 个候选文件及 17 个保护迁移匹配，结论限定本地候选 APPROVE，不与全量计数累加。7 项 skip 的节点及原因逐项保留在最终报告，均为需显式恢复环境或 owned source handle 的测试，不算恢复验收。原[休眠中断全量失败](../../validation/2026-10-28-m3-r2-central-full.md)、[显式迁移 expected 缺 019 的两项失败](../../validation/2026-10-28-m3-r2-central-full-awake.md)和旧 229／243／247 收据全部保留。017／019 仍未部署；真实渠道与费率缺失的能力、缺安全素材 producer 的旧 DashScope enrollment 均保持 closed。
- 新混合恢复候选绑定 DR `35068d9fb841598f50c59e6ec2acaab5792a598096055516b4c37950ff28424a` 和 wrapper `8d77536b5afa9207ba5268f4cdb904fbe14de181fdaf5667e8306ce69c457e9c`；mount-r1 独立复审通过，本地完整合成 82 passed／0 skipped。82 项通过仅证明本地候选。其后真实 mixed DR 在最后 source 复核失败，当前结果为 failed、待诊断，不能用本地通过或此前 R2 恢复成功替代；models 已恢复，两款图片可用，钱包及旧 pending 字段不变。失败记录、密文和恢复目标全部保留，未重跑；原定时备份范围没有因此自动包含新 blob，32 MiB 演练上限也不代表大媒体常规备份能力。

## 2026-10-28：新增候选与真实验收边界

- [中央M3冻结wheel](../../validation/2026-10-28-m3-r2-wheel-packaging.md)已从423输入tar独立构建，SHA `936b7cb181b60a9fae9c0b9f868ed8ea0a1df081d2f51d8b77ed865333be54f1`；94个Python／SQL与冻结源相同，Console静态／许可证补充来源单列，未混入Incho。只做本地安装及1秒WAV全解码；Linux目标仍缺ffmpeg／ffprobe，生产未迁移。
- [Incho本地候选](../../validation/2026-10-28-newapi-incho-local-delivery.md)新增精确native协议和020，001–019保持原字节；346项相关定向与另16项strict runner通过，不累加。独立复审53项通过，16候选前后与tar一致。现有channel7只含v3.5／v4.5，缺incho_music ability，三者无model_mapping；固定插件1.0.2只支持v3.5／v4.0，不降级v4.5。未设新费率、未启用、未真实生成；全中央新候选、Linux工具及真实渠道验收仍未完成。
- mixed DR诊断增强已本地加入六子阶段、受限私有异常定位，并修复辅助failure-safe写失败替换原guard的既有错误；作者96 passed／1明确Docker skip，独立最后5 passed。最终DR SHA `34df13489773a3e3bf8c28601e634546d494012923dde54b0bd76e792581271e`／wrapper `c5ee12bd34b5b48a09b002382bfc9c1762a8cd6d32aa4d343d6709840b931d36`，123个require AST与顺序保持；只有新nonce与当前窗口新精确计划可执行，未重跑生产，旧failed仍failed。
- v27真实文本失败已证实仅1次provider attempt、HTTP400；现有文本HTTP关联缺X-Oneapi-Request-Id fallback，中央request_id在网关精确匹配为0。可观测性补丁独立开发，不当作400根因已解决。自动审批最初拒绝共享日志时间窗查询，原命令未执行；用户随后明确批准限定诊断，服务器内仅匹配1条错误，导出固定类别／公开参数名／ID，未导出正文、提示词、账号或密钥。已确认create_media工具schema缺少根object类型，与worker多模型Type.Union的无type输出吻合；修复与实际SDK验证正在开发，尚未真实复验。保持actual=null与预留，不伪造退款或重放请求。

- 新完整中央候选为 [M3／Incho／关联头验证](../../validation/2026-10-28-central-m3-incho-header-final.md)：唯一full8911为1895 passed／14 skipped／0 failed，352.26秒；431输入前后一致。因缺payload新增SDK7跳过后单独用同身份Pi payload补跑92805为7 passed／0 skipped，3.16秒，另冻结434源码及11,932文件库存。两次不合计成单次full，原7恢复环境skip仍未覆盖；此证据也不包括后续worker schema修复。
- 上述431输入的新中央交付包已完成，本地wheel SHA `e7af8e66cc9a34deb92b47b5bc6f22f6d7406e0e510d3199b9a768efae3f8487`，包含001–020；96个冻结Python／SQL及4个明确来源的静态／许可证文件已核对，103个ZIP成员CRC通过。完整材料已复制保留于 `outputs/_run/central-platform-20261028-m3-incho/`，交付tar SHA `bb638bb7c38789bcaa33ca685d87a83c5b84f6a0a627f90239ce16ba28942294`。这是本地候选，未部署；Linux依赖／AV沙箱、实际数据库预检、新冻结维护窗口及真实恢复仍需完成，不能复用已失效的钱包窗口。
- Python来源调查已独立验证两官方20260510固定archive，当前旧base有1649／1650非bytecode项可精确派生；旧libpython仅签名结构未完整解释，新离线uv副本组件校验有效。v27原false保留，后续v28将从已验官方stripped+固定uv0.11.16于新inputs重建，26wheel不升级、旧包不覆；当前仅按方案实施，未宣称新来源链或新包已完成。
- 历史阻挡记录：[DeepSeek schema修复／v28交付状态](../../validation/2026-10-28-deepseek-schema-fix-delivery.md)早期阶段，worker根type修复及来源／COW代码审查已通过，但本机当时仅159MiB，v28 newPi／dist／freeze／App／ZIP／fresh backup和激活均未执行，仍运行v27。11:54:25核钱包可用6.942425／预留1.009728／已用2.047847点、pending2。该blocked safe保留不追改；空间随后恢复才执行下面的实际v28／v29交付，不将历史状态写作当前阻挡。
- 后续真实状态更新：[v28实际验证](../../validation/2026-10-28-workbench-v28-delivery.md)。空间恢复至约2.1GiB后完成新包，ZIP SHA `421dad0981b0d3383915ac8e0f00ac5333aeb92cabb2a532d1a86c53ba31b81a`；PID70171运行原workspace，146项保护文件字节exact、完整账号及钱包在升级时相同。新session `mvrlf6j53cn0u9`／turn `ba8ce3ee-17d5-4e52-8747-d6d7c7565a02` 只有一次UI提交，真实write_file和下载均18bytes及相同SHA，done／clean_end=true，两exec settled各1585／1331，共0.002916点；现可用6.939509／预留1.009728／已用2.050763，active0／pending2。原v27与Flareunknown不变。随后未发送草稿复现了完成后模型标签与就绪分叉，正补已验证来源内存provenance，尚未将连续任务体验标记通过。先前blocked safe是历史，不再代表当前包状态。
- [154 Linux依赖准备](../../validation/2026-10-28-central-linux-preparation.md)已在root私有独立目录完成：真实Linux marker下26项冻结闭包、uv.lock／PyPI asset双SHA和tag、离线安装／pip check、新候选100payload／8导入／PNG全解码均通过，大wheelhouse留服务器。没有生产部署／PG／模型／资金动作；ffmpeg／ffprobe未安装、实际systemd AV沙箱及新schema／ACL／fresh DR门槛仍在。
- [v29连续输入实际验证](../../validation/2026-10-28-workbench-v29-delivery.md)为当前工作台版本：ZIP SHA `f063ee9c203c03531c05c561bd49a2357aec7e2ddcc3f75dd5807621e7eec536`，PID97261。155项保护文件字节exact，原账号／接口配置／历史／钱包升级保全。新增turn `af5c71da-d1c6-444e-a028-2477d5af7de4` 独立文本回复精确，单exec settled1395micro；随后未重选模型的非空草稿实际可发送，证据后清空未提交。现可用6.938114／预留1.009728／已用2.052158点，active0／pending2。原unknown保留，当前未新增commit／push／merge，总目标仍未完成。
- [154 AV独立准备](../../validation/2026-10-28-central-av-preparation.md)完成3份官方签名InRelease／12索引／233deb共145,446,530bytes与216实际ELF归属核对；私有loader、无网络及严格资源限制下四种合成格式全解码。**当前仍有两个已实证门槛**：冻结clean-env直接ELF缺libavdevice退出127；500ms音频完整8000样本对应progress却384／470.938／493.5ms，旧候选用progress端点作实际时长会拒绝合法素材或偏短计量。来源准备不证明AVTools／实际APIUID／systemd可用，没有放宽容差或安装生产。计量最小修复及只读安装计划继续推进，新M2／M3仍不得投产。
- [新恢复F1-r2](../../validation/2026-10-28-restore-freeze-f1.md)已获APPROVE_F1_CONTRACT_ONLY：作者320项、原21及新增28项独立边界分别通过，补完整真实enabled观察态／25计数／八组跨表绑定。原69绿后发现六个应拒却接受反例的REQUEST_CHANGES及旧tar保留；旧5脚本／123guards不改，无生产动作。F仍approved=false／actions=[]／paused guard未执行；真实SQLproducer及clock admission正在F2只读设计，W／P／D和真实mixed DR未通过，旧failed仍failed。
- 已把当前本地工作台完整留一份Git忽略的0700私有持久副本，894文件／1160条目共15,509,164bytes，源前后、复制bytes／links／modes／mtimes全核；原active workspace未迁移，副本不进入App／ZIP或公开源。原临时预览路径与正式默认数据目录的迁移、独立净机验收仍不能由这份副本代替。
- [154 AV安装模拟与副作用计划](../../validation/2026-10-28-central-av-install-plan.md)已完成：依据实际649个installed状态，固定ffmpeg的第一模拟仅新增150包、既有包零Inst／Conf、零删除；150包来源SHA及572条依赖边均保留。实际安装未执行；x11-common postinst的服务操作、ldconfig／字体／loader缓存及既有APT hooks需在精确维护脚本中约束，不能由solver成功推断零重启。生产工具链和实际API UID／systemd验收仍未完成。
- v29交付后的只读复看遇到模型目录短时platform_unavailable。本人账号仍authenticated，恢复时本机`/api/status`和当前session模型目录均返回HTTP200、2个模型ready；composer“重新连接”仅刷新工作台合同，没有触发失败目录的重读。打开picker“刷新模型”后原DeepSeek Flash恢复，草稿为空，未派发或重试任务。此恢复入口缺口另做TDD最小修复；v29原真实验收记录保持历史事实，不把新修复未打包的代码算作现有包行为。当前本机约1.5GiB，等待用户释放至少2GB前不启动下一轮大体积打包。
- [AV完整解码计量r1](../../validation/2026-10-28-av-decoded-timeline-r1.md)四文件候选已冻结，作者本机95 passed／0 failed／0 skip，Ubuntu6.1测试专用loader下14正例／3负例；真实900秒音视频、VFR、末帧、AAC／Opus样本和静态旋转均有记录。流式计量替代progress端点，新增子进程输出积压／spawn取消的回收保护；001–020仍逐SHA一致。独立审查正在进行，尚无fresh完整中央回归、PG、实际cleanenv／API UID／systemd或生产AV验收，不沿用旧1895／14或旧wheel的通过结论。
- AV r1独立审查随后为REQUEST_CHANGES：原95项和新增11项协议／资源检查分别通过，但真实32×32→64×64 MP4被输出自动缩放隐藏，绕过max_pixels=1024；真实mono→stereo→mono MP3也被自动转成单声道，绕过max_channels=1。两项P1小样本及原冻结源码均保留。三项采样率变化样本已拒绝，未证明同类rate绕过。修复现仅限调查新的输入帧属性证明，尚未批准r2实现，不能据已有绿色计量测试放行媒体生产。
- composer重连最小源码候选只改ChatPage／Picker，新增强制目录重读和无效旧lease归零；作者85定向／514完整前端，root另一次fresh完整514／0失败／0跳过、TypeScript退出0，152个明确输入前后逐SHA相同。原RED7项及中间旧lease反例仍保留，独立审查进行中；没有新Vite／App／ZIP／原17868激活，也没有新模型或扣点。
- [composer重连源码独立复审](../../validation/2026-10-28-workbench-composer-reconnect.md)随后通过限定范围审查，无P0／P1／P2阻断；reviewer独立原版RED为13通过／9失败，新候选含额外边界30通过，另67定向及514完整分别通过，tsc退出0。三候选与五保护输入前后一致，原本机目录已通过手工只读刷新恢复，原工作台仍v29；新源码待空间满足后打包、激活和真实浏览器恢复验证。
- [AV r1独立阻断记录](../../validation/2026-10-28-av-decoded-timeline-r1-independent.md)现已保留到持久输出，root在原两份2492／1296字节小样本再次实际复现错误接受。r2仍只研究输出自动转换之前的原生帧属性证明；音频rate没有逐帧probe字段，需用固定命令真实拒绝和补充交叉验证闭合，不把100ms声明误差的偶然拒绝当输入门禁。
- AV r2原生帧审核[方案](2026-10-28-av-native-frame-audit-r2-plan.md)已由root完整审核后批准实施：一次native ffprobe审核所有原stream_index，之后framehash保持完整计量并禁用滤镜重建；短采样率切换与多段补偿差23／80ms但固定命令仍直接拒绝，未依赖声明误差。新增完整审核有真实资源成本：单流两child名义CPU限额之和60秒、双流三child为90秒，每child仍30秒，全部完整解码阶段共享60秒绝对wall；不宣称请求累计CPU30。原两P1已在作者工作副本转绿，最终冻结／独立复核／Ubuntu6.1重放与fresh全中央尚未完成，生产仍关闭。
- F2真实只读producer正在本地合成闭包中补齐固定Docker/socket/fullCID／snapshot来源、真实API与Console不同配置键、实际clock和私有receipt；root预审指出的事后PIPE容量检查已改为有界子进程读取，另补hold账户三方及不可变test/live资金来源校验。测试中的完整raw总和仍包含独立test／frozen／expired lot，不调用summary／expire或改写原账本；此处仍未冻结、未独立审查，没有真实daemon／PG／源capture准入。
- [AV r2冻结候选](../../validation/2026-10-28-av-native-audit-r2.md)随后完成作者137定向／0跳过，9项直接输入稳定。独立复核另137定向、原11未改私有检查及新增22边界分别通过，原两个P1原bytes均被拒绝；限定本机8.0.1范围无新增阻断，不能替代真实Linux工具链／服务沙箱或全中央。root已单独授权154新私有stage的固定6.1算法重放，只上传源码和小合成样本，复用服务器900秒资源，不改生产环境或账户资金。
- F2现已冻结：capture源码SHA `648e1d39753ab8495faf46c66e323436a40dede3176a59347aec17ed3eede596`，test `d1280b6c0015d46d0365d0ea4b9ffbf6ddbf457068134527a972fd11680a0e27`。作者定向100项、未修改F1闭包320项、旧纯逻辑11项分别通过；旧5脚本及123个require不变。独立复审进行中，当前仍没有实际PG25006／Linux真实fact／服务器capture／WPD／新真实DR证据，不能将合成producer通过写为恢复准入已完成。


## 2026-10-28：F2-r3与受维护AV来源证据收口

- [F2-r3验证与归档](../../validation/2026-10-28-restore-capture-f2-r3.md)已完成：三轮public源码、三轮独立复审及合成小证据原样复制188文件／1,654,777bytes到 `outputs/_run/restore-capture-f2-r3-20261028`，copy-manifest逐字节／SHA核对。r3 producer为fc3fa9cbdaa751c8efeb7ddb5a40c69efa0b7a3a7675c4b3f7832210577ed261，独立限定本地PASS；136定向、22新边界、1本机native ownership/reentry、320 F1、11核心分别通过，不累加为一次full。原r1/r2请求修改、退出过渡失败及B的1 failed均保留；B整个生命周期只许一次signal的期望已被澄清为非阻断，每次非零signal仍须证明同一未reap身份，并受首cleanup+12秒总预算约束。实际Linux／PG25006／source facts／服务器capture／WPD仍未验，旧真实mixed DR未改判。
- [AV r2已授权Linux重放](../../validation/2026-10-28-av-native-r2-linux.md)已完成14稳定正例、3原负例及3短rate输入门，唯一运行48647，固定工具6.1.1-3ubuntu5；这一算法结果与F2无关。当前cleanenv直接ELF仍缺libavdevice退出127，实际API UID／systemd未验。依据[USN-8915-1](https://ubuntu.com/security/notices/USN-8915-1)，普通base与noble esm15修复级别不一致，150包实际安装继续阻断，不以签名及兼容通过代替安全补丁。
- [官方FFmpeg来源研究](../../validation/2026-10-28-av-maintained-source-research.md)的44个公开小证据加原SHA receipt已备份到 `outputs/_run/central-av-maintained-source-research-20261028`。无Pro可研究签名官方源码＋公开安全补丁的独立prefix路线；9.0.2八项USN有正向源码证据，仍仅隔离候选，实际GPG、当前全部CVE／外库、全decoder/profile、原900s／native审计／资源合同和实际Linux API运行未验。不推荐第三方static包、换OS、新daemon或授API UID Docker/socket。未读Pro账户／假定商业免费资格／启用订阅，没有生产安装或修复声明。
- 本次只做白名单复制和文档收口，未改生产源码／测试／App、未SSH／上传、未运行真实capture／认证／模型／资金操作，也未commit／push。root随后完成了开发源码的实际CUA／合成后台双路径重连验证，并将独占保存 `docs/validation/2026-10-28-workbench-composer-reconnect-browser.md`／safe及同名outputs；本次不复制fixture。B／high、预置草稿与合成附件保留，后台单纯恢复不能自愈，只有composer重新连接后恢复send，POST0；这是开发源码fixture验收。v30未打包、原17868仍v29；fresh完整中央full继续进行中，不提前填写通过。


## 2026-10-28：root实际全量与当前交付门

- [当前冻结中央全量](../../validation/2026-10-28-central-current-f1-f2-av-full.md)最终一次完整运行session73200／exit0，2468 passed、0 failed、0 errors、7 skipped，354.817秒；七项明确需要真实Docker／owned source-handle的恢复测试仍未验。1333源码及11932 Pi payload前后一致，同次full实际SDK七节点通过；706时钟样本无断续，owned group已消失、专用PG无残留事务。原先完整RED的2460／8／7、父gid分变量A的7／1和child umask修正B的8／0均保留，不相加、不改原结果。没有修改产品／测试源码或降低权限guard。
- [实际ReactDOM重连验证](../../validation/2026-10-28-workbench-composer-reconnect-browser.md)已持久保存32个白名单文件／573869bytes，含修正HOME环境后的两条实际CUA路径、同session refresh=true history、草稿／附件／B-high保留和chatTaskPOST0。不是v30或原17868已经升级的证据，原工作台仍v29。
- [官方归档获取尝试](../../validation/2026-10-28-av-official-source-signature.md)尚未获取完整归档／RSA签名，未运行其gpgv分支。[标签实际密码学检查](../../validation/2026-10-28-av-official-tag-signature.md)验证了固定key对应的数学签名及tag对象绑定，但取得的publisher证书包过期，EXPKEYSIG／KEYEXPIRED保留，releaseTrustPolicyAccepted=false；primary网页转存还存在armor空行缺失及解析pub:e，未修补或继续primary验签，不推断官方现用key全局状态。root已向用户更正早期进度简报。没有安装、构建、放宽时间／证书策略或修改生产服务。
- F2 Linux进程材料6文件／64421bytes已本地冻结并归档至 outputs/_run/restore-capture-f2-linux-process-preparation-20261028；原fc3源码不变，八个仅owned child的纯合成案例，尚未上传／执行。已单独向用户请求154新私有目录的载荷和隔离执行授权；原AV13文件授权不扩用。此材料也不构成PG25006／source facts／capture／WPD验收。
- v30构建门保持至少2GiB可用空间；本次只保存小证据，没有新Vite build、App／ZIP、原工作台切换或真实模型请求。保留订阅／自定义双模式，当前分支不变，无新commit／push／merge；支付继续按用户要求关闭。

## 2026-10-28：A2修复、来源认证与工作区接续设计收口

- [A2内容指纹修复](../../validation/2026-10-28-workbench-query-hash.md)已冻结App `475b2d4263d10cb278c99cfbb62d74abdf396dc4160a8dd0106add9d29191450`，仅4行新增；真实AST callback／完整API parser／真实store的新15项由10pass5fail转为15pass，作者完整55文件529pass／0fail／0skip，tsc退出0。独立另15项与审源通过，无本次阻断。错误或缺少本地期望hash时，确认、终态追加和续接之前返回，保留草稿／附件／confirming与原ID。未访问真实storage／17868／PG／模型，未打包。旧2468完整中央输入包含修复前App653…，不当作新App全树快照；本次未改中央代码或重跑该全量。
- [v30只读预检r2](../../../outputs/_run/workbench-v30-preflight-r2-20261028/review.md)实核904公开来源相对v29恰为App／ChatPage／Picker三处，59构建输入相同；新guarded runner仅AST检查，SHA `944bf88a1fd1bc330285c68e4afbaed262838095f5008fc1c2140eb00f4af17e`，未执行。原两处delta的预检不覆盖本候选，原样保留。新检查时free271,872,000bytes，不满足2GiB门；没有新source freeze／Vite build／App／ZIP／激活。原17868仍v29、旧包及实际账号配置历史保留。
- [上游RSA来源认证](../../validation/2026-10-28-av-upstream-rsa-source.md)为新实际进展：Debian官方镜像orig12,040,788bytes／SHA8c385…与asc／DSC size和SHA相同，服务器真正gpgv exit0，唯一VALIDSIG精确固定 `FCF986EA15E6E293A5644F10B4322F04D67658D8`，无过期／撤销／坏签名状态；独立审读仅批准本份来源证明。DSC自身未验签、不是替代上游签名；没有与不可达ffmpeg.org HTTP bytes直接比较。旧ED数学有效但过期、直连失败均原样保留。
- [只读编译预检与离线构建准备](../../validation/2026-10-28-av-build-preparation.md)小证据12文件433,205bytes已逐SHA持久归档。154有4CPU／约2.97GB可用RAM／41.18GB空闲的采样，但无gcc／make等；Docker只运行本机--version，未联系daemon。76旧参数有74静态受支持，omx移除和glslang→glslc候选映射明列。镜像digest／依赖版本锁／shader语义／完整profile及实际512MiB运行合同仍待验；未提取完整源码、拉镜像、安装、编译或触及生产。后续先做有界官方元数据与storage只读锁定，不能据方案直接执行builder。
- [工作区预检](../../validation/2026-10-28-workspace-migration-preflight.md)在实际浏览器的纯合成A／B workspace及两个origin下核对：同origin换scope为空、返回原scope恢复，同scope换origin为空、返回原origin刷新恢复；contractGET11／mock附件验证POST3／taskPOST0，自建进程与tab已回收。真实App／生产附件与用户迁移未验，原workbench没有操作。默认目录仅metadata证实已存在，未读取内容或chmod。
- [正式目录与草稿接续设计](../specs/2026-10-28-managed-workspace-continuity-design.md)r2 SHA `fdb3d0cdc80a2eb61c3c0f51d2a576c71b913103c32a71c5849241232f9e4ca0`，第二轮独立文字审读PASS、原2P2闭合；r1与两轮报告原字节保存。推荐保留17868、全新legacy永久目录、共用私有profile及显式一次性草稿恢复。纯source/HMAC＋existing-only读、已有控制协议、可移动App／资源rebind、consumed映射、partial工作副本／canonical冲突、孤立草稿入口及未知submission均明确。人类设计选择已询问，尚未回答；零迁移实现、零新增迁移测试、零真实目录切换。此批准只针对新设计开发，真实迁移前须具体输入／备份／停写审计。
- 本轮有可验证进展，目标保持active；不标complete／blocked。F2六文件Linux进程测试仍等待独立载荷授权，不能复用AV13的授权。第二真实产品、签名身份仍未提供，正式支付按用户要求保留关闭。没有新commit／push／merge、真实调用或资金动作。
- 最终只读复核：git diff --check退出0，branch／HEAD仍codex/ui-redesign／e972af0；v29 ZIP与旧source manifest逐SHA不变。初始沙箱curl失败后，lsof确认原PID97261仍监听，沙箱外同地址HTTP200，未重启服务，未查账号／钱包／历史端点。小回执保存于 [final-readonly.safe.json](../../../outputs/_run/workbench-v30-preflight-r2-20261028/final-readonly.safe.json)；磁盘仍不足2GiB，构建未执行。

## 2026-10-28：v31激活与本人重登后的真实链验收

- 磁盘实际恢复至满足2GiB门后，v30及v31先后由已审guarded runner完成构建，旧包及原工作台全部保留。当前[v31基础归档](../../validation/2026-10-28-workbench-v31-delivery.md)记录ZIP 129,745,884字节、SHA `f0e6e86581edb243496ce8e3ad8f3f40363e87e3e1a2c4a28fe2c6ace6c5f13c`；源manifest `e1291af0955bbfcb07bc348fc6b92c0f6ef783bc28dd3c93513ae6422d75a346`，资源15402、公开source904、构建inputs59。相对v30只改web/app.py与App.tsx的A1配置／就绪分类，不改变真实认证／目录／租约／发送门禁。
- A1源候选的42后端、541完整前端和tsc及独立limited PASS已收口；实际v31匿名启动确认configured=true而gateway/auth=false；包内SDK→真实host write_file→168字节回读／下载在localhost-only sandbox通过，JPEG／WEBP额外实编解码通过。owned隔离进程与端口均回收。以上离线证据不冒充云模型／资金结果。
- v31在原17868、原workspace、自然HOME中激活，切换前先留Git忽略0700私有备份；894文件／1160条目、159保护文件逐字节保全，workspace/source scope与钱包一致。未迁移profile或目录，旧App／ZIP／launcher未修改。用户本人重登后仅session_id变化，原account.id与完整账号／账本归属仍相同；旧chat依据来源边界保持只读，另新建验收chat，没有放松guard或覆盖旧历史。
- [真实验收](../../validation/2026-10-28-workbench-v31-live.md)在session `mvs6l1zfnszqfb`，turn `18096fdf-fc77-4372-b151-01d2b0bcb829`，仅一次UI Send；route=subscription、runtime=Pi、DeepSeek Flash。一个invocation内的两个不同operation/execution分别settled，actual1436／1864microcredits；原轮completed、accepted、clean_end=true。工具只见write_file，新文件及App下载均18字节、SHA `aa5868805e04a9e21573cc0938288977dd71bcbc16de41e2c7a4a6195bc48097`，内容为合成marker及一个换行。
- 钱包由可用6.938114变6.934814，已用2.052158变2.055458，两者差额均0.003300点。预留1.009728／pending2／active0、原51条usage、issued及有效期全不变；未发新点或改旧unknown。exact-turn recoveries两笔与新增owner ledger的rate／amount／actual multiset及钱包差额交叉一致；execution IDs和钱包reservation IDs各自独立，不宣称逐行外键join。单次点击／未重放来自root操作记录，不从账本快照扩推所有网络尝试。
- 原轮状态查询、刷新及草稿测试均未发送第二任务。登录、原回复、下载链接、模型及未发送文字保留；发送恢复可用，无自动设置／画像弹窗。只清空本次合成未发送草稿，最终界面保留文件与两笔已结算明细，供用户继续使用。
- 离线核账脚本r1误将同一invocation中的两个模型步骤假定为两个invocation，断言失败；r2审阅指出金额派生／来源绑定等证据边界，r3补齐，r4增加实际ID集合不相交断言。原脚本与各阶段结果均留存；r4离线exit0及独立静态限定PASS，未因此新增任何模型请求。最终GET钱包与usage全同、ZIP重hash一致、branch／HEAD不变、git diff --check退出0。
- 本轮没有commit／push／merge、正式支付、生产AV部署、F2 Linux上传或真实工作区迁移；总目标仍active。AV完整源码树／递归依赖／编译与真实API运行、F2实际Linux／PG／source facts／WPD／成功mixed DR、永久工作区接续设计批准及实施、第二真实产品、签名／公证／净机仍保留未完成；不把当前macOS本机与真实单轮通过称为所有交付完成。
- AV下一步另完成[完整源码获取的只读最小方案](../../../outputs/_run/central-av-source-acquisition-next-review-20261028/report.md)，两份报告共14541字节已逐SHA持久归档。只计划复用已签名FFmpeg原归档、闭合libplacebo原始commit/tree/blob及配对Vulkan gitlink／完整vk.xml，再安全提取；限额仅建议、获取器和负控尚未实现。没有SSH、下载、提取、Git fetch、构建或生产变更，也不将tag认证或小元数据当完整源码验收。

## 2026-10-29：AV源码准备工具实际实现与离线收口

- 前一目标轮为progress：原17868的v31真实模型／工具／3300microcredits结算证据改变了权威状态；本轮继续完整剩余目标，未缩为只交付工具。详见[本轮验证](../../validation/2026-10-29-av-source-tooling.md)及[工具实施账](2026-10-28-av-source-acquisition-tooling.md)。
- 新增4实现／6测试及ops README。归档首轮37后独审5项，统一修复69项及r2限定Spec／Quality PASS（另16观察）；对象链58后独审2项，修复先RED12／GREEN71及r2限定PASS（另4观察）；HTTP31后独审3类，先RED13，修复加25回归，与34协议合90项及r2限定PASS（另10观察）。原FAIL、Task1审阅最终error、HTTP写报告中断后恢复均保留，不把旧结果改绿，不把不同轮计数相加。
- root唯一六文件完整组合230 passed、0 failed、3.12秒，Python3.12.7，10个source／test SHA前后完全一致。未使用DBfixture；原普通入口缺psycopg的conftest错误保留，不算行为RED，也未安装依赖。两真实本机Git回放在空的独立接收裸仓库严格导入，并经原始对象树验证与marker匹配；只是合成互通，不是上游／Linux资源／GPG验收。
- 154严格host-key SSH仅只读确认：旧签名archive size／SHA同，raw tar为GNU普通格式，10525头（126目录／10399普通文件）、无扩展或链接，最大949519B／内容90358343B／流98703360B。头自身5388800B使root原4MiB技术预算不足，已证据驱动改tar／容器metadata8MiB；HTTP／单Gitmetadata4MiB及其他限制不变。新默认有同规模回归，显式旧4MiB仍失败，未倒写旧通过。
- 当前HTTP在原始HTTPResponse读取前累计约束headers／transfer framing／trailers与advertisement共享4MiB、输入128MiB；候选仍明确identity／adoption／OS flags=false。所有提取／落盘要求调用方控制的owned0700父目录及可信单写者，不能用检查冒称隔离恶意同UID。容器校验、对象完整性、签名与来源采用分别验。
- 服务器Python3.12.3、Git、gpgv、unshare、systemd-run、prlimit和systemd255.4已只读确认，相关版本官方文档已查询；资源只是采样，probe自己的AS512MiB／CPU30秒不替代完整作业的Memory／Tasks／wall／DNS／filesystem隔离。未新建单位、上传新工具、提取、安装、编译、重启生产或更改账户／钱包／模型配置。
- 下一步是实现／审查／实际验证受限Linux执行器和完整真实来源链，再进入递归依赖／编译profile／API UID运行／供应商及生产。原F2、工作区设计批准及迁移、第二真实产品、支付暂缓、macOS签名净机等仍按完整总账保留；当前目标active，不标complete／blocked。当前分支继续保留，无新commit／push／merge，前端无新增说明文字。

## 2026-10-29：v32过程保留与v33完整GUI恢复查询收口

- [v32过程快照](../../validation/2026-10-29-workbench-v32-delivery.md)完成成功host写入结果的准确artifact引用、实际匿名包内Pi／host／SSE／快照／下载与刷新卡片验证；31字节合成文件／下载SHA一致，旧模型文字及凭据保护保留，没有新增前端解释段落。571完整前端、120定向后端与构建结果分别保留，不累加为另一套全量。v32直接HTTP恢复成功，但浏览器整条链被固定20秒总预算取消，仍记录为缺口，不能以v33后续成功改绿v32过程。
- [v33最终验证](../../validation/2026-10-29-workbench-v33-delivery.md)将GUI完整链保留为单一有限90秒预算，媒体恢复保留30秒共享预算／单GET最多10秒；账号前后签名、Scope、轮次、Abort及晚到拒绝未移除。相对v32仅`easel/platform/media.py`与`web/frontend/src/components/ConversationRecovery.tsx`两份来源变化，其余902份来源、60项构建输入与11,932个Pi文件保持；artifact核心来源字节不变，准确卡片管线的实际App／匿名UI证据明确继承v32，不冒充新真实模型协议。
- v33 ZIP为129,749,097字节，SHA256 `6af10003c9b176ae9fb8ecbf03ba77d0a66e20a2dcf1fed420f121d82b91a195`，macOS15 Apple Silicon、未签名／未公证。root核验15,402资源记录、完整ZIP CRC及前端输出／来源；ZIP36,118成员含目录，payload非目录15,407、AppleDouble／resource metadata非目录17,174，不把所有非目录都算应用payload。573完整前端／0failed、17定向媒体／认证预算与tsc／Vite构建通过；各轮及定向重叠结果分列。
- 实际原17868同会话`mvsy5q2r3knvdz`／轮次`e68be990-da7d-4196-85c6-d35b2a5fd8af`完成GUI状态查询，显示2条“已结算／结果可用”，0.001326与0.001622点，无查询超时／媒体错误，查询结束可再查。该动作是读取原任务，没有新模型提交。真实AX与工作区截图保持私有，仅安全摘要及hash进入[浏览器回执](../../../outputs/_run/workbench-v33-delivery-20261029/activation/browser-final.safe.json)。
- 本轮真实任务在升级前v31只发送一次UI，Pi＋DeepSeek Flash订阅路径的1 invocation含2 operation／execution，1326＋1622＝2948microcredits（0.002948点），可用6.934814→6.931866；25字节文件／下载已核对。旧真实轮次没有回填artifact metadata或改写被隐藏的原回复。root在私有输入运行r3核验器退出0，但HTTP响应不echo session／turn，来源绑定依赖确切路由／query／响应hash及已审服务器过滤；金额／费率multiset及钱包净差不等于逐笔数据库外键关联或全历史审计，旧r2静态审阅不当作r3 raw重放。
- [最终guard](../../../outputs/_run/workbench-v33-delivery-20261029/activation/final-guard.safe.json)确认同一账号仍authenticated，原174个保护普通文件及55条观察记录无变；可用6.931866／旧预留1.009728点、pending2／active0。原账号、历史、自定义配置及workspace保留，未迁移profile；旧App／ZIP／启动器及v31／v32归档保持。当前904份来源匹配冻结包，branch／HEAD仍`codex/ui-redesign`／`e972af07444e181e3eb737441ecac306bca778b3`，diff检查0；无新增真实模型发送、发点、资金改动或commit／push／merge。
- [v32闭合归档](../../../outputs/_run/workbench-v32-delivery-20261029/copy-manifest.json)保留33副本／311,081字节，加README／manifest共35文件；[v33独立增量](../../../outputs/_run/workbench-v33-delivery-20261029/copy-manifest.json)仅14副本／204,457字节，加README／manifest共16文件，含最终browser-safe及guard。各项源before／copy／after SHA相同，v32 base manifest `96c68934ada903418dad49789f36545144e50432cb9c527d90284e1164d720b8`保持，v33 manifest `d775517dd174e888be5005c2f1bd6666207682efefc00c3ce06e68160d98e30d`。未复制真实workspace／backup／账号／配置／raw钱包／AX／真实截图；归档与本次文档编辑不执行verifier、模型或资金操作。
- AV的单次授权Linux运行在来源处理前因kernel gate失败，尚无该次source／GPG验签、提取或编译结果，未自动重试；原服务／容器及公开输入保全，当前仅本地补诊断。本轮没有生产AV部署，也没有F2实际Linux／PG25006／source facts／WPD／成功新mixed DR、永久工作区接续批准及迁移、原生剪贴板／IME／VoiceOver、净机、签名／公证、正式支付、第二真实产品或完整音视频供应商完成声明；总目标继续保留全部硬门。

## 2026-10-29：AV 首失败诊断本地收口，新的远端一次运行待确认

- [诊断实施账](2026-10-29-av-kernel-diagnostics.md)补齐 collector／validator 首失败 stage、gate、reason 和受限事实；未知／恶意／超长结构整份拒绝，无异常正文、任意类名、原始挂载／路径或环境输出。所有原隔离条件、系统调用顺序、资源值及来源处理首门保留；完整失败记录最多4096字节，失败仍退出1。
- 真正RED8 failed后最小实现，实际validator→worker CLI合成链及7类恶意结构拒绝已测。root唯一联合五测试文件262 passed in0.75s，9个输入前后SHA相同；独立限定Spec／Quality PASS，无P1／P2。只是本机代码／合成验收，尚无新增Linux事实、GPG、提取、编译、生产或供应商证据。
- 新载荷仍4代码＋manifest，共5文件82321字节；bootstrap111794字节，SHA `035655a614be107bd77b2c26147b539e782de1147b889e4b4749d8ff377440e1`。新stage `/root/easel-av-source-run-20261029-791399b0dc890cf0` 与新unit `easel-av-src-791399b0dc890cf0.service` 独立冻结。旧载荷／bootstrap和失败现场保持；原一次运行批准已使用，本轮已发新一次具体确认，未把旧批准扩大或自动重试。
- 当前工作台仍v33原PID23888监听17868，ZIP重hash与交付摘要一致；本轮没有前端改动、桌面重打包、账号／钱包访问或新模型任务。分支／HEAD保持 `codex/ui-redesign`／`e972af07444e181e3eb737441ecac306bca778b3`，diff检查0，无commit／push／merge。完整剩余硬门继续保留，总目标active。

## 2026-10-29：第二次明确批准的一次 Linux 运行与确切拒绝门

- 用户另行明确批准5文件82321字节和111794字节bootstrap；root完成只读预检后仅运行 `easel-av-src-791399b0dc890cf0.service` 一次，SSH终态exit1，无自动重试或观察超时。该单次批准已使用。原计划false字段保持冻结历史，授权和实际执行另有独立回执。
- [实际运行报告](../../validation/2026-10-29-av-kernel-diagnostics-linux-run.md)记录worker诊断为kernel_collect/mountinfo/duplicate，count24为首重复记录序号，targetOTHER；并未知道路径、层访问位或是否正常叠挂。完整validator尚未通过，不把前序采集当隔离合格；work空、无result／GPG／提取／编译或operator成功回执。unit已collected且cgroup不存在，默认Result=success不能覆盖实际失败。
- 前后4指定服务身份、3容器含启动时间和3公开输入相同，5payload pin与批准内容一致；只证明这些采样对象保全。实际独立复核支持上述限定结论，不是全生产或财务审计。原28文件本地证据闭合原样保留；执行新增12副本、加README／manifest共14文件，manifest `9fb8cf09e0e587ac073c4a418a674421a083878dd5ed5a2bb40a94e2c31559ad`。
- 下一步为[本地可见挂载模型与固定FD交叉证明](2026-10-29-av-visible-mounts.md)，先实现纯模型及负控、再审collector集成；不按OTHER或记录顺序忽略重复，不扩大一次授权。本轮仍无前端说明文字、桌面重打包、模型／钱包操作、提交／推送／合并，完整目标保持active，其余所有硬门保留。

## 2026-10-29：可见挂载兼容候选已实现，第三次具体单次运行待确认

- [可见挂载实施账](2026-10-29-av-visible-mounts.md)已实现完整有界ID／parent图、唯一根、同点叠层与祖先覆盖、固定五目录O_PATH FD实际mnt_id交叉证明及首尾mountinfo稳定检查；隐藏分支也必须先通过全表结构检查。原validator所有root／capability／namespace／资源／只读／own-mount／可写子树门保持，新增源模块必须经过外部payload hash，不以OTHER或顺序选择层。
- 真RED/GREEN保留，root最终唯一8测试文件联合348passed／0failed、0.71秒，13输入前后SHA同；限定独审Spec／Quality PASS，无P1／P2。本机4096合成siblings单次wall1.626秒，仅量级采样。五FD非完整子树独立测量，首尾相同非原子/无ABA证明，执行前提和真实Linux验证仍保留。
- 新6文件93707字节（5代码＋manifest）、bootstrap127016字节已冻结，SHA `ae8f1a427836c7fa981177f9a1d865edf1d6346812a6917452a86b7710d09f93`；新stage `/root/easel-av-source-run-20261029-fadd4a945213cc1e`／unit `easel-av-src-fadd4a945213cc1e.service`。已发新精确单次确认，写入时未获回复，未上传／执行；两个旧单次批准已使用，不扩大、不重试。旧失败和两套闭合证据不改。
- 桌面v33的904源码和60构建输入当前逐SHA保持，本轮只改中央离线验证工具，没有前端解释文字、打包、账号／钱包／模型操作或提交／推送／合并。真实Linux、GPG、提取、编译与供应商尚未由本轮通过；完整剩余目标与其他硬门保持active。

## 2026-10-29：获准六文件实际越过挂载解析，ROOT写权限门失败与分类候选

- 用户明确批准六文件93707字节的一次运行；root核主机／资源／stage缺席和原服务后，仅运行fadd4a945213cc1e单位一次，SSH exit1、无timeout/retry。新可见图和5FD已完成collector，冻结控制流支持前序身份／能力／namespace／资源比较在该样本通过；随后ROOT可写后代策略拒绝，至少一条超既有允许子树。诊断总数9不是违规数，实际类别／路径未知；后续挂载／work700／权限探测未走到。
- [实际运行报告](../../validation/2026-10-29-av-visible-mounts-linux-run.md)记录work空、无GPG／提取、单位collected／cgroup absent；not-found默认success不覆盖exit1。指定4服务／3容器／3公开输入与6payload pin保持，只是采样保全。原本地34文件保持，执行新增14文件闭集manifest `a2e229872ee7e023a678c32300297ab281a11073a73c7e6db07c6e7c3eb27019`；第三次一次批准已使用。
- [ROOT分类实施账](2026-10-29-av-root-writable-diagnostics.md)及[本地候选验证](../../validation/2026-10-29-av-root-writable-diagnostics.md)只在原ROOT拒绝后增加固定17键计数，不增加读取、可写例外或改变原18个if／接受决定。root唯一10测试文件370passed／0failed、0.74秒，15输入前后SHA相同，独立限定Spec／Quality PASS。新测试曾误放root/tests再原字节移位，首次路径错误在测试前，不记行为RED；真实作者RED与中间失败保留。
- 新6文件96543字节、bootstrap130796字节已冻结，SHA `72c7f98eb302241e9f6fe3870e42c61f5ce1f412da5c5570dcfa43088e652b18`，新stage/unit 1adc617e79ddf853的具体一次确认已发，写入时尚未获新回复、未上传／执行；不复用前三次许可。本地32文件闭集保留，实际9项分类仍待新实证，合成分类不充当线上结果。
- v33的904来源／60构建输入当前逐SHA保持，无前端解释文字、桌面重打包、账号／钱包／模型操作、生产修改或commit／push／merge。完整ROOT策略／隔离、来源／编译／供应商与其他完整交付硬门仍未完成，总目标active；本轮为有代码和真实门推进的progress，不标complete或blocked。

## 2026-10-29：媒体备份缺口的实证与接入设计待确认

- 第四次AV载荷仍单独等待其已发出的具体批准，未上传／运行，前三次一次性许可不复用。本轮推进完整工作流8的另一项缺口；不把备份只读SSH观察当作AV执行批准。
- [媒体备份预检报告](../../validation/2026-10-29-media-backup-preflight.md)记录：既有mixed DR两个纯文件用例组实际10passed／0failed，另用全新自有33MiB稀疏文件在既有32MiB聚合门重现预期拒绝，文件内容读取0次，原脚本SHA未变。不是生产备份失败、真实媒体解码或新大容量恢复验收。
- 实际只读核定154上指定备份脚本／service／timer的摘要与执行绑定。初轮观察器不支持-B，binding=false原记录保留；后续支持受限Python flags的观察器核字面与effective命令一致、无hooks／drop-in／待reload，确认命名timer引用SHA d330bccf…的旧脚本，timer active/waiting。结合该公开脚本静态collect范围，媒体文件尚未纳入这一已审收集逻辑；不声称已检查真实归档内容、解释器／环境或其他全部备份作业。未读真实配置／密钥／数据库／媒体，未触发备份或修改timer。
- [新媒体备份设计](../specs/2026-10-29-media-backup-closure-design.md)推荐路线A：B1本地分块归档及文件闭包恢复，B2完整GC／写入屏障与认证加密，B3真实fresh restore及具体timer部署。两位独立R2文字复审PASS，原三个P2（整窗GC、墓碑、全物理归档及有界尾部）关闭，无新P1/P2；原R1／发现均保留。设计已向用户具体询问，尚未取得这项新设计批准，零新备份实现，不以reviewer／目标自动继续／AV批准代替人类选择。
- 本轮重新逐SHA核v33的904来源／60构建输入以及4个中央保护源，差异均空；未访问preview账号／workspace，无新增真实模型或资金动作，无前端／桌面重包／生产修改或commit／push／merge。现有v33仍是当前交付；完整目标active，F2／实际WPD／成功mixed DR／媒体timer、AV全部隔离与来源／编译、工作区接续批准、签名净机和其他完整硬门继续保留。

## 2026-10-29：用户批准路线A／B1，本地分块文件闭包交付

- 用户在预检后明确批准A／B1，本阶段[执行计划](2026-10-29-media-backup-b1.md)已实施；不是由AV授权或目标自动继续推断。新增import-only `media_backup`合同、原始USTAR codec、受信FD适配器和write／restore编排；不改旧DR／F1／F2／AV源，不新增生产CLI或timer，不改前端。
- [最终B1交付验证](../../validation/2026-10-29-media-backup-b1-delivery.md)的root唯一联合运行229passed／0failed／0errors／0skipped，0.61s；其中新B1为219项、旧mixed纯文件两个用例组为10项，不累加此前分次GREEN。前后11个新源／测试和10个旧冻结源SHA同。实际33MiB单blob及34MiB合计归档／恢复通过，1MiB最大IO，Python追踪峰值3173990／3177807字节；不是4GiB／32GiB极限、RSS、生产压力或真实媒体解码证明。
- 分项独审和完整独审PASS。原Task3 P2为退出Sources后保留部分generator仍持ownedFD／能读，原probe／R1保留；root行为RED后最小修复owned登记、exit关闭和active／复用检查，42项通过，R2与完整最终审查接受，无未解决P1/P2。没有将最初missing-module失败、一次contract依赖未就绪或原失败日志删去。
- [源码包与交付安全收据](../../../outputs/_run/media-backup-b1-20261029/delivery.safe.json)独立记录确切ZIP库存／SHA。证据归档46份固定副本共578708字节，加README／manifest共48文件；manifest SHA `a3e926df80428f91a9409b72d452b02b0b2cf37d8b8e678a4ee00ae2ac5bb138`，无大合成文件本体或真实数据。源码与证据只在本地，无SSH或远端任务。
- B1信任完整捕获计划，文件恢复不认证真实快照／missing evidence／停写／GC屏障，不执行数据库；sink加密、durable发布、实际fresh restore及精确timer均留B2／B3。原失败mixed DR、F2实际Linux／PG／WPD、AV全部隔离／来源／编译／供应商、永久工作区、签名净机及完整其他目标不因此完成。
- 本轮末v33的904来源／60构建输入再次逐SHA相同，未访问preview账号／workspace，不改登录、历史、自定义配置或资金、不发送模型，不重包桌面，不提交／推送／合并，分支继续codex/ui-redesign。B1阶段收口，完整目标保持active；第四次AV单次载荷仍需其独立批准，B1回复不覆盖它。

## 2026-10-30：B2现有代码调查、旧封装size实证与完整设计待确认

- 上一目标轮有实际B1代码／229项／源码ZIP交付，本轮按progress继续工作流8，未把目标缩成已完成B1。重新核B1 ZIP及21个新／旧冻结源完全匹配，实际分支仍codex/ui-redesign。
- [B2准备记录](../../validation/2026-10-30-media-backup-b2-preparation.md)保存两份独立静态调查：后台GC／async／voice推进不受enabled关闭，现storage锁不覆盖物理unlink／part／discard及全lifetime；finish_gc历史token不能当活跃claim。旧v1 whole-bytes32MiB且预知整体SHA，旧inner与B1格式不同；decoder最后read(1)必须绑定完整auth/EOF，不能暴露ciphertext fileno。八个race只是静态论证，无生产重现。
- 本轮实跑现有公开legacy helper的合成size对照：32MiB合法metadata到达被替换的key loader后拒绝，33MiB在loader前拒绝；两次都不是加密成功，没有加载key或crypto执行。源SHA前后保持，证明不能把B1大archive直接接旧接口。这是现有行为实证，不计成新B2测试或实现。
- 按Context7先resolve /pyca/pynacl后读official bindings，并核libsodium／Linux flock官方文档；官方固定PyPI JSON候选PyNaCl1.6.2及Linux/macOS wheel摘要保存，未下载／安装。当前venv crypto50.0.2、PyNaCl未安装；候选metadata/main文档不当目标wheel或安全来源通过。
- [完整B2设计R3](../specs/2026-10-30-media-backup-b2-design.md)24487B／SHA f9614a317705fd25663bd942e847759910a8d1c4fcf9c9a70e9107b8d8603cff：独立维护schema／opaque permit、完整操作与物理锁、generic producer和无环原facts、strict序列／roles／配置后验、新v2 exact frames／final auth、独立加密context＋root恢复anchor、durable双对象与commit、自验及显式wheel交付。两位R1共5P2在R2闭合；R2尺寸文字P2在R3单行更正关闭。原设计／报告保留，只是文字审阅，不改旧F1/F2／DR或宣称实际Linux／PG／密码实现通过。
- B2本地开发／隔离验证的具体确认已发，写入时未获回复；采用brainstorming明确设计批准gate，未拿目标自动继续或B1阶段许可代替新设计确认。零B2 runtime实现／迁移／真实服务或timer操作、零生产SSH／AV任务／模型或资金动作，前端无新增解释文字。21源及v33的904源码／60构建输入当前再次匹配；完整目标active，其余所有硬门保留。

## 2026-10-30：v34 有界账号入口与简洁文案交付

- 按已批准的创作者任务方向和用户明确UI文案要求，完成 [v34计划](2026-10-30-workbench-v34.md)；不是B2新设计实现。最新读取每次30秒截止，先失效generation再abort，清理deadline／poll所有权；进入可操作未知态，保留原账号／登录上下文，拒绝旧响应和计时器。没有自动登录／取消／退出／模型重发，通用API／服务端锁和90秒恢复保持。五文件六处单行文字收短，实际错误、必要决策含义／权限／费用状态保留。
- 定向行为RED／GREEN及独立两文件审查闭合；完整前端585passed／0failed／0skip、139输入前后相同；账号63项与全量重叠。TypeScript／Vite通过，既有chunk警告保留。独立包23项重算15,402资源、904来源、60构建输入、前端100来源／13资源、ZIP全部CRC和完整负载对应；仅六个前端组件来源变化，旧backend／Pi工具验收按同源继承，不冒充本轮真实供应商。
- 六个回环合成案例实际观察，专用服务／标签已关闭；未知账号头像恢复卡片、热点／登录错误、发布禁用title、画像tooltip和无对比数据文字已核。B站实际可发布，不能沿用fixture初始错误假设；普通不可用toast未自然触发。浏览器未单独点retry或制造晚到回复，组件测试覆盖这些边界；无真实认证／模型／支付／发布POST，只有一笔合成登录POST。
- 原17868使用新私有备份和认证实例切换，原scope／HOME／工作区／history和配置保留。真实页面未知态可打开卡片和设置，关闭返回原会话；原150保护文件保全、钱包在R2after相同；末次guard只观察正常四字段令牌刷新，未重读钱包。未再次查询已结算摘要，不将刷新后摘要未展示当历史丢失。[交付报告](../../validation/2026-10-30-workbench-v34-delivery.md)分别绑定激活前53文件与激活16文件闭集。
- B1源码ZIP本轮重新hash／17成员CRC仍匹配；不重复计作新229测试。B2本地实现／AV第四载荷／永久工作区迁移的具体确认仍各自pending，未借本轮扩大。正式支付按用户延期、第二产品／签名身份、AV隔离／来源／编译／供应商、F2实际Linux／PG／WPD、成功完整媒体DR／timer、原生IME／VoiceOver／剪贴板及净机等硬门保持。当前分支不提交／推送／合并；完整目标active，本轮是实现和交付progress。
