# B3 离线 worker 与网络采集进度（2026-11-04）

当前分支 codex/ui-redesign。本轮未提交、推送或合并，没有修改前端界面、账号／对话历史／接口配置，没有访问生产数据库、支付、模型或真实供应商密钥。完整 B3、bootstrap 交接和最终桌面交付仍未完成。

## 已完成并核对

1. 当前 worker v2 源码的24项协议测试、24项入口模拟测试通过。本轮构建快照 wheel d2ca3bdc723ec4728cc3c62e63f7ca64543c9d41231e86ff91184aff8a3968ad，561208字节／152成员；Root逐项核对RECORD、源码文件和worker字节。后续新增N1/N2未进入此历史wheel，后续必须重建，不继承其“当前制品”身份。
2. 新增固定离线安装器 b3_worker_install.py，23项合成测试通过，独立规格／质量审查批准。真实ARM64隔离公共准备容器完成27个锁定运行依赖＋平台wheel安装、pipcheck、完整私有PG原生库和许可材料落盘。pg_restore原始loader／动态库链与16.15工具版本检查通过。未执行数据库恢复。
3. 本地历史 worker 镜像 sha256:265b68fefa8c50915931b3893eae418732355fd50660097e932e9f3b75cb1ff1；构建清单d00b2f464c42fb5e6ddb5e30993365c2351f8b60f61d60781c5a479452e10755，5586个文件／472496903字节。构建后Root观察器误把Docker省略的可选Volumes字段当作缺失错误；保留原失败，并只读核对原镜像／容器，没有重新安装或重复commit。
4. 原始冷worker2fbc215f...退出1，固定诊断b3_worker_rejected；实际拒绝点为execution_worker.py:321。保留原失败，未重启或计为PASS。新的独立诊断证明：本机Linuxkit6.12.76的none网络带九个默认DOWN隧道和bonding_masters控制属性；该属性不是网卡。既有仅lo字面规则因此不能通过。用户已批准严格关闭默认接口路线A，完整批准记录与原提案归档在network-amendment。
5. N1纯二进制解析与N2固定只读采集源码完成。N1初轮负例存在被缺少DONE掩盖的问题，已通过完整有效邻界与内存guard变异证明修复；N2独立审查发现关闭前时钟的BaseException会跳过close，已通过嵌套finally和6项回归修复。最终联合202项纯测试通过（Root0.20s），独立复审规格／质量批准。源码／测试具体SHA与报告见netlink-transport-review/fix。
6. Root在新只读、networknone、UID10001／cap0／NNP公共容器中执行批准源码的独立组件探针，真实socket/select/clock未替换。六组GET采集完成，消息数10／2／3／1／3／2；同一15秒组件时限内完成、前后namespace inode和FD名称清单相同、原容器终态exit0。独立记录审查批准限定组件声明。该执行是明确的嵌入源码组件，不是已安装worker，也没有原始target绑定、profile或isolation验收。FD名称相同不是完整descriptor身份证明。
7. 另一全新组件捕获同一进程／namespace内netlink＋前后sysfs／proc视图，已保存28305字节公开测试候选，SHA ed372111e9b71271b082dd2cc8fcd6d8a5f6e124f58f4329999e4c742dfc5bab。它用于制定纯规则，不是可反序列化的运行授权。早期不同容器捕获未拼成同一实际快照。
8. 按现有清理授权，定向删除本轮7个已经退出、材料已归档的公共安装／诊断／组件辅助容器；没有删除镜像或数据卷。原失败worker及全部旧失败目标保留，原专用合成源PG50a...仍为原PID62835运行。未操作其他应用。
9. N3 精确语义校验已完成并通过独立规格／质量审查。冻结规则包含 630 项属性描述、完整配置向量、永久标识和哨兵语义；独立审查逐项核对编译常量与 81,806 字节冻结策略。Root 复跑 N3＋N1＋N2 共 6,552 项通过（19.68s，exit0）。审查另发现计数器测试的整体不等式可能被重新分包元数据掩盖，现已改为同样分包的基线和精确字段断言；839 项内存变异反证失败后，Root 聚焦复跑 839 项通过（5.60s，exit0），独立复审关闭该问题。生产校验器 SHA e40ed6f6fef62284edde4e5cfbf3f1f1afb7fc5173bb3ec4e1a27bb31ebedde6；编译策略 SHA d61bf88753d073e4a8e00e4e13b81dc0f7ffa1d5a3ecb79ddad9e32313859f30；最终测试 SHA eef00982fd9f3f29b7f3f7fcbe03a2b63107def80fa3e1b5642394e22b7b8a0a。这里只确认纯数据源码和测试，不确认当前 worker 已包含或实际通过新规则。
10. N4a 固定读取器完成，Root 复跑 41 项模拟测试通过（2.31s，exit0），独立源码审查和测试精度问题复审均批准。新公共源码组件容器19ee2351...实际使用冻结的五个源码模块，执行两次读取＋N3校验，用时15.261375ms，均在同一15秒组件截止时间内；十个接口完整报告、仅lo启用，两次完整安全快照相等。容器为none网络、只读、UID10001、cap0、NNP、1CPU／512MiB／PID64，无挂载或端口，终态exit0。独立证据审查批准该限定声明。它仍是公共源码组件执行，未检查已安装worker的来源、目标绑定、独立宿主预期或SQL恢复；标准FD0–2身份与FD名称清单相同不代表全部描述符持续身份或原子性。源PG进程元数据前后相同，不代表数据库内容比较。归档和审查后仅删除这个已退出辅助容器，镜像和数据卷保留；本轮累计定向删除8个成功辅助容器。
11. N4b v3协议与worker集成完成。独立审查指出直接Python输入的特殊字典键可在拒绝前触发自定义比较，已通过4处真实RED→GREEN修复；Root最终复跑313项通过（8.80s，exit0），独立复审规格／质量批准。新请求绑定独立策略／网络快照，响应保留两次完整观测、全部接口和原始计数器；stdout设置并核验非阻塞，原截止时间内保留短写偏移、拒绝未知进度。
12. 新wheel585352字节，SHA a40b8b3b97a5dae0573746b1a45ef146cee87e6afe9e16ae6f9c351649582dfa；Root验证158个RECORD条目和155份实际打包源码。初始候选清单遗漏5份force-include辅助文件，在构建前发现并保留；改用独立r1清单157输入＋198份冻结构建工具，未消费错误候选。新隔离离线构建实际安装27个锁定依赖、原PG库和许可；镜像81e6305...，清单bb25c5...／5592文件／472708149字节，原生pg_restore16.15与依赖loader检查通过，独立构建证据审查批准。
13. 新固定安装入口的独立冷启动once002实际通过：原容器c92224b8...、原PID／start保留；单独观察器先读取同容器PID1的namespace／start／exe，Root由原始观测独立推导基线后才发送1817字节合成关联请求。实际安装worker返回完整148240字节v3帧，完整原请求／策略／两次N3观测验证通过，退出0。独立审查用封存wheel重解析全部原始数据并确认摘要一致。该点证明独立容器内的固定入口兼容，仍不等于新PG目标对、完整宿主owner、TargetHandle、SQL恢复或矩阵。构建和冷启动两个成功容器在归档／独审后正常删除；本轮成功公共辅助容器累计清理10个，当前镜像和原B3失败现场保留。

