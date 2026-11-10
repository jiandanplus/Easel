# 三种文件写入场景的最小权限实际验证

2026-11-02，分支 `codex/ui-redesign`。本次不改产品或前端，只升级三个已计入历史账本的原始文件写入用例的权限覆盖。

incomplete、fresh、promote 均在同一冻结 full020 镜像中分别首次运行，三个独立合成数据库／运行角色／私有卷。三次自然退出 0，无 OOM、重启或外网。原始测试／helper／SQL／33 个依赖 wheel 和产品 wheel 保持固定；不是用新测试替换原断言。

Root 先 seed，再执行真实 broad-role reject → grant → commit → assert；app 的真实连接池执行默认角色断言后才启动 generation，实际 UID/GID10001、无附加组、cap0、NNP 及 worker 身份均由 Root 核对。原始 worker、物理写入、未完成部分文件和最终文件断言均实际运行。Root READ ONLY oracle 检查同一 job/attempt/permit、epoch/audit、完整资金保留／关联／预算及文件事实；未知费用保留，不结算、不释放。

公开安全归档 47 文件的 manifest 为 `4d9e667855152690784828d225e3b1d5b6fd46959db4557fffd78c0e2c333770`，独立审计通过。代码 closure 为 `a59522f96df71c902ebd1aab193a3b2906fc48926ac2a24f1928094705b0e388`；源 overlay 32 项纯测试和三轮审查通过。构建使用原360秒、运行使用原1800秒同boot监护；Root 另外检查精确 tmpfs/caps/mount/profile、固定回执、fresh 终态和最终发布围栏。

证据边界：公开归档不含私有数据库行、文件正文、app 角色原始帧或私有 checkpoint。独立审计重放的是安全材料的 schema/hash/身份/配置一致性；私有 oracle 和 done/post-app equality 由已审查 Root 执行链证明。最终发布前第二次围栏的调用时序由 Root 操作记录证明，公开 fence 不含该时刻的额外 monotonic 样本。不能将本报告描述为审计者重建了私有 SQL。

历史节点数仍为 **17/37**；其中最小角色覆盖由 **14 minimum + 3 broad** 升级为 **17 minimum + 0 broad**。两条新命名取消路径、原200ms失败、AV19、完整 B2/B3/T13/T14、备份恢复／DR 及正式发行门均不因此关闭。

成功资源已按原清理授权定向删除：4个容器、1个专用镜像。新鲜库存由33容器变29容器；205卷全部保留，其他容器ID/image/state前后一致，无force/prune或远程操作。先前库存是历史快照，不据此推算本次之外的资源变化。

[实际独立审计](../../outputs/_run/b2-physical-minimum-preparation-20261102/ACTUAL-REVIEW.safe.md)；[安全归档](../../outputs/_run/b2-physical-minimum-preparation-20261102/actual-safe/manifest.safe.json)；[清理结果](../../outputs/_run/b2-physical-minimum-preparation-20261102/cleanup-result.safe.json)。
