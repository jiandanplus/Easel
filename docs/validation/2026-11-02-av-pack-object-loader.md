# 受限 Git pack 对象读取器：组件开发与验证

2026-11-02，继续 `codex/ui-redesign`，补齐原AV源码获取Task3中缺失的pack→read_object适配。所有本次输入均为小型合成pack；原实际候选未解码。

新增 `services/platform/scripts/av_sources/pack_store.py`。读取固定大小/SHA256的私有pack，逐段检查PACK容器、zlib输出、全部普通对象与OFS/REF delta；支持前向REF、混合delta链、正确的copy/insert编码与重复同一对象。只允许pack内base，无外部回退。解析结果以不可变(type,bytes)接到原 `verify_commit_tree`，验证完整提交、目录树、blob、mode、symlink与gitlink；gitlink不递归加载。

保留原预算：单pack64MiB，inflate/resolved各128MiB的独立逻辑计数，单blob/delta程序16MiB，commit/tree/tag4MiB，最多60001条目、delta深64及600秒协作时钟。读/解压调用每次最多64KiB，输出只允许正max_length，严格检查EOF、长度、trailer与SHA；文件所有权、0700/0600、单link和全路径nofollow，以及打开/解析后/还原后的同文件身份均检查。逻辑字节计数不能相加冒充物理堆大小，也不代表AS/CPU/硬wall隔离。

保存了初始缺模块RED、delta缺失RED、超大clock溢出RED以及还原期间文件变更RED。Root初次执行199 passed /0.42s。独审指出名为mutual-cycle的原fixture实为两个自环；这个199通过记录保持原样，不能再称覆盖互环。修正前的fixture身份断言RED得到1 failed/127 deselected；修正后增加真正双节点REF循环、REF/OFS混合循环及REF依赖另一个delta输出的正例，并核原始编码引用、实际目标OID与resolver错误。

最终Root新鲜执行：`rtk proxy services/platform/.venv/bin/python -I -B -m pytest --noconftest services/platform/tests/test_av_pack_store.py services/platform/tests/test_av_source_git_objects.py -q`，**204 passed in0.45s，exit0**（133新用例＋71原verifier用例；不是全项目测试计数）。作者的204/0.42s是另一次运行。新测试在collection显式添加repo namespace路径，因此此隔离组合命令应保持新测试在前；原测试没有改动。Root的8项原输入基线全部逐字节一致，作者另对9个原模块＋原verifier测试检查一致。

源码SHA256 `30fa4928e766dfc4137f0b367d5d134c1523a06bc7f7eed4827fbc77d15ca3a6`；最终测试SHA256 `78a3fe8d452d6f1f9e4382f5fe4457c043b986c993013aad27d67fed957bd9cc`。签名、上游来源认证、构建采用、OS/DNS-IP隔离标记均未提升。没有读取/解码实际公开pack，没有提取或执行上游源码，没有创建/启动容器、访问服务器/供应商/账号/钱包或改前端/原17868/订阅/自定义接口，没有新打包、激活、提交、推送或合并。

下一步需专用AV离线worker/controller及内核限制、负控和一次性生命周期，再处理实际pack并绑定tag签名、commit/tree/gitlink与配对Vulkan树。现有PG验收模板权限与资源参数不适合直接调用；现有远端systemd archive gate与待批载荷保持不变。已只读核对本机固定Python镜像可复用，未pull/build/run；ARM64组件校验不能替代原Linux/amd64工具链验收。完整FFmpeg9.0.2原76-token能力、AV19、B2/B3/T13/T14、SourceFacts/factory/FWPD/真实DR及正式桌面发行仍需各自证据。

[实施报告与原始测试日志](../../outputs/_run/av-pack-loader-20261102/task-1-report.md)；[Root最终纯验证](../../outputs/_run/av-pack-loader-20261102/ROOT-PURE-r2.safe.json)；[首轮独审及P2](../../outputs/_run/av-pack-loader-20261102/TASK1-REVIEW.safe.md)；[执行器就绪审计](../../outputs/_run/av-pack-loader-20261102/LOCAL-EXECUTOR-READINESS.safe.md)。

最终[有界复审](../../outputs/_run/av-pack-loader-20261102/TASK1-REVIEW-r2.safe.md)已完成：规格符合性PASS、代码质量PASS，R1及补强项均关闭，无未关闭发现。Root完整读取复审报告并核SHA256 `2d2d985a64f8f12ee02f18398d026060807a5b8a2a5f561f11468d3ec3e90113`；未重复执行已通过且未再改动的测试。此结论仅为离线读取器组件闭合。
