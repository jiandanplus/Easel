# 双来源共享预算与公开候选获取

2026-11-02，继续在 `codex/ui-redesign`。此记录属于原 AV 源码工具准备 Task3，不代表完整 AV19、媒体工具链或发行验收。

新增 `source_batch.py`，固定顺序获取 libplacebo 和配对 Vulkan-Headers；两个来源共享原始 HTTP 输入、解码 body、metadata、请求数与协作截止时间，任何失败立即停止。校验实际私有文件与回执的身份、hash、计数及标记，失败保留候选，不重试、不采用。原四个模块与六个测试文件逐字节对比既有冻结副本一致。

纯测试：作者保存初始 RED 和过期仍派发的针对性 RED；作者 GREEN 76 passed / 0.11s。Root独立执行 76 passed / 0.13s，并在最终获取前新鲜执行 76 passed / 0.12s、exit0。三个时间是不同运行，不相加。代码独审 PASS；报告中一处 RED 时间已按原始日志由0.12s校正为0.11s，无源码变更。纯测试使用显式 fake，不称公网证据。

Root 固定三个模块 SHA、一次性 operator、私有目录、空白凭据环境和原预算后，仅运行一次默认真实 transport/clock；自然 exit0，耗时10.45121秒。固定 libplacebo commit `275f7dea27924cdd34ab2e6c20e448b6bc4fab65`，配对 Vulkan-Headers commit `74d8a6cb930c68ef617b202c3ff3c59d919e086b`。共4请求，原始 HTTP input 4,276,657 bytes，解码 body 4,273,834 bytes，metadata 91,946 bytes；独立计数不相加。各项低于原128MiB/128MiB/4MiB/64请求/600秒协作上限；单请求仍20秒，单响应/pack仍64MiB。

真实候选位于 `/private/tmp/easel-av-public-batch-20261102-r1/candidate`。libplacebo pack 1,088,307 bytes、SHA256 `81af0f77c52fde533fb2cf937060fc59c5acd633f793451e66a190b2bce0033a`；Vulkan-Headers pack 3,080,635 bytes、SHA256 `ded53eaef44fa9cf111384e919aca2bba922c9f456fe6e2f202ce3df2ceef375`。Root重新读取实际文件元数据与有界hash，核对两份receipt、总计、默认真实调用标记、once意图、operator pins、无failure receipt。安全归档manifest SHA256 `24390f9d67e8f6d26d2d63b0a083326f915490ebd0a67735aaa6044d833c01ee`。

候选未解压、未执行、未编译、未加入构建输入。PACK容器校验不是完整对象、tag签名、commit/tree/gitlink来源认证。协作时钟与socket timeout不是硬OS wall/AS/CPU/DNS-IP隔离证明；source_identity_verified、build_inputs_adopted、os_resource_limits_verified、dns_ip_egress_verified均为false。没有访问服务器或复用待批的远端一次载荷授权，没有读取业务凭据、登录、钱包或模型配置；未改前端、原17868工作台、历史、自定义接口或订阅模式，未打包、激活、commit/push/merge。

下一步是有界pack对象读取适配器与完整commit/tree/gitlink核验，再绑定已固定tag签名、补齐Linux硬隔离与完整原FFmpeg能力构建。原AV19、B2/B3/T13/T14、SourceFacts/factory/FWPD、真实DR和正式发行门继续保留。

[代码独审](../../outputs/_run/av-public-acquisition-20261102/TASK1-REVIEW.safe.md)；[Root纯验证](../../outputs/_run/av-public-acquisition-20261102/ROOT-TASK1-VERIFY.safe.json)；[一次准入配置](../../outputs/_run/av-public-acquisition-20261102/LOCAL-ONCE-PROFILE.safe.json)；[实际安全归档](../../outputs/_run/av-public-acquisition-20261102/actual-local-r1-safe/manifest.safe.json)。

实际材料独立只读审查已完成，限定候选获取范围 PASS、无待修问题。审查核对原始/安全回执、一次operator与三模块pins、私有文件元数据、四文件有界hash、PACK header/SHA1 trailer与聚合计数；未重跑获取或解对象。实际exit0来自Root执行观察，审计未独立重现已退出进程的环境。[实际独审](../../outputs/_run/av-public-acquisition-20261102/ACTUAL-LOCAL-R1-REVIEW.safe.md)。
