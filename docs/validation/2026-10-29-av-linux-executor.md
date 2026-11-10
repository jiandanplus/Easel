# AV Linux 执行器与首次真实运行记录（2026-10-29）

本地执行器实现、测试和限定独立复审已完成；首次真实Linux运行在来源处理前被内核状态检查拒绝。**安全提取尚未完成，不能将本记录写成AV能力或Linux正向验收通过。**

## 本地交付与精确归档

冻结执行载荷为4份代码和1份manifest，共71080字节；manifest SHA256为`c5dd053be8deb630cb80f88ea742f2cd3695700c518df484bda806b94ee5f157`。外层bootstrap r2为96806字节，SHA256为`821470b4f97a717cd07a6a5c7cdfafe8880a9a66091d45cd934b41f257c4ca70`，强制isolated、禁写字节码并拒绝优化，先校验manifest及全部代码摘要后才导入operator。归档过程没有运行该脚本。

[证据目录](../../outputs/_run/central-av-linux-executor-20261029/evidence/README.md)按明确白名单复制34份文件；加README和[copy-manifest](../../outputs/_run/central-av-linux-executor-20261029/evidence/copy-manifest.json)共36份、312988字节；copy-manifest SHA256为`56c57b585480fdc3c972a818b02837e1816a87e7922b2cd69d5fc022feb9769b`。源复制前、副本、源复制后字节和SHA逐一相同，文件集合闭合于本evidence子树；同级原始payload和运行记录保持原样。

本地root三模块联合测试为**212 passed，0.61秒**，对应Task A 116、Task B 31、Task C 65。独立r2审阅确认本地候选的路径语义、权限/资源证据校验、固定签名归档worker、有界日志PIPE和单次operator契约；这些测试、合成探针和静态PASS没有代替真实Linux内核、GPG及归档提取验证。详见[联合日志](../../outputs/_run/central-av-linux-executor-20261029/evidence/reports/easel-av-linux-integrated-r2-20261029.log)及归档的A/B/C/Bootstrap报告。

## 唯一一次实际运行

用户另行明确批准本份5文件和一次离线运行，root通过严格主机密钥校验的SSH，以`python3 -I -B`将载荷送到`154.217.245.179`的新私有目录`/root/easel-av-source-run-20261029-7db1802b4289c76f`，并仅启动`easel-av-src-7db1802b4289c76f.service`一次。未复用此前13文件的授权，也未自动重试。[执行前计划](../../outputs/_run/central-av-linux-executor-20261029/evidence/run/payload-plan.safe.json)里的uploaded/executed=false保留其历史快照含义；[实际尝试](../../outputs/_run/central-av-linux-executor-20261029/evidence/run/run-attempt.safe.json)、[结果](../../outputs/_run/central-av-linux-executor-20261029/evidence/run/run-result.safe.json)和单位日志记录执行后事实。

单位实际启动了worker，约290ms后退出1。worker给出`SandboxError`；operator将失败保留并返回`OperatorError`，SSH观察者退出1且没有超时不确定性。`work`没有result，尚未进入公开来源处理、gpgv、验签、成员检查或提取，没有编译、安装或生产发布。不能从不存在单位的show属性`Result=success`反推作业成功；明确的systemd-run exit1和journal failure保持为终止证据。

当前class-only输出无法区分collector失败和validator失配。[只读诊断](../../outputs/_run/central-av-linux-executor-20261029/evidence/reports/easel-av-linux-first-run-diagnostic-20261029.md)列出的重复挂载、cgroup路径、swap控制文件、capabilities、work权限和proc探测均是**未证实分支**，没有具体根因结论。

## 失败现场与保全边界

[只读保全回执](../../outputs/_run/central-av-linux-executor-20261029/evidence/run/preservation.safe.json)及保存的before/after比较显示：原4个服务身份、3个容器身份和3份公开来源输入size/SHA相同，5份载荷size/SHA一致。新临时单位已collected，cgroup为空或移除，没有source result或operator成功回执；现场保留，无自动重试。

该结论限于记录的两次身份和摘要采样，不是整个生产状态、全时段或财务审计。归档者只读取白名单中的代码、测试和安全记录，未访问网络/SSH、业务配置/密钥、账号/钱包或真实用户工作区，未执行或import任何worker、operator、bootstrap、测试或verifier。root的实际运行事实来自所提供的原始安全回执和限定日志，本归档不是另一轮远端重放。

## 下一门槛

先补固定`failure_stage/gate`枚举及少量白名单内核事实，保留来源处理前拒绝、gate前不写文件、不输出任意异常文本或宿主挂载正文；据实际脱敏事实再确定是否需要表示兼容性修复。新代码载荷和新一次单位运行应列出具体文件/字节/SHA及输出范围后另行确认。不得放宽原安全要求、复活旧单位、自动重试或换执行器绕过本次一次运行范围。

源码、测试、已有计划和历史报告均未由归档任务修改；没有提交、推送或合并。
