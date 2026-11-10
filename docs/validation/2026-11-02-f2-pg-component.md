# PostgreSQL 快照组件验证进展

专用测试镜像构建和真实 PostgreSQL16 组件九场景均已实际通过。该结论限定于测试专用 Unix socket 适配下的合成数据库，不代表生产来源核验或完整恢复通过。

2026-11-02，独立审查发现构建器的取消后重连、JSON 指数溢出以及最终查询期限延长三处问题。修复后 Root 实际运行 31 项纯模拟测试，全部通过；[复审](../../outputs/_run/f2-pg-snapshot-20261102/BUILD-REVIEW-r2.safe.md)确认原问题关闭。执行器 SHA-256 为 `9f845536bebf4a6bebf288a08895d9a53e2b6656c33765bfe82c5c562a0ede52`。

Root 随后执行一次固定本机 Docker socket 构建，工具会话 47776，最终退出 0。构建上下文 266,240 字节，SHA-256 `c0b9ed3f6d430d106381f5a425e61a985e021518adb3340dacde603459467b33`，仅含 Dockerfile、已审查的测试程序及冻结公开源码。使用两份本机已缓存的固定官方镜像；Dockerfile 无 RUN、安装、下载或业务文件。没有启动测试数据库或生产服务，没有切换 Docker 存储后端。

实际镜像为 `easel-f2-pg-snapshot:harness-d64f2bad2889`，准确 Docker ID `sha256:c4316782843012bc65236da633a1b447898605cf8f31f0fe5a8461c32f36d129`，Linux ARM64，大小 170,039,338 字节。实际 manifest 描述为 OCI image manifest、3,789 字节。全部指定配置的规范哈希为 `283e34ff100d6cc451c9243b277910fb7d3a9396d0e3cc8e447ea8206855f4b7`；Root 在构建之后通过新的只读查询再次核对基础镜像和目标镜像的全部指定配置，结果一致。ID 按 Docker 实际返回值绑定，没有猜测 manifest 与 config ID 等价。

构建流仅在内存验证，2,363 字节、42 行，SHA-256 `f6c84ccdf62a2df8ae096bec0f2e906f90344c73ed915af5db7a019477f903e1`；未保存原始流。因此后续只读证据审计可独立复核安全回执、上下文和源码哈希，但不能从归档重新解析原始构建流。安全回执位于 [build-actual-safe](../../outputs/_run/f2-pg-snapshot-20261102/build-actual-safe/manifest.safe.json)，新的[镜像配置复核](../../outputs/_run/f2-pg-snapshot-20261102/fresh-image-profile.safe.json)单独保存。

运行采用[固定单次设计](../../outputs/_run/f2-pg-snapshot-20261102/RUNTIME-ACTOR-TASK.md)。Root 实际运行 26 项纯／模拟测试通过，并完整阅读执行器、测试及[独立代码审查](../../outputs/_run/f2-pg-snapshot-20261102/RUNTIME-REVIEW.safe.md)。早期检查发现的 HTTP 正常关闭导致取消及重复写入专用回执问题均已修复。执行器 SHA-256 `963020d257b63e6589c8d59c9bb2cc42f7b8b2635be1af8cd0ef76e912c82ec6`，清单 SHA-256 `3100e1d2468fd80df35c1ad9a1f4ce16dd1cb9902495b660de873e17b0bde932`。

Root 随后仅执行一次实际运行，工具会话 49818 最终退出 0。专用容器 ID `3271d57eb5f5f531f8ca1d375e51c0ea274826db34baa0dd04032adf8e76db09`，nonce `bd62c36f04ad18a2e8795bc1`。实际启动 `2026-11-02T09:08:46.653613125Z`，结束 `2026-11-02T09:08:47.833581626Z`，约 1.180 秒，exit0、无 OOM、无重启。无网络、UID/GID999、只读根、1CPU／768MiB／swap0／pids128、固定 tmpfs、无匿名卷、无主机绑定或端口发布均由实际配置检查通过。

