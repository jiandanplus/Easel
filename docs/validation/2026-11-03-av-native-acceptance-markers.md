# 原生验收标记与新版本日志案例

2026-11-03，`codex/ui-redesign`。本记录区分启动脚本的源码检查、只读预检和实际失败案例，不将其中任一项当作容量通过。

## 启动接受边界

新 child loader 在原 kill／abort 检查旁增加 failure.json 拒绝。新 parent 将原有完成记录检查提取为 records_guard，在设置 accepted 前调用，并在写出暂存 result.json 后再次读取失败标记、各记录哈希和 done 内容；其后再次检查原时间、boot 和源码／运行环境身份。暂存结果文件不是最终接受依据，仍要求父进程／工具最终退出码为 0。

纯测试只提取实际脚本 AST 的 guard／post-result 分支，使用合成依赖执行。原脚本副本得到 7 项失败、10 项通过；修正后 **17 项通过**。首次 GREEN 中的 AST 遍历顺序断言错误另行保留，改为按源码行号排序后通过。测试不执行完整脚本或父子进程。独立源码审查通过，18 个原有 AV 文件保持字节不变。

候选 loader SHA：`40bf5df256bd6652489f37deebd5fab44b576cee1d356d32b9bb01848358b092`；parent SHA：`0fc44a2c5d0c3db6eb47b681227428efc54835bf498bf253484f5d37e1411dff`。

## 只读预检与物化

Root 核对了当前固定镜像、daemon 关键字段，以及原已接受 foundation 的精确 CID、完整配置／状态哈希和 boot，结果一致。原失败 observer 的精确名称在查询时不存在；这不能反推历史 CREATE 是否发生。

新独立 stage／nonce 使用当前 API／helper 和新接受脚本，Root 认证八个 stage 模块、15 个文件／八个目录及主 GID20，物化后 results 为空、无 once intent。公开配置／profile／descriptor／recipe 与 canonical freeze 哈希一致；独立物化审查通过。新 freeze：`e5d4e7b167916e995b6ce3bd5844980d9b121720a3e01694f8319ce9175d6f2e`。

## 一次新版本实际调用

nonce `9806eef1c3d84bc5b34031e6bea7e586` 只运行一次。父／子进程均 exit70，耗时约0.137秒，没有 stdout 或有效回执。私有目录只留下 create.intent 和 failure.json。经认证的 Records 读取和 schema／绑定核对后，诊断为：

| 事实 | 结果 |
|---|---|
| 控制器阶段 | create |
| API 阶段／操作 | transport／create |
| 观察到的 HTTP 状态 | 400 |
| 受认可 CID | null |
| START 尝试 | false |
| 测试注入标记 | false |
| 容量验收 | 未通过 |

新名称随后只读查询也不存在。没有重复 POST、重跑旧案例或读取容器日志正文。该结果证明这条实际失败路径能留下受限诊断；它没有执行成功回执的完整接受路径，不能证明新 parent 成功边界的原生正例。

本机 Docker Desktop 已知后台日志末尾最多2MiB按本次 nonce／自有 foundation CID 和时间窗口分类，无可归因本次的错误行。十条只匹配自有旧 CID 的记录早约2448秒且未匹配所选错误类别，未将其用来归因。未导出原文。原旧案例的阶段／状态仍未知，新案例也没有取得错误响应正文。

## 确定的请求兼容问题与后续

只读版本查询给出 Engine29.5.3／GitCommit285b471。对应的 Moby `validateBindDaemonRoot` 对 daemon root 内的 bind source 不接受显式 rprivate。当前请求恰好使用该 source 和模式，因此请求存在确定的源码级不兼容项；但未捕获的400响应也可能先由 Desktop 或其他校验拒绝，不能称已拿到精确错误原文。后续独立两文件修正选择显式 rslave，并保持同一只读、非递归、不可自动创建的单文件挂载。实际挂载和容量测量仍需重新冻结新修订验证。[匹配构建的主源码](https://github.com/moby/moby/blob/285b471/daemon/volumes_linux.go)

没有修改工作台、账号、历史、接口配置或钱包，没有拉取／构建镜像、删除／重启资源、调用模型或远程服务器，也没有打包／commit／push／合并。完整音视频、备份恢复和发行门继续保留。

[源码审查](../../outputs/_run/av-native-markers-20261103/INDEPENDENT-REVIEW.safe.md) · [物化审查](../../outputs/_run/av-native-markers-20261103/MATERIALIZATION-REVIEW.safe.md) · [实际结果](../../outputs/_run/av-native-markers-20261103/ROOT-EXACT-LOG-r2-ACTUAL.safe.json) · [失败诊断](../../outputs/_run/av-native-markers-20261103/ROOT-EXACT-LOG-r2-FAILURE.safe.json) · [当前版本](../../outputs/_run/av-native-markers-20261103/ROOT-ENGINE-VERSION.safe.json)。
