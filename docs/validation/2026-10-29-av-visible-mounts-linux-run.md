# AV可见挂载：已获准单次Linux运行失败记录

**本次运行失败，已从collector重复记录门推进到ROOT可见写挂载策略门；隔离与来源验收仍未通过。** 用户明确批准的新6文件只尝试一次，root实际SSH终态exit1，无观察超时／自动重试。原[本地348项验证](2026-10-29-av-visible-mounts.md)、34文件证据和修正文档／safe保持原字节；本地PASS及前序采样比较不能替代完整Linux通过，也不授予第四次运行。

## 一次范围与终态

[授权](../../outputs/_run/central-av-visible-mounts-20261029/execution-evidence/run/authorization.safe.json)绑定154.217.245.179、stage「/root/easel-av-source-run-20261029-fadd4a945213cc1e」、unit「easel-av-src-fadd4a945213cc1e.service」，6文件93,707字节。bootstrap127,016字节／SHA「ae8f1a427836c7fa981177f9a1d865edf1d6346812a6917452a86b7710d09f93」，manifest SHA「98b7c5cd0fc2fbe5ec7efc776bbfacf3cf556d41ef8be9be8a8ef3cb11b0b0cb」。UTC批准07:41:45，前置07:41:54，尝试07:42:26，终态07:44:25，后置07:44:32。attempt记录strict host key、batch mode、无agent转发和该载荷attempt1。

[终态回执](../../outputs/_run/central-av-visible-mounts-20261029/execution-evidence/run/run-result.safe.json)与实际155字节outer stdout均为failed／OperatorError，stderr为空；归档复算大小／SHA一致。outer stdout不带unit身份，且可与旧失败内容及SHA相同，不能单独证明本轮；连接依赖root记录的固定授权、bootstrap、目录／时间顺序及后置unit／payload观察，不冒充另一条独立远程取证链。

## 确切拒绝与进展边界

[后置快照](../../outputs/_run/central-av-visible-mounts-20261029/execution-evidence/run/remote-after.safe.json)解析出的worker诊断是kernel_validate／mounts／mismatch，facts为ROOT、read_only=true、own_mount=true、writable_count9。**9是全部可见RW后代挂载点总数，不是9个违规项。** 原始mountinfo、FD及worker stdout未纳入本批；328字节worker stdout／SHA仅是后置快照声明，不能另行核原始字节。真实违规路径和挂载来源未知，不推测任何具体目录或以此放宽规则。

[限定独立复核](../../outputs/_run/central-av-visible-mounts-20261029/execution-evidence/reviews/linux-run-independent.md)结合冻结producer／validator顺序和root采集可信前提，认为collector已返回，固定五FD观察、首尾mountinfo相同、可见图及五点ID核对没有先报错；平台、root身份、能力／网络namespace、cgroup与资源限制的前序比较在该份采样证据上已过。这不是原子／无ABA、持续状态或整套隔离独立认证。

按同一冻结源路径，ROOT read_only与own_mount为true、fs_type与child路径构造约束已过，可将拒绝收窄到**至少一条可见RW后代不在既有允许子树内**。源码允许集合不等于主机实际路径清单；9可同时含允许和不允许项。后续code／inputs／work／cgroup挂载、work权限／可写性及AF_INET／proc1_root拒绝判断尚未走到，不一并宣称通过。独审没有运行代码、SSH、测试或内核采集，没有授权修复白名单或再运行。

## 来源、残留与保全

worker尚未进入来源校验／GPG／提取，work_children为空、result及operator成功回执不存在；主机前置只读核验3公开输入SHA是另一活动，不将本句扩大为整轮没读任何公开输入。没有编译、构建或生产发布成功。

unit后置已collected／not-found，MainPID和ControlPID均0、专属cgroup absent，支持该快照时这一unit没有残留进程。**回收后默认Result=success／ExecMainStatus=0不能覆盖本次exit1。**

[保全回执](../../outputs/_run/central-av-visible-mounts-20261029/execution-evidence/run/preservation.safe.json)和前后快照显示4个指定服务的身份／启动／状态一致，3个容器完整记录含StartedAt一致，3公开输入size／SHA一致，5源码加manifest共6个payload pin与批准相符。归档再次复算本地快照和记录摘要；这是命名对象的身份／散列采样，不是全生产、业务数据、资金账本或全历史审计。

## 独立执行增量

[执行目录](../../outputs/_run/central-av-visible-mounts-20261029/execution-evidence)保存12份精确白名单副本：8运行文件、root原本地归档复算回执、2运营脚本和独审报告；加README／copy-manifest共14文件，集合闭合，每项源before／copy／after SHA一致。[清单](../../outputs/_run/central-av-visible-mounts-20261029/execution-evidence/copy-manifest.json)SHA「a2e229872ee7e023a678c32300297ab281a11073a73c7e6db07c6e7c3eb27019」。原本地manifest「89dfb966e32bc0a540ef844867727f09b6ae77c4173297a1259eca4c2335e8e5」和修正版doc／safe原字节保持；原计划false是历史冻结，本次批准另记，bootstrap／payload／当前源不改。

本归档过程只做本地精确复制／摘要／文档，不执行候选、运营脚本或测试，无网络／SSH／业务raw访问，不改总账、代码、前端及v33 App／ZIP，不提交／推送／合并。本次一次授权已消费，无自动retry、重开旧unit或第四次运行许可。完整Linux策略、来源GPG／提取、依赖、编译profile、API UID／供应商及生产仍未完成。
