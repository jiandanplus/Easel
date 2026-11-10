# AV 叠加挂载兼容：下一步本地实施计划

## 依据与当前未知

第二个明确获准的单次 Linux 作业已失败关闭：`kernel_collect / mountinfo / duplicate`，首重复记录序号24，分类OTHER。实际路径、第二层访问标志、层数及可见层均未知。来源处理尚未开始；旧两次运行、载荷与证据保留。本计划不授予第三次远端运行。

当前 `_mount_facts` 以路径为唯一键，所有重复点均拒绝。先在本地实现能够表示 mount ID／parent ID 的纯模型及反例；未取得充分的可见层证明时保持拒绝，不能根据OTHER、记录顺序、最大ID或“可能都只读”直接跳过重复。

## 已核对的当前接口资料

- [Linux man-pages mountinfo](https://man7.org/linux/man-pages/man5/proc_pid_mountinfo.5.html)：同一点叠挂时，新层的parent是此前被覆盖层；同点栈顶还可能被祖先叠挂遮住。chroot之外的父挂载可能不出现在本进程视图中，不能把所有缺失parent简单当损坏，也不能任意允许缺失。
- [Linux man-pages fdinfo](https://man7.org/linux/man-pages/man5/proc_pid_fdinfo.5.html)：自身文件／目录描述符的mnt_id与挂载标识关联。固定目录FD可交叉核对算法选出的实际可见层，不能单独证明整个子树安全。
- 按AGENTS完成Context7 library解析和两次docs查询，共3命令：`/websites/man7_linux_man-pages`的mount文档命中；fdinfo查询返回相关statmount资料但不够精确，具体fdinfo语义以上述直接原始页面为准。不引入需要额外能力的新挂载系统调用，不据检索噪声修改既有capability约束。

## 本地开发顺序

1. 新增独立纯挂载表模块／测试，先RED。所有记录保留ID、parent、规范路径、mount与superblock访问位，拒绝超限、重复ID、坏数字、冲突访问位、循环和无法证明的根；不导出任意路径。原冻结模块／payload副本不改写。
2. 用合成图覆盖同点RO/RO、RO/RW、RW/RO、祖先覆盖与隐藏可写子挂载、记录乱序／ID非顺序、外部root parent、分叉／循环、两个OTHER路径不可合并、普通只读bind与可写superblock。保守算法可以多拒绝；不得误接受可写代码／输入／cgroup或新增可写路径。
3. 在可见性模型完整审查后，设计固定五目录 `/`、`/code`、`/inputs`、`/work`、`/sys/fs/cgroup` 的自身FD mnt_id采样与首尾稳定性核对。只操作固定目录和本进程刚打开的FD，不按mountinfo派生任意路径，不打开三份公开来源文件、不读取业务数据或宿主其他进程。固定输入文件的hash／读操作继续留在原sandbox成功之后。
4. 由纯模型与FD证明得到原validator所需事实，完整保留root身份、cap、namespace、资源、只读／唯一work与既有API/tmp例外。必要证据缺失、快照变化或实际访问不符仍失败；有限诊断只输出固定分类、数值和布尔，不泄露路径或原文件。
5. 独立安全审阅、真正RED/GREEN、原回归与合成集成之后，再冻结全新具体载荷／stage／unit，形成可审阅的一次运行请求。两个旧批准均已经使用，不复用、重启或自动重试。

## 验收范围

本计划只接续完整交付目标的AV门，不改变前端、v33包、账号、钱包、模型配置或生产；本地模型／合成Linux事实不能替代实际Linux、签名、提取、编译、供应商或生产验收。工作区接续、F2真实恢复、macOS原生交互／净机签名、支付暂缓与第二产品等完整总账硬门仍保留。

## 本地实施与冻结结果

- Task A 新纯模型57项：46项首轮RED后GREEN、唯一self-parent根3项先RED3failed／54passed再GREEN57；所有记录按ID/parent建图，隐藏分支也先通过全表一致性检查。五个固定FD必须与有效层相符，输出原facts形状给原validator，未新增可写例外。原始路径留在本进程，不进入错误诊断。
- Task B 固定O_PATH FD观察27项，先RED26failed／1passed；首实现25passed／2failed为根parent0 fixture问题，改为合法外部parent999，未为此放宽模型。随后新27＋旧116＋诊断34联合177passed；旧collector fixture仅补固定FD seam与根parent，原guard断言保留。O_PATH不支持或fdinfo坏／缺／重复、非目录、读失败、快照变化一律拒绝，每个FD finally关闭，不读输入文件。
- Operator仅将新模型列入必须校验的payload成员；新依赖2项真RED后，与原65项联合67passed，缺模型时不能进入layout／unit。
- root唯一8测试文件联合 **348 passed in0.71s**，13输入前后SHA同。独立限定Spec／Quality PASS，无P1／P2，报告SHA `d730b22d91d186045ec83a06af60834f008b6d9fbfa46054656d7af15935e105`。前后mountinfo相同只是采样一致，不宣称原子／无ABA；private namespace、原能力／syscall限制与可信宿主无并发挂载者前提保留。
- v33的904应用源和60构建输入逐SHA保持，未重打包、未访问账号／钱包／预览。一次4096条互不覆盖合成siblings纯解析采样为wall1.625856625秒／CPU1.624566秒，源SHA前后同；不等于154主机预算或峰值内存验证。
- 新冻结目录 `outputs/_run/central-av-visible-mounts-20261029`，5代码＋807字节manifest，共6文件93707字节；manifest SHA `98b7c5cd0fc2fbe5ec7efc776bbfacf3cf556d41ef8be9be8a8ef3cb11b0b0cb`。bootstrap127016字节，SHA `ae8f1a427836c7fa981177f9a1d865edf1d6346812a6917452a86b7710d09f93`，相对前版本仅第8行固定参数／内容变，其余23行相同。AST/base64/copied/current source闭合，旧载荷与两次失败证据保持。
- 新stage `/root/easel-av-source-run-20261029-fadd4a945213cc1e` 与unit `easel-av-src-fadd4a945213cc1e.service`已具体列入新的单次确认；本段写入时未获新回复、未上传／运行。计划false字段作为冻结历史保留，后续批准／实际执行必须另记。本地PASS不保证第三次Linux必过，更不关闭来源、编译、供应商或完整交付门。

## 后续明确批准的一次真实运行

用户随后批准本份六文件及指定单位一次运行。root预检后仅运行一次，SSH exit1，无观察超时／自动重试；该批准已消费。新模型／FD collector已完成，validator进入ROOT挂载策略，诊断read_only=true、own_mount=true、writable_count9，至少一条可见RW后代超原允许子树。9为总数，实际违规类别和数量未提供，不能猜路径或判完整隔离通过。work空，无GPG／提取；单位collected且cgroup不存在；指定4服务／3容器／3公开输入／6payload摘要保持。

独立实际复核及[执行增量](../../validation/2026-10-29-av-visible-mounts-linux-run.md)将前序样本比较与尚未到达的后序policy明确分开，原本地34文件证据不改；执行另有14文件闭集。后续仅做[ROOT派生分类诊断](2026-10-29-av-root-writable-diagnostics.md)，不调整allowlist或旧配置，不授权第四次运行。