## 当前正在完成

Slice3C2宿主owner设计已完成并经过独立审查，补齐固定15秒helper窗口、原main启动前锚定的60秒接纳窗口、保留UNKNOWN的显式RETAINED处置分支和payload／frame+4字节边界。Task1原始目标生产者包装器与独立执行日志开始开发；严格PG工厂8状态／8记录不变。Task1不提供可运行ATTEST，后续固定观察器、并发流、caller/supervisor闭合图和实际新目标对仍需各自验收。

公开上游资料仅解释语义，不冒充当前vendor内核字节证明。实际IPv6netlink只有::1，而proc另外显示两条拒绝哨兵；匹配上游rt6_dump_route显式跳过nullentry，proc格式化器直接输出它。规则不能按lo名称接受任意默认路由。

## 仍需完成

宿主原句柄执行所有权／闭合graph／双端kernel namespace证明；新增固定观察器后再次构建／来源验证；已批准bootstrap身份交接与实际PG验证；角色、凭据、完整SQL／媒体／配置／新控制CAS／实际只读应用；完整恢复矩阵与故障验证；新制品回归、桌面交付及后续生产验收。上述项目均未因纯数据校验、组件或独立冷启动成功而自动关闭。完整 B3 新任务开始前仍须满足原13,824,425,984字节容量门槛，本轮没有降低门槛或启动完整任务。

## 证据入口

总目录 outputs/_run/b3-worker-artifact-preparation-20261104。关键记录：public-worker-once001/RESULT.safe.json；build-and-cold-status.safe.json；network-amendment/approval.safe.json；netlink-parser-review/fix；netlink-transport-review/fix；N2-ACTUAL-REVIEW.safe.md；n2-public-component-once001和once002/RESULT.safe.json；public-helper-cleanup-once001/RESULT.safe.json；n2-public-component-cleanup-once001/RESULT.safe.json。

N3 完整冻结与复审：profile-literal/ROOT-freeze.safe.json；network-profile-source-review/ROOT-source-freeze.safe.json、ROOT-unit.safe.json、REVIEW.safe.md、ROOT-M1-unit.safe.json、REVIEW.M1.safe.md。N4 拆分计划见 docs/superpowers/plans/2026-11-04-b3-network-worker-integration.md；v3 详细字段见同目录 2026-11-04-b3-network-v3-wire.md。

N4a：network-reader-review/REVIEW.safe.md、REVIEW.M1.safe.md、ROOT-M1-unit.safe.json；n4-reader-public-component-once001/RESULT.safe.json、ACTUAL-REVIEW.safe.md；n4-reader-public-component-cleanup-once001/RESULT.safe.json。

N4b与新版实际制品：network-v3-source-review/REVIEW.I1.safe.md、ROOT-I1-unit.safe.json；current-wheel-once002-ROOT-RECORD.safe.json；public-worker-once002/ACTUAL-BUILD-REVIEW.safe.md；cold-worker-once002/ACTUAL-COLD-REVIEW.safe.md。Slice3C2设计：docs/superpowers/plans/2026-11-04-b3-execution-owner.md；outputs/_run/b3-execution-owner-design-20261104/REVIEW.framing.safe.md。

后续用户要求清理旧测试资源：另删除21个过时B2／辅助容器和15个旧B2镜像，227个卷全部保留，当前B3和最新B2保留。此项不与上述10个本轮成功公共容器重复计数，见2026-11-04-docker-obsolete-tests-cleanup.md。旧B2失败记录保留为历史证据，但相应旧Docker容器／镜像已按此次要求删除。
