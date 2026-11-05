# 媒体备份 B1 本地交付与验证

**已完成用户批准的路线 A／B1：本地分块归档、完整文件清单校验和私有目录文件恢复。** 这是中央服务后续接入用的 import-only 模块；没有改生产服务、备份定时器、前端或桌面 v33，没有执行真实数据库恢复。B2／B3 与其他完整交付目标仍未完成。

## 实现与入口

用户在设计预检后明确回复“批准路线 A，先开发 B1（推荐）”，[批准收据](../../outputs/_run/media-backup-b1-20261029/authorization.safe.json)绑定设计 SHA `fd210395d8bf870e67b185d8eeb56997f5d13f97bfe1825ad70e6bd64bc438bb`。该回复不包含生产维护或 AV 第四次运行。

新增 `services/platform/scripts/media_backup/` 五个 Python 文件：

- `contract.py`：严格计划 schema、固定七项 dump／roles／配置逻辑名、registry／scope 关联、缺失与墓碑、规范 manifest 及资源边界。
- `ustar.py`：原始 header 校验、逐块短 IO、固定成员 framing、有界尾部与 EOF；不调用 extractall，不处理扩展或链接。
- `files.py`：受信 FD／私有媒体 dirfd、精确集合、身份／权限／大小／SHA 和前后稳定性，区分借用与自有 FD。
- `closure.py`：`write_archive`／`restore_archive` 编排。恢复只创建全新私有 stage，0600 普通文件／0700 目录，重读落盘字节，保留失败 partial stage，不覆盖或任意 chown。
- `__init__.py`：import-only 包边界，没有新增生产命令入口。

调用方式与格式见[API 文档](../platform/media-backup-b1.md)；[源码 ZIP](../../outputs/_run/media-backup-b1-20261029/Easel-Media-Backup-B1-source.zip)只含本阶段公开源码、测试、文档、原 LICENSE 和批准收据，不包含运行环境、真实配置或数据。确切字节／SHA、包内清单和 root 验证结果记录于[交付收据](../../outputs/_run/media-backup-b1-20261029/delivery.safe.json)，避免文档与 ZIP 相互散列产生循环。

## 实际测试结果

root 最终一次联合运行是 **229 passed in 0.61s，0 failed／0 errors／0 skipped**：五组新增 B1 测试共 219 项，另加既有 mixed DR 的两个纯文件用例组共 10 项。[命令、输入 SHA 和安全回执](../../outputs/_run/media-backup-b1-20261029/final-tests.safe.json)、[原始日志](../../outputs/_run/media-backup-b1-20261029/final-tests.log)及 JUnit 原记录保留。没有把分次 GREEN 计数累加成额外覆盖。

| 本机实际合成实验 | 单文件超过旧限制 | 合计超过旧限制 |
| --- | --- | --- |
| 输入规模 | 33 MiB，1 个 blob | 34 MiB，2 个各 17 MiB blob |
| 完整物理 archive | 34,621,440 字节 | 35,665,920 字节 |
| 恢复 blob 实际字节 | 34,603,008 | 35,651,584 |
| 最大 pread／archive read／write | 均 1,048,576 字节 | 均 1,048,576 字节 |
| Python tracemalloc 峰值 | 3,173,990 字节 | 3,177,807 字节 |

输入使用合成稀疏零文件；归档和恢复走实际磁盘写出、读取及 SHA 核对，未把整个文件／archive 放入 bytes。初始可用磁盘采样约 4.48 GB，两个测试分别要求至少 170,917,888／174,063,616 字节余量。测量限 Python 追踪分配，不包括总 RSS、内核页缓存、最大 metadata 库存或 4 GiB／32 GiB 极限负载，不是生产吞吐／压力证据。

拒绝用例覆盖重复／额外／缺失条目、错误 scope 与 registry、布尔限制、未知 `.part`／孤儿／GC 状态、staged 缺失证明、deleted／purged 实体复活、符号／硬链接、FIFO、权限、大小／SHA、路径替换或写中变化、坏 checksum／header／扩展／padding／尾部／截断、短 IO 无进展、错误 expected_plan、既有目标或不可信恢复 parent。已知缺失仍缺失，不创建占位成品。

## TDD 与独立复审

合同模块先观察 missing-capability RED；逐行 metadata 预算又有独立行为 RED 后修复。USTAR、文件适配器和完整编排分别先记录缺少新能力的失败，再实现。首次文件适配器 GREEN 尝试因并行 contract 尚未就绪而失败，原日志保留，不冒称业务行为 RED；依赖就绪后通过。

独立 Task 3 审查发现一个 P2：保留未读完的 blob generator 时，`Sources` 上下文退出未立即关闭自有 FD，之后仍能读。受限 6 字节合成探针证实；root 新回归确实 RED（`os.fstat` 未报已关闭），然后最小修复 owned FD 跟踪、退出关闭、迭代恢复时 active 检查和防止清理已复用 FD。修复后 files＋closure 42 项通过，独立 R2 确认 P2 闭合。原失败、原 probe、R1 与 R2 均保留。

合同与 codec 分项独审 PASS；文件层修订复审 PASS；完整 B1 最终独立 Scope／Spec／Security／Quality PASS，没有未解决 P1／P2。最终复审 SHA `c98497687aaa916abefa8d27ced8e139a4e0b564b23e9457f7f0d664eaf0e520`，明确保留最初 pins 和仅 files.py／测试的修订差异，未重复执行作者 suite。完整报告及 RED/GREEN 日志收纳于[本阶段证据目录](../../outputs/_run/media-backup-b1-20261029/evidence/README.md)。

## 保全与未完成范围

最终联合测试前后 11 份新源码／测试和 10 份指定旧 DR／F1／F2／AV 源摘要一致。另[重新核对](../../outputs/_run/media-backup-preflight-20261029/source-preservation-final.safe.json) v33 的 904 份来源及 60 个构建输入，差异均空；没有访问 preview 账号或工作区，没有新增真实模型、钱包、支付或远端操作。分支仍为 `codex/ui-redesign`，本轮不提交、推送或合并。

B1 信任捕获器给出的完整计划；它不认证真实 snapshot、missing evidence 或 GC／写入屏障。输出 sink 的加密、durable 发布、认证边界与调度由后续阶段承担；restore 成功只表示本地文件 stage，不表示 PostgreSQL／账号／钱包／财务恢复。B2 仍需完整同窗控制与流式认证加密，B3 仍需实际 fresh restore、源后验和具体 timer 部署。原失败 mixed DR、F2 实际 Linux／PG／WPD、AV 完整来源／编译／供应商、永久工作区接续与 macOS 签名净机等仍按完整总账分别保留。
