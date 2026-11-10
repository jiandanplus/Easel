# S5-A4：真实 Logto 私有传输、隔离恢复与权限验证

日期：2026-10-24（Asia/Shanghai）。继续在 `codex/ui-redesign` 工作，不自动 commit、push、merge 或切换主分支。本记录承接 [S5-A3 真实预检](2026-10-24-central-production-preflight.md)与[实际切换清单](../../services/platform/deploy/CUTOVER_20261024.md)。本轮已经把真实 Logto 私有归档通过 SSH 传到批准服务器，并在全新持久 PostgreSQL 目标实际恢复；当前权限元数据、有效表 ACL 和两个租户 PostgreSQL 登录分别通过验证。**这仍是非最终恢复演练，没有启动目标 Logto，也没有执行公网切换或身份映射。**

本文根据各阶段安全回执整理。完整数据、用户／应用集合摘要已一致，新 wheel 的独立 Linux 安装已通过；**完整 schema 指纹仍未通过**，唯一剩余差异为两个邮件日志 partial index 的数组 cast 表示。未用文本替换或省略字段把它改为通过，因此 T2 结构验收仍未完成。

## 授权与私有数据范围

真实身份归档包含用户认证资料、应用机密和 Logto 租户数据库凭据等敏感载荷。用户明确确认了这类真实账号归档的传输范围、唯一目的地 `115.190.211.112`，以及维护者／服务器 root 的私有目录边界，因而本轮真实传输的授权门槛已解除。历史回执中原有的 blocked、not_run 或“尚未传输／恢复”保持原样，继续代表原时点状态；本页追加后续已经实际执行的事实。

归档在 Mac 维护用户的 `0700` 目录中以 `0600`、单链接普通文件保存，经严格 SSH host-key 验证的加密连接送到目标主机 root 私有目录，目标同样使用 `0700`／`0600`。接收端核对四文件的尺寸、SHA256、manifest、源容器身份和完整 transfer intent。正文、私有 DSN、用户行、应用 secret、租户密码及完整私有 metadata 不进入仓库或公开报告；本页仅记录计数、阶段、摘要和受控对象标识。

没有读取或导出 New API／模型供应商 Key，没有模型或付款请求，没有改原业务库账号、钱包、额度、会员、订单或 pending。本轮没有把源 Logto 停写作为已完成事实，现有自定义接口与中央订阅边界继续保留。

## 已完成的独立证据

| 阶段 | 本次实际结果 | 证据范围 |
| --- | --- | --- |
| 私有归档链复核 | 四文件尺寸／SHA、UID、`0600`、单链接和父目录 `0700` 通过；manifest 与源 inventory、镜像、容器 ID、角色依赖证据一致 | 没有通过查看 dump 正文核对用户内容 |
| SSH 传输 | operation `185581b3ef22427cb24b94e423b95211`，`complete`、`outcome_unknown=false`、四文件 intent 匹配 | 只到批准服务器私有目录，没有公网文件下载入口 |
| 全新持久隔离恢复 | operation `91cab4f664ca4efeaadb2d06061c28f6`，`complete`、`outcome_unknown=false`，PG 16.15；摘要计数通过 | 恢复的是 `non_final_rehearsal`，没有启动 Logto、API 或 Console |
| 当前权限元数据与有效 ACL | operation `7de6f24d75484e3d877184e0290c1148`；每端同一只读 repeatable-read 事务采完整既有权限 metadata 与 canonical ACL；79 表／857 grant 项一致，总采集窗口约 1.66 秒 | 当前权限 metadata 通过；完整数据由独立 collector 另核对，schema 仍有两个索引门槛 |
| 租户 PostgreSQL 认证 | 服务器内两个实际租户角色均完成 LOGIN、`SELECT 1`、实际 session/current role 和限制属性检查 | 不是 Logto 本人登录，也没有完成跨 tenant 数据拒绝或更名数据库的 runtime 验收 |
| 完整数据／身份集合 | cycle `5f146e98762fd2d3e52cb12f0d04dc04`；全表与 sequence data SHA、default tenant 用户／应用集合 SHA 及数量、必要两应用检查均一致 | 两库分别只读采集，非跨库冻结；schema SHA 不等，未签发最终 proof |
| 新 Linux 校验环境 | 新 wheel、同一 collector SHA、原锁依赖；26 distribution／56 payload 文件核对，API／Console 禁止 socket 的独立构造通过 | 独立新目录，未覆盖原 venv，未启动正式服务 |

