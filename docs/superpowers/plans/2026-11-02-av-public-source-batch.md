# AV公开源码获取：双来源共享预算接入

依据既有2026-10-28源码获取实施账Task3，补齐当前缺失的双来源共享预算协调器。当前完整目标仍包括全部AV19、原76-token FFmpeg9.0.2能力、完整来源/依赖/构建/隔离/业务验收。不能以下载候选替代这些门。

## 当前事实与边界

已读现有bounded_fetch.py完整源码及smart_http.py。fetch_pack仅接受固定libplacebo与Vulkan-Headers commit，每次单独创建预算；README显式source_identity_verified/build_inputs_adopted/os_resource_limits_verified=false。直接调用两次会重置128MiB/4MiB/64请求/600秒上限，不能冒充一批共享预算。现有工具没有pack对象loader/签名到完整树绑定；Linux sandbox是archive-worker离线门。远端第四份精确载荷批准仍待答复，不重复发问，不执行SSH、远端载荷、提取或编译。Mac当前约17GB自由空间也不满足原完整编译预算＋reserve；不删功能或抬高资源门。

本阶段只增加必要协调代码、纯测试、独审；通过后Root才另行固定本机匿名公开获取的具体一次准入。候选仍未认证/未采用/未证OS硬隔离，不接触真实凭据/模型/业务数据/原17868或前端，不commit/push/merge。

## 全局约束

- 新文件services/platform/scripts/av_sources/source_batch.py及services/platform/tests/test_av_source_batch.py；原4个获取/协议/对象/归档模块和旧测试不改。
- API fetch_sources(destination, limits=HttpLimits(), *, _fetch=None, _clock=None) 固定依次libplacebo、vulkan_headers；无调用方URL、commit或任意source选择，不读取env/auth。
- 复用原HttpLimits上限：HTTP metadata4MiB、单响应/pack64MiB、解码body和原始HTTP input各128MiB、请求64、request20秒、整批600秒。一次固定绝对monotonic截止，第二源只获剩余body/input/metadata/requests/clock预算；所有传入剩余限制只能收紧。失败立即停止，不重试或开始后续来源。
- metadata/body/input是原meter的独立计数，advertisement可能同时属于body和metadata，不把它们相加当input。所有数量严格int排除bool，非负且受实际下发限制约束。响应上限不误当两个响应的总body上限。clock必须有限、不得倒退；失去预算不创建/启动下一源，不续期。
- 使用原私有目录/no-follow/exclusive文件规则；新owned0700批目录及两个新child，0600排他小receipt，失败候选保留。原模块private目录/文件helper可复用，不能覆写已有目录/文件，不扫用户工作区。只有单trusted writer，无恶意同UID并发保证的扩大声明。
- 验证每源receipt完整身份/固定URL与commit/目的地/计数/pack容器hash及sourceflags；原fetch_pack负责有界wire内容，不把wire checksum当Git对象或签名认证。任何unexpected adopted/identity/OSflags均拒绝。默认真实transport与明确_fetch seam分开记录，测试注入绝不能被称实际公网获取或hard-egress隔离。
- 批receipt只含固定公开URL/OID、文件hash、预算/计数、状态；不保存HTTP raw/error message/env/密钥。成功只在两个源与receipt校验后给出candidate结果。失败/中断保持失败，已完成来源和partial目录保留，未观测的最后请求/字节不能填0冒充完整计量；不能让failure receipt写入错误制造success或吞原异常。
- source_identity_verified=false, build_inputs_adopted=false, os_resource_limits_verified=false, dns_ip_egress_verified=false保持明确。现有socket/cooperative时钟不是硬OS wall/memory/netns证明，代码和文档不得混淆。
- 测试仅stdlib/原inert模块/合成receipt和新私有临时目录；_fetch必须fake，不执行network/socket/Git/subprocess/Linux/Docker/产品/DB，不跑main，不读真实配置。

## Task1 共享预算协调器和纯回归

1. 阅读上列4原模块接口、现有源码获取plan、README与任务约束。
2. 有意义RED：双源共享计数/剩余限制、固定整体deadline不重置、首源失败不调用次源、恶意receipt/flag/身份/计数及已有目标不能通过。
3. 实现最小API/私有结果写入，不改变单源行为，记录partial未知状态；默认limits不能放宽。
4. 验证正常两源、边界收紧和耗尽、clock非有限/倒退、负数/bool/超限计数、wrongsource/url/commit/path/packhash/flags、测试seam标签、父目录/symlink/已有目标、失败/中断保留与不续跑；不用真实大文件充当预算测试。
5. 精确命令 `rtk proxy services/platform/.venv/bin/python -I -B -m pytest --noconftest services/platform/tests/test_av_source_batch.py -q`，保存真实RED/GREEN及源码/原保护输入SHA。写outputs/_run/av-public-acquisition-20261102/TASK1-REPORT.safe.md，完整代码与测试纳入独审。

## Task2 独审、Root具体获取与后续真实来源链

源码/纯测试通过不代表网络实际可用。Root在另一次具体记录中固定完整source/helper字节、parent/destination、aggregate预算、失败保留/不重试、无凭据、公开源和用途后，只执行一次默认真实transport候选取入。失败查原handle，不凭观察超时重启。公开输入可在本机隔离候选目录处理；不扩大任何远端载荷授权。不能在尚无完整对象loader/tag绑定、Linux硬资源/网络证据时提取/运行/编译或声明来源完整认证。

下一必要代码为受限pack对象store→原verify_commit_tree→已固定tag签名/commit绑定；按真实缺口另写精确实施任务。完整原能力/工具链、资源/实际API/AV19和生产恢复验收继续保留，不把本小步当最终目标。

## 已执行结果

Task1 76纯测试及独立代码审查通过；Task2 固定本机匿名一次候选获取实际exit0、4请求、10.45121秒，Root有界文件hash/回执校验及独立实际材料审查通过。见 `docs/validation/2026-11-02-av-public-source-batch.md`。仅候选获取闭合，全部来源身份/采用/OS/DNS标记仍false；下一步完整对象loader/签名/tree绑定及原全部AV门未关闭。
