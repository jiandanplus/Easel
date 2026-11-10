# AV Linux 隔离失败诊断实施账

接续完整剩余交付目标。v33 原工作台和交付包保持；本批只补首次 Linux 作业在来源处理前拒绝时的安全诊断，不修改前端、账号、钱包、模型或生产服务。

## 已知事实与授权边界

首个已批准的五文件载荷仅运行一次，`verify_sandbox` 抛出 `SandboxError`，未进入 GPG、归档检查或提取。错误类型不足以区分采集失败与校验失配；所有具体根因仍未证实。原目录、单位和证据保留，不重用原意图、重启单位、自动重试或改用其他执行器。

## 本地实现

1. Task A：`linux_sandbox.py` 及新的独立诊断测试。按原顺序标记第一个失败阶段和固定 gate／reason；保留全部原拒绝条件。仅输出对应 gate 的有限整数、布尔、已知路径分类和固定读取状态，未知或超限整份拒绝。提供纯 `sandbox_failure_diagnostic(error)`，不依赖异常文本，不读额外系统文件。
2. Task B（root）：`archive_worker.py` 及新的独立 CLI 测试。失败始终 exit 1，仅允许固定错误类别和经过校验的可选诊断；单行 JSON 最多4096字节。任何格式或序列化失败降为固定失败记录。检查失败仍在任何来源／输出访问之前。
3. 独立复审：确认没有减弱隔离、未执行步骤不被报告通过、任意异常字符串／路径不泄露；真正 RED／GREEN 和三模块回归按实际命令记录。
4. 冻结新代码、manifest、bootstrap 与全新 stage／unit，列明精确字节／SHA。完成可审阅结果之后才请求新一次远端传输及执行授权。此次本地开发不等于远端授权或 Linux 正向验收。

## 验收边界

保留旧负向证据，不把本地合成测试当 Linux 实证。不编译、不安装、不调用模型、不修改付款或未知预留；订阅和自定义模式均保持。仍在 `codex/ui-redesign`，不提交、不推送、不合并。

## 本地实施结果

- Task A：新增34项诊断测试及原116项，共150 passed；原测试文件未改。固定 stage／gate／reason 和事实白名单，错误文本不导出，所有原拒绝条件保留。
- Task B：真实RED8 failed后实现固定错误类别及4096字节完整输出限制；新16项与原worker31项联测47 passed，包括实际validator经worker CLI的首错误和7种恶意结构拒绝。
- root最终唯一联合命令涵盖5份测试文件，共262 passed in0.75s，9份实现／测试输入前后SHA相同。日志和回执见 `/private/tmp/easel-av-diagnostics-combined-20261029.log` 及同名 `.safe.json`。
- 新5文件载荷冻结为82321字节，manifest SHA256 `2f13a72476accacea6408da5b336656f37a81501e6d01a9512b373551a1f688c`；bootstrap111794字节，SHA256 `035655a614be107bd77b2c26147b539e782de1147b889e4b4749d8ff377440e1`。相对旧bootstrap仅第8行的新stage／unit／manifest／嵌入公开bytes变化，其他23行相同，旧载荷保持。
- 新stage为 `/root/easel-av-source-run-20261029-791399b0dc890cf0`，新unit为 `easel-av-src-791399b0dc890cf0.service`。本地AST和base64闭合集核对通过；当前未批准、未上传、未运行。独立复审和安全证据归档另行记录，不能以本段代替。
- v33 ZIP在本轮重新hash仍为129749097字节／`6af10003c9b176ae9fb8ecbf03ba77d0a66e20a2dcf1fed420f121d82b91a195`，17868观察到原PID23888监听；本轮没有重新打包、激活、访问账号或新增模型任务。
- 冻结后独立审阅为限定 Spec PASS／Quality PASS，无P1／P2；报告SHA256 `a00e06a0822f37b9ec4eb86192c75b5cdc2ff7f4f4e1adac5ce666540427d7fc`。独审逐字／AST核对原辅助函数、拒绝条件及调用顺序，读取262项实际日志并复算9份输入，没有运行测试或候选。已对新stage／unit和5文件载荷单独发起一次运行确认；等待明确回复时继续完成本地安全归档，不做远端操作。

## 获准的实际单次运行结果

用户随后明确批准了本份新载荷与指定单位的一次运行，root先只读核主机、资源、新stage／unit缺席、4服务／3容器／3公开输入，再通过strict host key SSH以`python3 -I -B -`仅运行一次冻结bootstrap。SSH终态exit1，无观察超时、自动重试或第二次启动。本次批准已消费。

worker输出受限诊断 `kernel_collect/mountinfo/duplicate`，facts为 `count=24,target=OTHER`。24是首次重复出现的记录序号；不等于24个重复点，也不能由OTHER证明真实路径、ro/rw或合法叠挂。完整validator未过，先前采集到的资源事实不能直接认定合格。work为空，未创建result或operator成功回执；没有GPG／提取／编译结果。unit已collected，MainPID／ControlPID均0，专属cgroup不存在；not-found的默认Result=success不覆盖exit1。

前后4服务身份、3容器含启动时间、3公开输入与5payload pin相同，仅证明指定对象采样保全，不是全业务或账本审计。[实际运行独立增量](../../validation/2026-10-29-av-kernel-diagnostics-linux-run.md)保留授权、终态、安全快照和独立复核。原28文件本地归档保持；执行增量另有14文件闭集，manifest SHA `9fb8cf09e0e587ac073c4a418a674421a083878dd5ed5a2bb40a94e2c31559ad`。

后续本地工作沿用[可见挂载实施计划](2026-10-29-av-visible-mounts.md)；不把当前诊断当作解析兼容已修复，不降低守卫或重用这次一次运行授权。
