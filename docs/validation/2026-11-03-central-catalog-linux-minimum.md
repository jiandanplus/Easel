# 最新中央候选：Linux 最小运行权限整链验证

2026-11-03，分支 `codex/ui-redesign`。本报告覆盖当前中央服务候选的 `full020/whole` 合成隔离验收；生产、完整数据库恢复及其他发行门分别记账。

## 产物和执行身份

- 中央 wheel：`easel_central_platform-0.1.0-py3-none-any.whl`，496,103 字节，SHA256 `a6d5553286c69b3a62f8ae6d7f67f8a43b615b0cd2c0ef67bce13c45cb59e268`。
- 公开脚本 19 份，另 3 份冻结输入，共 22 份物化文件；原 18 份脚本中只有 driver、app 和 host 发生限定修改，新增 `minimum_role.py`。其余 15 份脚本保持字节相同。
- 源清单 SHA256 `69de386edfe25f1a3950b955049b230f20e15301709aa30fce8d9e68fbd6336f`；代码审查 SHA256 `338d1e0e8c6361aa67929b5c1fca4f27ca09179d10902e915bf43841da0639c0`。
- 冻结载荷 SHA256 `7360f34524db67110c5029e9b561004577803b8979d8cf834bd1674d3d0f9e44`，1,796 个根文件系统条目；60,631,040 字节 USTAR 的 SHA256 `fb4dcc901189d23efd495dc521bd059a12e5670e66a6e3a43810e5b8482dd9c4`。
- 实际离线镜像 `sha256:26793bffaac09d095de3a1a14e808a8e840e72b7682f97076e57475ce167e655`。明确选择缓存的 Linux/amd64 基础镜像 `sha256:f9d1e581cde3e4c8bc696b07e6f7b7aea1d41014b33a52b27990762daae61beb`，未依赖默认 ARM64 解析或拉取新镜像。
- 构建容器 `512a198ad06ec84b9e5665a87cfba26c689e56862c14b6d76316506d13da2f53`；运行容器 `b36da3bb95bdf8650ddb297bc44cfdeec585768c139a361c1fde72a2a36e29fe`。

## 实际结果

| 范围 | 核对结果 |
|---|---|
| 纯测试 | 本轮重新运行 9 项，0.119 秒，退出码 0 |
| 离线安装 | 中央候选与 33 份固定依赖的全部 34 个 wheel 哈希匹配；安装、导入 smoke、公开目录模式检查通过 |
| 构建权限与期限 | 启动前固定三个公开目录的首个归档头均为 root:root/0755；实际 cgroup 为 1 CPU、1536 MiB、swap 0、PID 128、CapEff 0、NNP 1；自然退出 0，无 OOM；原 360 秒同 boot 围栏有效 |
| 最小数据库角色 | 原宽授权首先被产品安全检查拒绝，再由产品正式 grant helper 收敛、提交并复核；实际 app 连接再次验证，Root/app 角色哈希一致 |
| 业务进程边界 | 实际 UID/GID 10001、空附加组、CapEff 0、NNP 1；5 项 OS 拒绝检查通过；冻结 app 中 5 项 control 操作和 2 项 catalog 操作均要求精确 SQLSTATE 42501 |
| 所有者维护屏障 | 两条原 SQL 由 Root 使用实际 owner 执行，均精确拒绝为 23514 和 `media maintenance permit required`；公共 COPY 数据、全部 7 个 control 表及公共/control 序列库存前后不变 |
| 媒体和合成资金 | 6 次合成生成、12 个唯一媒体 blob，共 37,774,332 字节，实际分配满足 dense 条件；原合成钱包 spent 增加 12、reserved 前后均为 0 |
| 备份和文件恢复 | 实际 `backup_committed`，窗口恢复 OPEN；独立新进程完成认证读取、自验及私有目录文件恢复，恢复归档物理大小 41,861,120 字节，12 个 blob；维护转换序列和 app/worker 关闭断言通过 |
| 生命周期和资源 | 整个运行容器自然运行 142.851040 秒、退出 0、无 OOM/重启；原 1800 秒期限、同 boot、终态围栏、写后摘要、fresh 完整 HostConfig/Config/Mounts 比较通过；22 份物化源码再次逐哈希一致 |