传输与恢复分别使用独立阶段 permit，绑定冻结 helper、源 inventory、容器身份、指定 host／hostname；恢复另绑定实际 archive／manifest SHA、完整 transfer intent 和 transfer operation。执行前落盘并 fsync 私有 intent／phase journal，不自动重试，不覆盖未知或部分状态，也不把客户端结束当成远端操作已终止。上述已完成回执均明确 `outcome_unknown=false`。

真实数据归档为 399,811 字节，SHA256：

```text
40d20ed3f7181e58272058e23d566a2350ed341df60d1313cbdcfbc8d9a588fc
```

Manifest 为 1,895 字节，SHA256 `6ff020ca6fcb4f4fbd9918375b520564892c97974d002b8510098835d4c82dde`。四文件 transfer intent SHA256 为 `8c197cf502c5e403026fa73c0986a4002698a2501df922ba11bd47a76f4c123e`。这些摘要只用于定位本轮受审私有对象，不使归档成为公开交付物。

## 隔离恢复目标与保留范围

目标为同一批准服务器上的 `easel-idp-production-20261024-pg`，独立持久卷与 internal bridge 网络；验证期间仅运行 PostgreSQL，没有目标 Logto 容器。固定 PostgreSQL 镜像与源一致，实际版本 `160015`，容器限制为 512 MiB、CPU 1、PIDs 128、`restart=no`。运行状态、固定镜像／标签、唯一 RW 数据卷、指定 internal 网络及成员关系均另行回读验证，没有新增数据库公网 bind。验证后于 12:05:46（Asia/Shanghai）精确停止这一个新恢复 PG 以释放内存，保留容器、持久卷、internal 网络、私有归档与目标配置；原服务未停止。

恢复目标数据库为 `easel_logto_rehearsal_20261024`、schema `public`。它保留源对象 owner／ACL，恢复时未使用 `--no-owner`、`--no-acl` 或 `--clean`，没有把整个源 cluster 的角色脚本盲目重放。源 `central_runtime` 在 Logto owner／ACL、default privileges、membership、当前库／shared 角色依赖及受控定义扫描中均无引用，因而在 permit 中明确作为无关角色排除；这是 Logto 作用域的角色恢复，不是全 cluster 逐角色复制。

源及恢复摘要为全部 tenant 合计 6 用户／8 应用，`default` tenant 5 用户／4 应用；8 enum、153 RLS policy、85 非内部 trigger。两个既有产品／Console 应用仍纳入必需应用核对。摘要计数一致不是完整数据一致性的替代。

源中同名 `preview_owner` 是高权 owner。目标新建的同名 owner 仅供一次性恢复／维护采集，没有交给 Logto、API 或 Console 作为常驻身份。两个 tenant PG 角色及其成员关系保留；对应认证密码由恢复后的私有 tenant 字段在 PG 内部设置，不输出值、不进入 argv。后续服务器内认证只在内存中读取所需凭据，检查两个不同且完整的期望角色后逐个连接。

## 原始 ACL 差异与后续只读补证

最初的原始权限 verifier operation `678a4038e3a449d4a014c0343eda5154` 返回 `objects=false`，其他检查为 true；该失败记录、原始 source／target baseline、恢复 helper 和 manifest 均保留，没有追改为成功。

实际差异只在 `service_logs`、`systems`、`tenants` 三张表：源记录显式 owner-all ACL，目标 catalog 为 NULL/default ACL。278 个对象没有缺失／新增，owner、名称和 kind 一致，其余 275 个对象原始记录相同。

V3.1 独立只读查询对两端全部 79 张普通表执行 `aclexplode(COALESCE(relacl, acldefault('r', relowner)))`，按角色名称、grantor、grantee、privilege 和 grantable 位规范化后，857 项有效 grant 完全一致。它绑定原 V2 failure 的 operation、result SHA、目标 baseline 路径与 SHA；每端在同一个只读 repeatable-read 事务里重采完整既有权限 metadata 与有效 ACL，再核对各自原 baseline 和两端当前结果。仅排除会随并行只读维护连接改变的 `summary.active_other_sessions`；其他 owner／ACL、column grants、default privileges、角色、成员关系、extension、数据库 locale 与必要应用检查都保留。

