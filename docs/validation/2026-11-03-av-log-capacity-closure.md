# 精确日志文件容量验证闭环

2026-11-03，`codex/ui-redesign`。**已通过本次单独的原生容量验收门**，不代表原 worker／guardian 控制、真实源码工具链或正式发行完成。

## 实际通过结果

新修订案例 nonce 为 `752a36f283af4e2b9bbf87cf1dca405b`，freeze 为 `eeddf715dd34bd44f90f577da3caeae309cbdfd9575881a94ccb1a4bcd5e73eb`。只运行一次；Root 工具会话52545与 child 最终均 exit0，stderr为0，周边调用约1.429秒。容器正常退出、无OOM；没有失败／停止／中止标记，完整私有回执和记录哈希通过。

| 测量对象 | 可用字节 | 精确 fsid |
|---|---:|---:|
| 探测容器根文件系统 | 434389401600 | 4827309409299244920 |
| 只读绑定的精确自有日志文件 | 434389401600 | 5658131204301507303 |

两项均超过固定的 `2415919104` 字节小型验证门槛。**可用字节相等不表示文件系统相同**；两个 fsid 实际不同。这次通过的是精确文件绑定后的实际测量，不再依赖父目录相同的推断。

程序只读取 `lstat/statvfs` 数字元数据，要求同一个普通单链接文件的前后身份一致，不打开或读取日志内容。所观察文件为286字节；新的 observer CID 为 `3b83d668e3f7a8efeeae7e032c129b2debfd8802e5e449b16635876ec398b23e`。原基础容器状态／配置／boot 经执行前后检查保持一致。基础容器与新 observer 均保留。

这是瞬时虚拟文件系统可用量，不是宿主物理空闲空间或容量预留。宿主近期只读样本约15.88GB，不能据此批准25GB加12GiB储备的完整构建。该门仅解除后续受限验证的容量阻塞。

## 变更和回归

当前 Engine29.5.3 的 GitCommit285b471 对应的 `validateBindDaemonRoot` 不接受 daemon root 内显式 rprivate。固定 exact-log 请求和检查已严格改成 rslave；同一精确文件、只读、非递归、禁止自动创建及全部资源限制保持不变。[匹配构建的主源码](https://github.com/moby/moby/blob/285b471/daemon/volumes_linux.go)

生产代码只有上述两个字面量变化。Root 新鲜容量回归 **104 passed in 6.85s，exit0**；其余16个 AV 文件哈希不变。基础容器请求字节前后完全一致。独立源码、物化审查均通过。

原来的新版本案例只得到 CREATE／transport／HTTP400／无受认可CID，其失败记录继续保留；更早的 unknown 案例状态也未改写。匹配源码证明原请求有具体不兼容字段，修正后的新案例已实际通过，但仍未捕获旧400的响应正文，不能伪称已取得旧错误原文。

## 结果复核

Root 额外核对了私有 source／operator 身份、目录身份、五份回执记录和 done 精确内容；创建／终止策略哈希相同、原始配置哈希不同，符合既有 false／null 表示规则。

独立实际结果审查 **PASS，31项公开一致性检查通过**：结果／回执／stdout／可重建记录哈希、source／case绑定、数值和原期限均一致。回执2250字节（含LF），样本距离原截止时间还剩28.644497917秒。私有文件检查和实际父进程状态明确归因于 Root，复核者没有重复执行。审查SHA：`7c35807fbbdce235dc932964592ac7da8e51a07f5e4a2546c4e32360c3ae0e50`。

## 下一步

容量门通过后，按原计划依次冻结原 worker 的 preflight、内存／CPU／墙钟原生控制、近16MiB产物保留及真实源码 pack 案例。历史旧 preflight stage 仍绑定旧源码，保持未执行；必须用当前 operator628／profilee045／API501重新准备，不能升级旧记录当作通过。完整FFmpeg工具链、备份恢复／DR和发行验收仍需各自完成。

本批没有改动前端、工作台账号／历史／接口配置或钱包，没有调用供应商、生产服务或远程服务器，也没有打包、commit、push或合并。

[原生结果](../../outputs/_run/av-bind-propagation-20261103/ROOT-RSLAVE-ACTUAL.safe.json) · [父进程最终退出记录](../../outputs/_run/av-bind-propagation-20261103/ROOT-RSLAVE-TERMINAL.safe.json) · [私有记录核对摘要](../../outputs/_run/av-bind-propagation-20261103/ROOT-RSLAVE-RECORD-CHECK.safe.json) · [实际结果独审](../../outputs/_run/av-bind-propagation-20261103/ACTUAL-REVIEW.safe.md) · [源码审查](../../outputs/_run/av-bind-propagation-20261103/INDEPENDENT-REVIEW.safe.md)。