| 实际场景 | 结果 |
| --- | --- |
| 同一导出快照在独立提交标记后保持完整清单一致 | 通过 |
| 新快照观察到已提交标记 | 通过 |
| ReadPolicy 在执行前拒绝写入 | 通过 |
| 导入的只读事务 INSERT | 实际 SQLSTATE `25006` |
| 导入的只读事务 nextval | 实际 SQLSTATE `25006` |
| 独立序列推进被原稳定性校验拒绝 | 通过 |
| 序列变化被原完整库存一致性校验拒绝 | 通过 |
| keeper 关闭后导入旧快照 | 实际 SQLSTATE `22023` |
| keeper 异常路径清理与回收所属子进程 | 通过 |

原清单收集器在四次实际观察中均取得 3 张表、1 个序列。同一快照 before／after 完全相等；序列推进后仍为同一快照但库存哈希变化；新快照和库存均有不同哈希。PG16、REPEATABLE READ／READ ONLY、全部子进程回收、流关闭和 PostgreSQL 正常关闭均由实际报告通过。

180 秒同启动周期期限、START 前守护、完整创建配置、从未启动证明、唯一创建／启动／日志读取、终态围栏、完整输出、输出哈希、fresh 精确终态及最后发布围栏均通过。Root 随后再次用固定源码的严格解析器校验实际 1,664 字节单帧 stdout，SHA-256 `4b2ea931153eb5a3cefa120d85d42537c3c9c61ed9a487ac2fd488d62bf5119f`。15 份明确允许的安全结果及[清单](../../outputs/_run/f2-pg-snapshot-20261102/runtime-actual-safe/manifest.safe.json)已归档，清单 SHA-256 `3754d998b9e5457db1f76873fc50ff03283f2f92ec6e5b919daac5616d307ebd`。守护 PID75038 随后只读确认不存在，未发送信号。

继承的固定守护器会将该合成容器的完整 inspect 元数据写入私有 0700 目录中的 0600 日志，这是任务书明确列出的例外；不声称所有原始元数据仅在内存。Root 未读或导出这些原始 watch 日志，没有递归复制 stage；新 API 和公开安全归档不包含原始环境或流。未重试、重启或修改旧失败现场。

该组件使用明确的测试专用 Unix socket 适配，不替代生产 Docker/root/source facts、真实捕获、FWPD、媒体 B2/B3、完整 DR、签名公证或净机验收。

本阶段没有修改产品前端、原 17868 的登录、历史和接口配置；订阅与自定义接口继续保留。当前分支 `codex/ui-redesign`，未提交、推送或合并。

随后[独立构建证据审计](../../outputs/_run/f2-pg-snapshot-20261102/BUILD-ACTUAL-REVIEW.safe.md)核对准入、执行器、完整上下文、冻结源码及回执，未发现不一致或阻塞问题。其结论明确不包含原始流重放、独立实时 Docker 查询或 PostgreSQL 用例通过。

[独立实际证据复核](../../outputs/_run/f2-pg-snapshot-20261102/RUNTIME-ACTUAL-REVIEW.safe.md)已完成并经 Root 全文阅读：15 份归档的大小／hash、实际严格报告、原始 180 秒同 boot 期限、创建体 hash、终态与 fresh 全状态、stdout hash 和发布引用均一致，没有发现阻塞项。其独立部分仅为安全证据重放，工具会话退出和守护进程不存在属于 Root 实际观察。

复核后沿用用户对测试资源清理的授权，核对精确 ID、镜像、成功退出和无持久卷后删除本次专用测试容器。容器 43→42，全部 202 个卷及其余容器 ID／镜像／状态一致。未删除镜像或私有 stage，保留完整安全归档和原始私有证据；测试镜像暂用于剩余验证准备。见[清理结果](../../outputs/_run/f2-pg-snapshot-20261102/runtime-cleanup-result.safe.json)。早先“容器保留”的阶段状态已由本次记录更新。