本次实际采集为 UTC 03:55:44–03:55:46，总窗口 1.658 秒，小于 120 秒上限；结果明确 `current_full_permission_metadata_reverified=true`、`database_modified=false`。`current_full_schema_reverified` 与 `full_data_proof` 仍为 false。这份补证没有 GRANT、修改原始 ACL 或放松权限字段来匹配目标。

## 内部端点与实际 PostgreSQL 登录

Docker `HostConfig` 中的 loopback publish 元数据不能替代实际可达性。root 观察到该 internal 网络下宿主 `127.0.0.1:19876` 连接不通，而精确绑定容器的内部端点可达；没有为此扩大网络暴露或更改原私有配置文件。

服务器内验证先校验原私有配置仍是批准的 loopback／数据库／owner 合同，再通过实际容器 ID、镜像、运行状态、资源限制、唯一卷、internal 网络、endpoint 与 subnet 守卫，生成仅在内存中使用的内部 IP:5432 连接参数。密码、DSN 和内部端点不进入安全输出。

两个实际 PG tenant 角色均通过 LOGIN 与 `SELECT 1`；会话身份与期望角色相符，无 superuser／createdb／createrole／replication／BYPASSRLS 属性，没有 owner 成员权限，schema／database CREATE 权限检查为 false，事务保持只读。回执明确 `credentials_exported=false`、`data_rows_exported=false`、`private_config_modified=false`。没有通过尝试执行 DDL 来验证拒绝。这只证明本轮 PostgreSQL 认证和列出的限制，不能推出托管登录、跨 tenant 行可见性或 Logto runtime 已验收。

## 代码与验证记录

原 collector 对 ACL catalog 原文进行 fingerprint，在真实 dump／restore 后暴露出 NULL/default 与显式同等授权的表示差异。本轮修复在保留 owner、grantor、grantee、privilege 和 grantable 的前提下，对正确对象类型的默认 ACL 展开并排序；没有更改真实数据库授权，也没有跳过 RLS、enum、sequence 或数据比较。PUBLIC 与同名真实角色区分保留，显式空授权继续与默认授权不同。

| 检查 | 本轮实际结果 |
| --- | --- |
| 真实备份／传输／隔离恢复工具纯测试及独立复审 | 18／18，通过后按阶段签发 permit |
| V3.1 与内部端点 tenant 验证器独立纯测 | 16＋6，通过；两项新增审查意见关闭 |
| 身份 collector PostgreSQL 定向回归 | **74 passed in 10.00s** |
| 中央服务完整 PostgreSQL 回归 | **900 passed in 90.81s** |

上述测试有子集重叠，不能相加成新的总数。首次完整回归使用的本批专用合成 PG tmpfs 为 256 MiB，运行时遇到 DiskFull；root 核对其 data／WAL 占用后，在全新的本批专用实例使用 1 GiB tmpfs，完整重跑得到上述 900 passed。原失败和容量诊断保留；真实 Logto、真实中央账本没有被用作可写回归测试库，也没有为测试扩容或清空这些真实数据库。

旧的 [S5-A3 安装／交付证据](2026-10-24-central-production-preflight.md)仍代表其当时 wheel 和 collector，不能自动替代本次 ACL 修复后的重新打包与全新 Linux 安装。

## 完整 inventory、独立安装与剩余门槛

新 wheel SHA256 为 `9314071632c4f8f9f370f15ebcb6f7dc69c7b2218562a54d5a0c94cff5e985f5`；collector 为 `11d2e78c2025c439121e1bfb548dee17f69fa238a757a3e710356f2b60cfabcf`。`uv.lock` 与原 requirements 锁保持原 SHA，未升级依赖。服务器在全新 `/opt/easel-central-preflight/20261024-3acde2188e295d033cbf1e1bda357417` 目录安装了 26 个 distribution，56 个源码／migration／前端 payload 文件逐项等于当前本地内容，API／Console 构造时 socket 被禁止且构造通过。包含 10 个 migration 文件不代表已对真实中央库执行 migration 010。旧 wheel、旧 venv、旧 ZIP 与旧回执未覆盖。

