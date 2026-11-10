# 当前中央源码全量回归：2468 通过，7 项明确跳过

**修正测试环境后的同一份冻结源码已完成一次完整运行：2,468 passed／0 failed／0 errors／7 skipped，354.817 秒，exit0。** 下文保留原失败与分变量诊断，不拼接多轮计数。此结论是本机完整回归，不是生产部署或真实恢复成功声明。

当前冻结闭包为 1,333 个源码输入及 v29 Pi payload 的 11,932 个库存项，包含本轮 F1／F2／AV 与实际 Pi SDK 测试输入。来源 snapshot SHA 为 `6a90f7d2501e26f19ab8608dc25e903376d619cc8efb1de15c6fd9c1598bb6d2`。这里的全量不是旧 1,895 项回归或若干定向计数相加；原七项真实 Docker／source-handle 恢复测试仍需要明确环境，不能算通过。

## 原始完整运行

`easel-central-current-f1-f2-av-full-awake-final-20261028` 完整执行：**2,460 passed／8 failed／7 skipped／0 errors，368.084 秒，exit1**。时钟观察无跳变事件，最大 wall／monotonic 差为 0.0002351 秒；来源与 payload 前后 hash 相同，结束后专用测试 PG 无其他 active 或 open transaction。该失败结果原样保留，不会因为后续修正而改写为通过。

更早两次启动分别因移除夹具 HOME 覆盖、检测到约 44.915 秒时钟断续而中止，均只属 partial；没有将这些部分计数拼接到完整运行。后续子进程明确省略 HOME，使用独立 XDG／TMPDIR／basetemp、固定专用测试 DSN 和防休眠 wrapper。时间断续原因未确定，没有声称防休眠包装已经修复根因。

## 八个失败的实际共同边界

七项在真实 UID／GID／mode 检查处拒绝：测试进程为 UID501／GID20，但 `/private/tmp` 下新夹具的目录及文件继承 GID0。严格 guard 按设计拒绝，不是账本或恢复数据损坏。第八项期待一个未设私有权限的负例文件被拒绝，但 launcher 的 umask077 使默认文件实际为0600，因而这个测试的前提没有建立。

未改任何产品或测试源码，也未放宽 owner、mode、路径或原123个 guard。仅在全新自建、0700 的临时父目录设置真实运行用户 GID，并对子进程单独设置 umask，完成同八个原节点的分变量验证：

| 运行 | 子进程实际前提 | 原八节点结果 |
|---|---|---|
| A | 新临时父 GID20；保留 umask077，默认文件0600 | 7 passed／1 failed，剩余恰为 nonprivate-file 负例前提 |
| B | 新临时父 GID20；子进程 umask022，默认文件0644 | 8 passed／0 failed |

A／B 无 PG、模型或生产调用，均未设置 HOME；源码 1,333 项前后 hash 一致。普通负例文件仅存在于0700的隔离父目录内，根证据与源码 tar 继续保持私有权限。A／B 是环境因果验证，不能替代修正后的完整回归。

## 修正后完整运行

`easel-central-current-f1-f2-av-full-permissions-final-20261028` 的同一套完整回归已完成，session73200／exit0：2,468 passed、0 failed、0 errors、7 skipped。七项跳过均为明确要求真实 synthetic Docker／owned source handle 的恢复演练。七个实际 Pi SDK 合成协议节点在这一次完整运行中通过，不是旧补测计数。

运行使用已验证的 TMP 父 GID20、子进程 umask022，保留全部资源和时间门。1,333 源码与 11,932 payload 库存前后一致；706 个时间样本无断续事件，最小可用空间 1,305,817,088bytes，未触及700MiB停止门。owned wrapper 已 reap、进程组消失，专用 PG 结束后无其他 active／open transaction，DSN 私有文件未变。

复用同 SHA 的原来源 tar，以绝对路径和 hash 引用；1,333 个成员逐项内容 SHA 已核对 manifest。未复制第四份 tar，也不声称另做了一份备份。HOME 未设置，原生产源码、测试断言、严格权限 guard 与用户配置均未修改。该完整回归不能补齐 F2 的实际 Linux／PG25006／source facts／capture／WPD，旧真实 mixed DR 仍为失败。

本轮只使用原本机专用测试 PG，没有新 Docker、生产迁移、真实恢复、认证、模型或扣点操作。失败与 A／B 小回执见 [持久证据目录](/Users/jiandan/Code/GitCode/Easel/outputs/_run/central-current-f1-f2-av-full-20261028)。
