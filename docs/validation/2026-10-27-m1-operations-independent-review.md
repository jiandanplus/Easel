# M1.1 部署准备、切换与新媒体恢复：独立审查

日期：2026-10-27；独立 reviewer：`delivery_media_review`。本次只读审查产品/运维实现，仅写独立测试证据和报告；没有执行生产 apply、stop/start、cutover 或 enable。

**结论：下列最终冻结的三个工具没有剩余已知阻塞。独立组合测试 56 passed、0 skipped，31.79s，exit 0；包含本机新隔离 PG 的非空合成媒体加密恢复。** 实际生产迁移、零资金差异、真实源首轮空媒体恢复，由 root 按已审精确计划分别执行并提供回执，不能由本报告替代。

## 冻结输入

| 文件 | SHA256 |
|---|---|
| prepare_m1_image_release.py | ff9d019ef275798b24e12adcc25c8018c9f8f3ff244dc003133f2df4912c02ef |
| prepare_m1_image_cutover.py | 32ba883a5362af05edf3a53dc070c05f80ec8275a947a4bb7063078ae80aba36 |
| verify_media_restore_drill.py | 68dccf3699a3c9b06d79e5cf87609dcf567b2d6c93c8b3c6186cc40c3474e1d3 |
| 未修改的严格 R2 v6 恢复核心 | 4d3c4210469d6630dd4cbc71d8d5327e62aa972eedb7084685bb0df85a2c88ea |

目标后端 wheel 为 `f50e52511e941d3bea8e724eddbd68330e20eaa6339c5e2437b3880ca9705abf`；016 为 `a5a1dcfdb90299ebd063b18377a900ad68e1338bb2137cba9a7010f9694d6cba`。三个工具、v6 核心及三测试文件共 7 个输入在执行前后、写报告前均逐项 hash 相同。最终 DR 测试 SHA 为 `43490c8c3b66a963ab3106304056e718eadf168514a8e69a3974b3d1edf85644`，显式指定实际冻结 fixture，未因本机临时路径或 Docker 开关缺失而 skip。

## 六动作 disabled apply 的边界

完整读取 prepare 的 plan/apply/enable 分支，静态核验：

- 必须使用新 staged Python、完整匹配 83 文件候选和 76 个 py/sql，Pillow 固定 12.3.0；原数据库迁移必须恰为匹配旧 001–013 checksum 的 R2 基线。
- 私有 owner locator 留在服务器。owner DSN 必须与运行 DSN 的 host/port/database 相同，且原始连接用户确为目标 schema owner；不把账号、DSN、Key 或私有图片 URL 输出到安全回执。
- apply 前比较精确计划 SHA、工具 SHA、原 unit/env/execution/timer hash、已审核备份回执 hash、两模型真实协议/PNG 证据、资金行 hash 与原 grant 全行。两原 unit 必须 inactive，reserved/dispatched 执行数必须 0。
- 仅迁移 014–016、重新授予 API runtime 的精确权限、为那一个原 grant 追加 image 功能、发布两条模型定义为 disabled、写 UID 所属 0600 media 配置、准备 0700 owned storage。
- 不新建会员、点数、订单或付款，不延长原 grant/lot；结束再次完整比较原财务表行 hash。两模型必须恰好 2 条且均 disabled。
- 该阶段不更改 unit、不启动/停止服务、不发供应商 POST。enable 是另一条显式命令，要求后迁移严格恢复 proof 与新 release 的 unit；本轮没有执行。

测试中的 5 项是私有文件/精确 hash/费率参数/安全投影/停机与 snapshot 纯合成守卫；没有伪装为实际生产迁移。多步迁移失败需保留现场按已审 forward repair 或备份恢复处理，不能把“切回旧 unit”描述为已验证数据库回滚。

## 候选和 cutover 的边界

cutover 工具与 prepare helper SHA 绑定，计划保留原两主 unit/env 原字节及 hash，固定 loopback 19876 候选、19874/19875 原端口。candidate-start 必须有 exact applied-disabled 回执，原两 unit inactive，已经 active 的候选拒绝；不会把旧候选进程当作新实例。

候选/生产 smoke 除 WorkingDirectory 属性，还检查实际 MainPID、/proc cwd、argv 和解释器；真实新 release 进程才可进入健康检查。公共 GET 只访问 health/capabilities 和两条必须 401 的受保护 catalog；没有创建或提取用户登录 token，也没有真实模型请求。runtime 用默认只读事务连接复核安全角色；原 text model 定义不变、image 模型 disabled、资金 snapshot 前后不变才通过。