两端使用新 collector 和新 cycle 重新采集，没有混用旧 ACL 指纹。实际 UTC 03:59:32–03:59:33（即本地 **11:59:32–11:59:33**）完整比较结果为：全表与 sequence 的 data SHA 一致、default tenant 用户／应用完整集合及数量一致、两项必需应用存在、数据库 context 确实不同；**schema SHA 不一致**。这里只是每库自身的 RR READ ONLY 快照，未证明跨库 writer fence。

后续只读诊断把差异定位到 `indexes` 组件；其余 relation／columns／enum／function／trigger／policy／sequence definition／locale 等结构组件指纹均一致。181 个索引的顺序一致，其中仅以下两个定义文本不同，valid／ready 均为 true：

- `service_logs__email__tenant_id__created_at__id`
- `service_logs__email__tenant_id__recipient__created_at__id`

两个 predicate 的原始常量集合摘要分别一致。脱敏形状显示源为 `ANY ((ARRAY[...::varchar, ...::varchar])::text[])`，恢复为 `ANY (ARRAY[(...::varchar)::text, (...::varchar)::text])`。[PostgreSQL 的反编译函数](https://www.postgresql.org/docs/16/functions-info.html)和[PG16 ARRAY cast 解析源码](https://raw.githubusercontent.com/postgres/postgres/REL_16_STABLE/src/backend/parser/parse_expr.c)能解释这种 dump／reparse 形状变化，但现有证据未逐项证明本次实际节点的 operator、cast、typmod、collation 与数组结构满足等价条件。

因此保持原比较的 `success=false`／`schema_sha256=false`，不使用删除 cast、正则改 SQL 或数据相等来替代结构证明。后续需另加受限、只读的 PG16 predicate 结构补证及真实 dump／restore 正反向回归，未知节点、用户定义 cast、domain、不同 collation／operator 等应失败；不执行 predicate、EXPLAIN 或 planner 来寻求通过。**本轮尚未实现该补证，也未签发最终身份 proof。**

12:06:06（Asia/Shanghai）重新检查三个公网域名，HTTP 仍返回 Suzaku 302 至 `webblock.volcengine.com`，严格 HTTPS 握手仍失败。阿里云原备案到火山服务器的接入备案／实际拦截状态仍需处理；不能以新 venv 安装通过替代公网门槛。

最终迁移窗口尚未进入。源写入冻结、最终 callbacks、最终新备份／新数据库恢复、migration 010、fresh proof／inspect／apply、正式 Logto runtime 最小权限、受验证 TLS／issuer、产品与独立管理员本人登录，仍按[实际切换清单](../../services/platform/deploy/CUTOVER_20261024.md)逐项实施。当前没有 public cutover、目标 Logto 启动或账号／钱包 alias apply。

## 可审查的安全回执

- [传输与恢复阶段汇总](2026-10-24-logto-transfer-restore-safe.json)、[当前权限](2026-10-24-logto-current-permissions-safe.json)、[服务器内两租户认证](2026-10-24-logto-tenant-logins-safe.json)
- [源库完整 inventory](2026-10-24-logto-source-inventory-safe.json)、[恢复库完整 inventory](2026-10-24-logto-target-inventory-safe.json)、[保留失败状态的完整比较](2026-10-24-logto-inventory-comparison-safe.json)
- [900 项回归与新 wheel](2026-10-24-identity-acl-regression-safe.json)、[新 Linux 安装](2026-10-24-central-acl-linux-package-safe.json)
- [恢复后保留与原服务复核](2026-10-24-logto-retention-safe.json)、[本轮公网复核](2026-10-24-central-public-ingress-safe.json)

本轮数据库名带 rehearsal 后缀；这没有证明 Logto 对数据库派生角色名的 runtime 行为。正式目标是否保留原库名，需要在最终配置与 runtime 验收前单独确定。当前源仍可变化，两端有限时间内的只读一致观察也不是 writer fence；未知费用、held 与 pending 继续保留，不能把旧快照接回可消费权威入口。