容器时间包含 PG 初始化、迁移、种子、生成、备份、恢复及关闭，不能解释为单个接口延迟。Linux/amd64 运行于 Apple Silicon 上的模拟环境，不能作为原生 Linux 性能结论。外部 HTTP 使用 MockTransport，没有供应商、付款或真实钱包调用。

## 证据边界与保留记录

Root 在内存中读取五个固定合成验收记录，核对实际 Root/app 角色、app 事件、资金、媒体和 whole 数据；只导出三份严格安全角色/屏障回执以及计数、布尔值和哈希。没有导出 DSN、合成密钥、原始业务行或子进程私有日志。公开审计能够检查来源、期限、身份和回执一致性，不能凭这些摘要重新构造私有 SQL 行。

最初新目录继承 GID 0，在任何文件复制和容器创建前被拒绝。空目录和失败记录保留；后续使用全新目录并明确设置当前用户所属 GID 20。沙箱中的一次只读 Docker 检查被本地 socket 权限拒绝，随后在授权执行环境核对同一固定缓存镜像。一次 Root 进度筛选遗漏已知的 `initialized` 事件，原输出保留；这是观察脚本的枚举遗漏，未重启容器或更改测试。原准备 R1、旧失败现场和旧 200ms 失败记录均未改写。

本次新的安全归档包含 29 个固定文件，manifest SHA256 `b12bbf10350038690d4b25c812608376dbc00b0a5fe4c382c224e6197324d037`。独立实际安全材料复核结论为 **BOUNDED PASS，无本范围阻断项**，报告 SHA256 `8bfb5b4e3b85d2a88dc1037f5a5387e98e86b5529418098d0c64307ee3b66723`。复核逐项确认归档完整性、22 份公开作者文件、34 个安装包的名称/版本/哈希、角色摘要、原期限及写后/fresh 引用；另静态核对 wheel 的 140 个 ZIP 成员、139 项 RECORD 和 137 项来源映射。审计没有重建私有 SQL 或实际网络日志。

独审完成后按已有清理授权，定向删除本轮 2 个成功测试容器及 1 个专用镜像。容器库存 38→36，全部 210 个数据卷保留，其他容器 ID/image/state 和其他镜像 ID 集合不变；没有 force、prune、删卷或远程动作。公开包、私有阶段目录、合成备份及全部失败证据保留。以上镜像 ID 是历史验收身份，清理后不代表镜像仍在本地安装。

`database_restore_performed=false`：这里完成的是备份归档与文件恢复，没有把数据库导入新 PG、恢复数据库角色/凭据/ACL/序列后启动应用。`production_compatible=false`、`t13_complete=false`、`full_release_complete=false` 保持。B3 新设计、完整 T13/T14、AV19、生产备份恢复/DR、签名公证、净机和其他交付门仍开放。

没有修改前端或原 17868 登录、历史、自定义配置，没有部署生产、修改定时器、commit、push 或合并主分支。

[执行计划](../superpowers/plans/2026-11-03-central-catalog-linux-minimum.md)；[实际安全归档](../../outputs/_run/central-catalog-linux-candidate-20261103/r2-actual-safe/manifest.safe.json)；[源审](../../outputs/_run/central-catalog-linux-candidate-20261103/r2-pure/REVIEW.safe.md)；[实际独审](../../outputs/_run/central-catalog-linux-candidate-20261103/r2-actual-safe/ACTUAL-REVIEW.safe.md)；[清理结果](../../outputs/_run/central-catalog-linux-candidate-20261103/r2-cleanup-result.safe.json)；[候选包报告](2026-11-03-central-catalog-candidate.md)。