切换需要 exact candidate-smoke 回执，原两 unit 仍 inactive，无未知 drop-in。保留原主 unit 和私有 env，仅写固定 2 个 90-m1-images.conf，清旧 ExecStart 并使用新解释器/WorkingDirectory。API 的 MEDIA 通过 drop-in Environment 生效；原 EnvironmentFile 若写有 MEDIA 必须一致。恢复工具独立生成相同精确字节，不接受泛化 systemd 配置近似。

只读 smoke 和角色/资金检查的 12 项本机 fixture 已独立执行；实际 Linux systemd/进程/公网/登录是否成功，仍以 root 后续服务器回执为准。

## 新恢复工具的范围

新工具复用 SHA 固定 v6 的原始 schema、全部数据、角色/owner/ACL、约束 flags、序列和资金平衡严格比较；不改旧工具或 timer，不放宽 fingerprint。仅保留那 7 条已审核旧 BETWEEN CHECK 在新 owned target 上的 exact 源语法重建。

新覆盖包括原 6 私有配置、media.json、2 主 unit、2 固定 drop-in，共 **11 个文件**。捕获 bytes、SHA、UID、GID、mode，入认证密文并在新 root 私有目录完整逐字节/权限还原；不自动启用还原应用。固定来源 allowlist、NOFOLLOW/单链接/前后身份检查、O_EXCL 目的地、路径穿越和混入文件拒绝。media registry 的 attached 文件必须完整匹配；staged/gc_pending、part、未知/漏文件、错大小/hash/owner、软硬链接或读取中变化均拒绝，不能悄悄跳过。

真实第一轮仅允许：新 release 的 API 与 console 均 active；实际 PID/cwd/argv/exe 对应新 release；effective media path 正确；两模型 disabled；invocation/job/blob/budget 均 0 且 owned storage 为空。plan、中央 exported snapshot 前/内/后及最后再验配置与该窗口。首轮 `blob_count=0`、`nonempty_media_blobs_restore_verified=false` 必须如实输出。实际媒体生成后的非空恢复另建 nonce/计划。

数据库各自只读快照，声明不跨库原子、不具备 PITR。捕获总上限 32 MiB，超过失败，不丢内容假称完整日常媒体备份。目标 PG 独立 nonce、network none、无公开端口、512 MiB/0.75 CPU/128 PIDs；源码 guard 验证自有容器/卷后才停止，成功失败均停止保留，不清理原对象。

## 独立实际执行证据

```text
56 passed in 31.79s
0 skipped
exit_code=0
```

组成：prepare 5、cutover 12、恢复 39。原始日志 `/private/tmp/easel-ops-independent-review-20261027/ops-final-tests.log`。

其中实际非空测试使用新 nonce 的本机 PostgreSQL、完整 001–016，真实受 FK/trigger 约束的 invocation/asset/attached blob，而不是孤立伪造 registry：

- 75 表、529 约束、29 条合成数据，完整 inventory/hash/flags 严格相等。
- 1 个 34 字节合成 blob，registry 归属、内容、SHA 和 0600/0700 权限恢复相同。
- 11 配置文件，包括主 unit/drop-in，bytes、owner/group/mode 还原相同。
- 新生成的合成 RSA/AES 密文写入文件并重新读取认证后，再由新空 PG 和私有文件目录恢复；篡改密文拒绝。这里不代表断电测试。
- 本机 UID/GID 为 501:20；生产工具另外强制真实 media 用户 997:997。不能把本机身份验证冒作已执行服务器身份验证。
- 实际非空测试恢复的是 **central 数据库**；新 whole-tool 的真实双库执行没有在本轮发生。其生产循环和 proof 必须要求 central/identity 都 matched，身份库沿用未改 v6 核心；实际双库新回执仍由 root 获取。

机器可读合成回环证据：`/private/tmp/easel-ops-independent-review-20261027/synthetic-nonempty-independent-safe.json`。新测试容器均由自有 nonce guard 停止并保留，未删除容器或卷。原应用、旧 R2 恢复证明和旧 timer 未修改。

本报告与同名 safe JSON 不包含真实 Key、DSN、私有 plan ID/原账号 Scope 或签名 URL。它提供代码与本机隔离验证结论，不能替代 root 对精确生产计划、短维护窗口、资金 hash、真实 blob_count=0 和正式启用的逐步核验。
