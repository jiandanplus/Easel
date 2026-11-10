# 当前源码的实际捕获、加密发布与私有文件恢复

2026-11-03，`codex/ui-redesign`。承接备份目录异常修复，对修复后的当前源码完成本机真实 PostgreSQL／工具链回归。没有使用真实账号、供应商接口或真实点数。

## 实际运行结果

| 批次 | 原用例 | 实际结果 |
|---|---|---|
| once001 | attested_capture | 1 failed，1.49 秒；在捕获前 ConfigInputs 拒绝 GID0，与要求 GID20 不符 |
| once002 | attested_capture | 1 passed，4.47 秒；全新 GID20 私有目录，产品所有权检查未修改 |
| once003 | facts、logto | 2 passed，7.84 秒；实际归档事实交叉校验及官方 Logto 合成库捕获 |
| whole001 | 原完整 integrated 文件的六个参数组合 | 6 passed，50.03 秒；运行前后 136 份当前公开 Python／SQL 文件哈希全部相同 |

六个原组合分别为：001–016＋空媒体＋public 身份库；001–020＋空媒体＋public；001–016＋非空媒体＋public；001–020＋非空媒体＋public；001–016＋空媒体＋Logto；001–016＋非空媒体＋Logto。不是对全部版本／身份库的笛卡尔组合覆盖。

首个失败的原目录、日志和安全帧均保留，没有改写为通过。只修正新测试父目录的实际 GID，并设置本进程 TMPDIR；后续均使用新目录和新随机数据库／角色。所有运行使用原用例和断言，首失败停止，外层故障上界 180 秒。没有重启或重放旧句柄。

## 本次实际证明的内容

原 `attested_capture` 验证真实 worker 登记／证明、导出快照、维护历史、完整来源事实、dump schema，以及捕获后校验；删除 worker registry 的篡改反例仍拒绝。`facts` 从实际捕获的配置包重建交叉关联，并拒绝改写的 dump 摘要。`logto` 验证固定官方合成库的目录、八类枚举和捕获后稳定性。当前 public 与 Logto 两条有效身份目录路径均已执行成功。

六个 integrated 用例使用原 `build_backup`，实际完成捕获、流式认证加密、自验和提交发布，要求维护状态回到 OPEN、无剩余许可、准确的末五步审计以及工具子进程／远端引用已收束。随后启动新的 Python 进程，从可信 anchor 与 commit 读取已提交归档，重新完成认证／文件内容验证，并恢复到新的私有目录；核对历史 CAPTURING 状态、来源投影库存、固定七文件、媒体数及非空媒体 SHA。两个数据库的快照始终明确 `cross_database_atomic=false`。

非空媒体通过真实服务、维护许可及合成点数账本生成，但 HTTP 是明确的 MockTransport 合成图片响应。这不是供应商或真实扣点验收。

## 环境与运行后核对

本机 macOS 源码测试环境 `/private/tmp/easel-b2-legacy-runtime-20261030/bin/python`，Python3.12.7；它通过既有 `.pth`／本进程 PYTHONPATH 读取当前源码，不是安装 wheel 的独立验收。

原合成 PostgreSQL16 容器精确 ID 为 `7bed3d6afc12db82f89cadf062ab5d07315b1ff793962234bd6ea4d342f32f0d`，镜像 `sha256:9e2cf6287f25c3151f4c9421ccce2b17f3e60061c111a29165e0d2cdd9ff0a97`，仍仅发布 `127.0.0.1:63623`。前后只读检查均为 running／noOOM／restart0。dump／restore 使用原固定完整容器 ID 的 Docker exec 工具链。

测试后用只读事务核对原合成基库：PG major16、public 基表／分区表／序列计数0、`easel_b2_owned_tests` 活动连接计数0。测试辅助函数的精确 finally 清理与成功退出支持本用例随机数据库／角色已收束；没有做跨历史库存的全面清理证明。容器保持运行供原合成环境使用，没有新建、删除或重启容器。

安全记录位于 `outputs/_run/backup-catalog-capture-20261103/`；私有日志及合成归档保留在各独立 0700 临时目录，日志权限0600。账号、DSN、密码、合成私钥及备份内容没有导出到安全报告。独立安全材料一致性审查另见 [审查报告](2026-11-03-backup-capture-actual-review.md)。

## 仍未证明的交付范围

这里的恢复是新进程的**私有文件恢复**，没有把恢复出的 dump 导入新数据库，也没有启动恢复后的应用。因此不等于完整数据库恢复或生产 DR。仍未关闭 Linux/root 固定来源 factory、生产维护／备份定时器、完整 B2/B3/T13/T14、音视频全覆盖、正式签名／公证或净机发行门。

本次未改前端、原17868工作台、登录、历史或接口配置；未修改生产服务或生产资金；未打包、激活、提交、推送或合并。历史冻结代码的证据与本轮当前源码结果分别保留。
