# AV诊断载荷：已获准单次Linux运行失败收口

**本次真实单次运行失败，隔离／来源验收未通过；已取得安全的首个失败门。** 新载荷获用户明确一次授权后，root仅尝试一次，SSH终态exit1、无观察超时；旧授权不复用，无自动重试。本页是独立实际执行增量，原[本地262项验证](2026-10-29-av-kernel-diagnostics.md)及28文件证据原字节保留，不以本地PASS改判真实Linux通过。

## 授权、时间与确切故障

[本次授权](../../outputs/_run/central-av-kernel-diagnostics-20261029/execution-evidence/run/authorization.safe.json)绑定154.217.245.179、新stage「/root/easel-av-source-run-20261029-791399b0dc890cf0」、unit「easel-av-src-791399b0dc890cf0.service」，5文件82,321字节／bootstrap111,794字节。manifest SHA为「2f13a72476accacea6408da5b336656f37a81501e6d01a9512b373551a1f688c」，bootstrap SHA为「035655a614be107bd77b2c26147b539e782de1147b889e4b4749d8ff377440e1」。批准UTC07:03:24，尝试07:04:17，终态07:04:58，后置快照07:05:59；尝试记录声明strict host key、batch mode、无agent转发、attempt1。此为root收集的记录，不是本归档另行远端取证。

[实际终态](../../outputs/_run/central-av-kernel-diagnostics-20261029/execution-evidence/run/run-result.safe.json)及stdout为155字节单条OperatorError/status=failed JSON，stderr为空；二者大小和SHA与安全回执一致。[后置安全快照](../../outputs/_run/central-av-kernel-diagnostics-20261029/execution-evidence/run/remote-after.safe.json)中的worker拒绝是：

kernel_collect / mountinfo / duplicate / {count:24,target:OTHER}

**24是首次遇到重复挂载点时的第24条记录序号，不是24个重复挂载。** OTHER仅是非5个固定目标的类别；原始mountinfo和worker stdout没有进入本批归档，不能另证真实路径、叠挂来源、ro/rw、合法性或安全性，也不能据此放宽规则。失败在collector，完整runtime validator尚未通过，不能把先前读取过的资源、namespace或capability认定为合格。worker stdout的286字节／SHA仅是后置快照声明，本归档不冒充拿到原始字节。

## 残留与指定对象保全

work_children为空，result及operator成功回执均不存在；结合冻结代码的sandbox首门，与来源处理前拒绝相符，没有GPG／完整提取／编译／构建成功证据。unit后置not-found、MainPID与ControlPID均0、专属cgroup absent，支持该快照时此unit没有残留进程。**已回收unit显示的默认Result=success／ExecMainStatus=0不能覆盖实际exit1。**

[保全回执](../../outputs/_run/central-av-kernel-diagnostics-20261029/execution-evidence/run/preservation.safe.json)及前后快照显示4个指定服务的身份、PID／启动时间／状态相同，3个指定容器完整记录相同，3份公开输入size／SHA相同；4源码加manifest共5个payload pin与批准载荷相同。本归档再次对这些本地快照、stdout／stderr和摘要核对。该范围仅证明指定对象身份／摘要在观察区间内一致，不是全生产、业务数据、资金账本、延迟或全历史审计，也不证明所有宿主进程均已检查。

[实际运行独立复核](../../outputs/_run/central-av-kernel-diagnostics-20261029/execution-evidence/reviews/linux-run-independent.md)认为指定安全材料内部一致，支持重复点拒绝、失败终态及上述边界；它没有执行代码、测试、SSH、网络或内核探测，没有独立重放整个远程链，也不给下一次授权。

## 增量归档与后续门

[执行证据目录](../../outputs/_run/central-av-kernel-diagnostics-20261029/execution-evidence)复制12份精确白名单文件，含8份运行／保全文件、root原本地归档复算回执、两份只读运营脚本及独立报告；加README／copy-manifest共14文件，集合闭合。[复制清单](../../outputs/_run/central-av-kernel-diagnostics-20261029/execution-evidence/copy-manifest.json)SHA为「9fb8cf09e0e587ac073c4a418a674421a083878dd5ed5a2bb40a94e2c31559ad」，每项源before／copy／after SHA一致；脚本仅复制不运行。原本地归档manifest「a94b4815dc63948c74f432ad4227d722d81ecdfc620b6e752e0ff2919f8bba5e」及本地验证文档／safe原字节保持，原payload-plan false冻结字段不改，本次批准另记。

本次一次授权已经消费，不包含retry、重新启动旧unit或新建后续运行许可。后续真实定位、签名／完整提取、依赖、编译profile、API UID／systemd、供应商及生产验收均未由本轮完成。本归档无网络／SSH／模型／钱包或raw业务访问，不执行脚本／候选／测试，不改总账、代码、前端及v33包，不提交／推送／合并。
