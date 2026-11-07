# AV 宿主解释器修复与只读运行验证

2026-11-03，codex/ui-redesign。当前执行器原来将 guardian 固定到 /usr/bin/python3。实际一次只读检查发现本机该路径是 CPython3.9.6，不能解析受保护 Docker 状态校验器允许的纳秒时间戳。相同程序换成项目的 CPython3.12.7，并使用相同最小环境和 -I/-B 后成功；原系统解释器结果保留，没有降低时间戳校验要求。

执行器现在捕获已认证父进程的绝对 sys.executable，拒绝 CPython<3.12、未知实现、空／非字符串／相对路径、当前路径漂移，以及缺少隔离或禁止字节码标志的运行环境。默认依赖构造在 API 工厂／创建资源之前检查，guardian 启动与默认入口也检查。解释器二进制仍由实际操作者预先认证和固定；这些兼容性检查不等于独立的二进制认证。

仅修改 pack_executor.py 和 test_av_pack_executor.py；14 个受保护文件字节不变，原 R2 文件及本轮失败／审查记录全部保留。最终 Root 新鲜精确回归：84 passed，21.85秒，exit0；独立规格／质量审查通过，额外的路径黑名单测试可移植性问题已关闭。完整报告与差异见下方链接。

## 实际观察及失败归因

三个 host-only case 分别冻结并各执行一次，不是全程只有一次调用：

- R1 在文件认证阶段 exit70。只读谓词诊断确认唯一不满足项是 GID：专用 /private/tmp 输入继承了 group0，而运行进程要求 group20。未进入 operator 执行／FFI；原目录和失败记录保留。
- R2 为新目录／nonce，显式设置所有专用目录和文件到当前主组。已完成真实模块认证、preflight stage preparation 和配方构造，但在 boot ABI 阶段 exit70。限定诊断确认默认沙箱内 kern.boottime 返回 -1/EPERM；沙箱外同一只读调用返回0，结构长度16和字段范围有效。两份诊断没有导出原始时间。
- R3 在沙箱外运行相同批准的 operator／probe／8helper 字节，使用新冻结的专用目录／nonce和明确的当前主组。Child exit0，0.09063141699880362秒，stderr0；实际 stage preparation 通过，两次 boot 哈希一致，最终源文件／运行时身份和哈希复核通过，原5秒时限内返回。前两次失败未删除或改写。

目录为 owned0700，公开输入及记录为0600单链接；没有账号、密钥、真实 pack 或业务数据。选定运行时、实际二进制、操作器、descriptor、profile及8helper均有冻结哈希。独立实际材料审查核对了限定私有目录、once intent、全部来源绑定和公开／私有结果一致性，支持 R3 的窄范围 PASS。原生调用和时长归属于 Root 的实际执行，审查者没有重跑原生调用。

## 完成边界

本次实际通过的是独立认证子进程的 operator 模块加载、实际 preflight stage preparation、配方生成和 Darwin 只读 boot ABI；没有运行产品 BOOTSTRAP、launch_guardian、guardian_main、worker main 或 Docker。小程序的5秒超时和结果检查不代表通用恶意进程的硬输出／资源封锁，也不能宣称完整 guardian 调度或控制器生命周期通过。

VM／日志容量仍未证明。接下来仍需原生 guardian／容器生命周期、AS／CPU／wall 控制、接近16MiB的产物保留、真实源包及原始来源链验收；完整 AV、FFmpeg、B2／B3、F2、恢复、签名公证和最终发行目标仍开放。没有修改前端、原17868登录／历史／配置，没有调用模型或修改钱包，没有 commit／push／合并。

冻结源码 SHA f5b0f7ed5131497c41b002771c193da672734129b9f2129c3e028f0cc58a8c0c；测试 SHA17ee284a59afdc67122beb70f8e284be9c986a866272cb71bb7e956ad5591a84。R3 freeze SHA c05c08610289dd031498994a883675c073bd3cb6bc4cc45a6c70eab21aad6d13。

[Root 回归记录](../../outputs/_run/av-pack-interpreter-20261103/ROOT-PURE-r2.safe.json)；[源审复审](../../outputs/_run/av-pack-interpreter-20261103/INTERPRETER-REVIEW-r2.safe.md)；[R3 实际记录](../../outputs/_run/av-pack-interpreter-20261103/ROOT-HOST-READINESS-ACTUAL-r3.safe.json)；[实际证据独审](../../outputs/_run/av-pack-interpreter-20261103/ACTUAL-HOST-REVIEW.safe.md)；[完整实施报告](../../outputs/_run/av-pack-interpreter-20261103/REPORT.md)。
