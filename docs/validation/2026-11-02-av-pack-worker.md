# AV pack worker：源码与纯协议验证

2026-11-02，`codex/ui-redesign`，原完整AV源码/工具链目标中的本地worker组件。此文只记录源码和合成协议验证，不代表实际Linux资源门、真实pack、宿主控制器或整版交付通过。

新增pack_worker.py与pack_runtime.py及两份纯测试。专用PID1监督进程管理一个非PID1子进程；资源合同保持AS512MiB/CPU30s/硬定时器120s、cgroup512MiB/swap0/PID16、只读输入和32MiB工作tmpfs。代码先核资源/信号/挂载/cgroup事实与私有输入/代码pins，再调用不变的pack_store和git_objects完整校验与清单生成；实际kernel效果仍需后续负控。

独立首审发现2项P1、3项P2，均已修复：实际检查/解除继承的SIGALRM屏蔽并确认默认处理与无pending，TERM/INT跨fork归属交接，移除runtime模块无界读取回退，所有父进程失败路径的owned-child停止/回收，首次profile快照UID+GID与完整入口测试。复审再发现假管道绕过真实FD/交接、pre-proof错误阶段丢失；连接队列/每进程FD所有权/EOF模型与准确失败阶段已修复。原53与102测试记录、失败测试和审查全部保留，没有改写成最终通过。

最终Root新鲜运行：`rtk proxy services/platform/.venv/bin/python -I -B -m pytest --noconftest services/platform/tests/test_av_pack_worker.py services/platform/tests/test_av_pack_runtime.py -q`，**116 passed in0.98s，exit0**。测试覆盖五种模式的真实main/child_protocol代码，通过显式假的系统调用适配器完成，没有真实fork、信号、资源修改、mmap、CPU忙循环、线程或容器操作。合成pack经过原解析器/完整目录树校验并写出全量清单；注入标记强制为true，默认宿主校验拒绝，不能冒充实际Linux结果。

Root完整读取原四文件及R2/R3全部变更与报告，所有最终pins和原保护输入一致。最终workerSHA256 `3d40d6608428e6e689e7fe545609aaa2c21ab747ed0f75acbb75d7f5f05e10a4`；runtime `c08ffd6b2b92a0b42ccdacaa1ec143d231da0c7fbb93ad8ce13c96358b4c90e2`。独立R3规格/质量复审通过，Root完整读取并核报告SHA `c23208e172a0f6a73cfe76fa8a4df5ad21e1c312f6093495da25f748174c4163`。这关闭Task1源码/纯协议项，没有关闭Task2。

宿主使用共享校验器前，必须prepare_runtime(profile,code_dir=私有0700/0600固定代码目录)，保留同一已核字节的模块；没有隐式源码树读取回退。成功报告仍为av-pack-worker-report-v1；错误为固定av-pack-worker-error-v2加allowlisted phase，排除raw异常/环境/路径/载荷。新signal proof字段strictbool；完整schema见R2/R3报告。

交接设计另发现一个真实生命周期矛盾：[Docker官方tmpfs文档](https://github.com/docker/docs/blob/main/content/manuals/engine/storage/tmpfs.md)明确容器停止即丢失tmpfs文件，因此原计划“终态后GET /work清单tar”不可实现。已在任何实际任务前修订Task2为独立、有界stderr公共清单通道：stdout32KiB与metadata mux64KiB保持，完整manifest16MiB保持，源输入只读与work32MiB不变；新local json-file保留上限与sameCID/terminal/deadline/全清单hash校验必须显式冻结并独审。此替代尚未编码或实测，不称完整清单已在终态交付。

本轮仅只读核对现有Docker29.5.3/API1.54及v1.49兼容镜像身份，没有create/start/pull/build/重启/清理，也没有实际pack读取解码、源代码提取执行、SSH/服务器、模型/资金/登录、前端/原17868/历史/订阅/自定义接口、打包激活或commit/push/merge变更。私有cgroupnamespace根视图语义按kernel官方文档校正，外部HostConfig.private证明仍须Task2实际inspect；ARM64本地字节校验不替代原AMD64/ABI/远端archiveROOT门。

[Root最终纯验证](../../outputs/_run/av-pack-executor-20261102/ROOT-TASK1-R3-PURE.safe.json)；[最终复审](../../outputs/_run/av-pack-executor-20261102/TASK1-REVIEW-r3.safe.md)；[R3报告与RED/GREEN](../../outputs/_run/av-pack-executor-20261102/task-1-r3-report.md)；[Task2清单传输修订](../../outputs/_run/av-pack-executor-20261102/ARTIFACT-RETENTION-REVISION.safe.md)。

后续：Task2完整清单producer/consumer、宿主受限API/一次执行/同boot原wall/fresh配置与产物围栏；然后实际preflight、AS/CPU/wall负控与合成大清单，再处理固定真实pack/commit/tree/gitlink/tag签名链。原76-token FFmpeg9.0.2全能力、AV19、B2/B3/T13/T14、SourceFacts/factory/FWPD/真实DR及正式发行仍保留，不以本组件纯测试替代。
