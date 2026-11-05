# AV 容量观察器验证记录

2026-11-03，分支 `codex/ui-redesign`。本记录仅覆盖容量观察器，不表示原 worker、完整音视频工具链或发行验收完成。

## 源码与纯测试

`capacity_probe.py` 的 R5 修正只接受 `OomKillDisable` 明确存在且严格为 `False` 或 `None`。请求仍固定为 `False`；策略哈希仅将该字段的 `null` 等价表示归一为 `false`，另存完整未修改配置的原始哈希。完整镜像 descriptor 仍进入两个哈希，不删除实际 platform 字段。原有 16 个受保护文件保持字节一致。

Root 新鲜运行隔离测试：**79 passed in 2.57s，exit 0**。R5 冻结包的 14 个文件、两个实际源码／测试文件及 16 个受保护文件的 SHA-256 全部匹配。独立源审通过；审阅者没有执行原生操作。

## 实际运行

| 案例 | 实际结果 | 处理 |
|---|---|---|
| foundation r1 | CREATE 后、START 前因镜像 descriptor 的额外精确 platform 字段拒绝 | 原容器及 once 记录保留，不重放 |
| foundation r2 | START 后因 cgroup v2 将 OomKillDisable 回显为 null 拒绝；最终 exited 137、无 OOM | 原容器、停止声明和失败记录保留，不重放 |
| foundation r3／R5 | 外层父进程 exit 0，子进程 exit 0；1.378601 秒，stderr 0；容器 exited 0、无 OOM、无重启 | 接受本次根文件系统瞬时测量，容器保留 |
| exact-log r1／R5 | 父／子进程 exit 70，run-case 阶段失败；无回执、无已知 CID，仅有 create.intent | 原 once 记录保留，不重新 CREATE |

成功 foundation 使用无网络、无挂载、只读根文件系统、64 MiB 的固定专用容器。其 `statvfs('/')` 可用量为 **434,389,495,808 字节**。这只是该挂载点的瞬时结果，既不是宿主物理可用空间，也不是为后续工作预留了容量。此前宿主可用量约 16.96 GB；不能据此批准要求 25 GB 加 12 GiB 储备的完整构建。

foundation r3 的精确冻结 SHA：`a6828fb0ba83f3e64a6f2f462d21aa8cd6282110a6c85b4866f035893d6ae0e4`。最终父进程退出证明与结果文件分列；不能只凭结果文件中的 outcome 接受运行。

## exact-log 仍未通过

`create.intent` 写在 INFO、IMAGE、借用 foundation 检查和 CREATE 之前，因此只有这个文件不能证明失败发生在 CREATE 之前，也不能证明 CREATE 没有发出。

后续只读诊断确认：精确 observer 名称在查询时不存在；原 foundation 经 CLI 和实际 GuardianAPI 检查，完整配置／状态哈希与原证明相同；两个案例的 boot 身份相同。另一次只读顺序 INFO、IMAGE、foundation inspect／验证全部通过，CREATE 请求仅在内存构造且其字节哈希与原冻结一致，未发送 POST。本地已知 dockerd 日志末尾最多 2 MiB 按本案例精确 nonce／CID 过滤无命中，未导出原始行。

上述证据不能恢复原调用的状态码或失败原因。daemon 拒绝精确日志文件 bind 是候选解释，尚未证实。传输、解析和原时刻的围栏失败仍未排除。独立静态审查未找到确定的 Python／接口级持久阻断点，建议保留不确定结果，先增加受限阶段／状态诊断，再对将来的原生案例另行冻结和授权。不能凭后来的只读成功将原失败改为通过。

**日志文件系统容量门仍然开放**。没有加宽挂载、读取日志内容、重新创建 observer、拉取／构建镜像、删除／重启资源或操作其他容器。实际创建的本任务容器共 3 个，均保留。

## 后续边界

原 worker validator 中同样存在 descriptor 和 false／null 两处已知兼容问题，现已作为独立源码任务处理。该任务只修改自己的四个文件，其他 14 个 AV 文件冻结；容量观察器的历史冻结不被更新。修正源码不意味着可以跳过 exact-log、原 worker／guardian 原生控制、产物保留、真实 pack 解码、完整 FFmpeg 工具链、备份恢复或发行验证。

本批没有改动前端、原 17868 的登录／历史／自定义接口配置，没有调用供应商、支付或钱包，也没有新打包、签名、公证、commit、push 或合并。

[源码审查](../../outputs/_run/av-capacity-20261103/SOURCE-REVIEW-r5.safe.md) · [Root 测试输出](../../outputs/_run/av-capacity-20261103/ROOT-R5-FRESH-VERIFICATION.raw.txt) · [foundation 实际结果](../../outputs/_run/av-capacity-20261103/ROOT-FOUNDATION-ACTUAL-r3.safe.json) · [父进程退出记录](../../outputs/_run/av-capacity-20261103/ROOT-FOUNDATION-PARENT-TERMINAL-r3.safe.json) · [exact-log 实际结果](../../outputs/_run/av-capacity-20261103/ROOT-EXACT-LOG-ACTUAL-r1.safe.json) · [失败独审](../../outputs/_run/av-capacity-20261103/EXACT-LOG-FAILURE-REVIEW-r1.safe.md)。
