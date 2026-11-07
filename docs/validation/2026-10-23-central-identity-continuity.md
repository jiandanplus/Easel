# S5-A2 身份连续性与隔离恢复验收

本记录对应 `codex/ui-redesign` 的账号迁移准备开发。自定义接口与中央订阅两条路径继续保留；本批不提交、不推送、不合并主分支，不改真实身份库、正式钱包、New API、DNS、TLS 或服务器服务。

## 实现范围

新增 migration 010 和五张不可变表，保存限时恢复证明、规范候选、迁移批次／明细及 Console 身份 alias。维护命令实际比对两个受信身份数据库，核对批准归档 SHA 后记录 proof；apply 只接受 proof／幂等 key／原因，自己读取原主体，不能上传任意用户映射。

新 issuer 使用同一 Logto 的原 subject，产品解析回原 user／tenant／wallet，Console 指向原 admin 并实时沿用原 active／role／epoch。禁止按邮箱合并或禁用账号复活，映射阶段保留原会话和全部财务事实。

本批明确包含维护命令、数据库约束与权限、合成签名 OIDC、真实合成 PostgreSQL 比对和实际 dump／restore 演练；不包含真实 Logto 库兼容性、本人新 issuer 登录、生产切流或正式付款。完整操作合同见 [身份迁移手册](../../services/platform/IDENTITY_MIGRATION.md)。

## 审查与修复

独立核心审查发现原实现每条 deferred trigger 重复全批扫描。修复为 proof 自动一次规范化、即时索引成员绑定和每批一次最终集合校验，同时覆盖提前 `SET CONSTRAINTS ALL IMMEDIATE` 后追加条目绕过的回归。规范候选不能直接追加；五张表都不能更新／删除／truncate。

一万身份合成规模验证实际触发了原逐身份锁的 PostgreSQL 锁表资源上限。产品首次登录、Console resolver／写入与迁移现共用各自 schema 和 kind 隔离的 1024 锁槽；同身份必同槽，碰撞仅增加串行。迁移按固定次序获取去重锁，不支持旧锁协议 writer 混跑。产品 owner-wallet 查询也改为一次集合查询。单条 SQL 30 秒、锁等待 2 秒、整个事务 180 秒，超时完整回滚。这些是执行上限与合成验证，不是生产性能或可用性承诺。

CLI 独立审查发现连接池重试日志先于固定错误处理输出底层诊断，以及 open 失败不进入 close 的问题。新增 canary 回归实际 RED 后修复：专用维护进程作用域抑制两个实际 driver logger，结束恢复日志状态；open／close 共用 finally。参数、返回值和错误均不输出 DSN、密码、真实 subject 或整行身份数据。

## 验证结果

冻结源码后的完整中央服务回归已完成：**868 passed in 89.51s**，包含两组一万候选和全部既有测试，没有排除规模用例。下表的各项是不同证据，不累加为额外测试总数。

| 验证 | 实际结果 |
| --- | --- |
| 完整中央回归 | `services/platform/.venv/bin/python -m pytest services/platform/tests -q --tb=short`，显式私有合成 DSN 文件；868 passed／89.51s |
| 独立核心审查 | 最终 PASS，初版复杂度 P2 关闭；见[审查原始记录](2026-10-23-central-identity-core-review.md) |
| 独立 CLI 审查 | 最终 PASS，日志隐私与 open 失败资源清理已修；见[CLI 审查](2026-10-23-central-identity-cli-review.md) |
| 实际合成 IdP 恢复 | pg_dump／pg_restore 后由真实只读 collector 比对，通过后迁移 1 个产品身份和 2 个 Console 身份 |
| 实际合成中央库恢复 | 新空数据库、同 schema 名；51 张表、10 条迁移，以及全表数据／已记录结构／序列定义、状态和所属关系摘要一致 |
| 恢复后的真实低权连接 | 产品／Console 守卫通过，旧 Console 会话认证与钱包读取通过，敏感 token 列读取被拒绝 |
| 资金与权限语义 | 1 个 settled（2000 microcredits）、1 个 pending 且 held 保持、1 个会员；账本／钱包／journal 平衡，原产品用户、启用管理员和禁用 viewer 保持 |
| Console 新构建 | TypeScript／Vite 通过，静态资源 receipt `c3fddd3e9849e2f71057f02f9ef4c3abf9086f5391f15142e5270e839651d62f` |
| 新 wheel 与源码核对 | 56 文件逐字节一致：42 Python、10 SQL、4 static；wheel 212343 bytes，SHA256 `f602aa14ea7276cd24a25fd77fe878bc780045f36fe42ba99954156d6655ebd9` |
| 干净安装 | 新 Python 3.12.7 venv 按带哈希锁文件安装，26 个 distribution；API／Console 构造、3 个身份命令导入和 10 条 SQL 检查通过；禁止 socket 连接，不启动 lifespan，不读取私有业务配置 |
| 历史迁移保持 | 001–009 逐字节与 S5-A1 旧 wheel 一致，依赖锁 SHA 仍为 `eb00eae7b6966760595376c65da0571d40406dc2d2a3019c1247f575dfa44f2a` |

[恢复安全收据](2026-10-23-central-identity-restore-receipt.json)与[构建安全收据](2026-10-23-central-identity-build-receipt.json)只含摘要、计数和公开配置；真实模型调用和支付调用均为 0。v3 归档与合成连接文件保留在 `/private/tmp/easel-identity-continuity-20261023/actual-restore-v3/`（目录 0700、文件 0600），不放入公开包。

演练并非首次即通过：v1 把内置 information_schema 误计为业务 schema，在恢复前停止；v2 在低权验证中把 keyword DSN 传给只接受 URL 的 ConsoleSettings，实际恢复与数据比对已通过但整体失败。两项都先纯本地 RED 再最小修复，未知 schema 拒绝、真实低权连接和全部资金／权限检查未放宽。fresh v3 最终整体 PASS，实际执行收据时间为 `2026-10-23T15:50:05.414167+00:00`。完整过程见[演练记录](2026-10-23-central-identity-restore-drill.md)。

开发中一组 166 项相关运行曾有 5 项既有时间边界断言失败（161 passed）：产品 transaction／handoff／access／refresh 的过期检查，以及 Console `auth_time +61`。之后单次诊断通过，但失败时的时钟偏差未被捕获，原因未确定；没有修改旧 TTL 或断言，不能称这些失败已修复。静态证据显示测试跨用主机与 DB 时钟，一秒与整秒截断边界易受执行延迟影响；这只是线索，不能当作根因结论。**最终一次 fresh 全量运行 868 项均通过，包含上述五项。** 全量前后两次数据库相对主机中点时间分别为 +0.004874 秒与 +0.008177 秒；它们不证明较早失败时的偏差，生产前仍需验证时钟同步与时间边界稳定性。

## 分支与资源收尾

仍为 `codex/ui-redesign`；HEAD `e972af07444e181e3eb737441ecac306bca778b3` 与 main `bb3c8ff0463f36c202454d74171682ba6e470cb1` 保持，本轮没有 Git 写入、提交、推送或合并。

仅启动并在所有测试结束后停止专用容器 `easel-console-tests-cbabf5af7060`，已实查 running=false；没有停止 Logto、其他 Docker 应用或服务器 New API。v3 成功时仅清理该轮随机 schema／数据库／角色；v1／v2 失败现场保留至容器停止，其私有归档、连接文件和诊断收据仍在 0700／0600 目录中。该专用库使用 tmpfs，容器停止后内存中的合成现场不持久；保留的 dump 才是可恢复证据，没有声称保留在线数据库。没有删除容器、数据卷、原预览或真实数据。

## 正式发布剩余门槛

1. 用受控只读流程核对真实固定版本 Logto 的完整 schema、类型、RLS 和角色／ACL；当前 collector 不支持的结构须经实现和测试补齐，不能删对象或忽略差异。
2. 备份真实身份库、原中央权威库与角色配置，实际恢复到隔离目标并核账；collector 比较不等于备份操作来源链或 writer fencing。恢复副本不能同时开放花费。
3. 在 `115.190.211.112` 完成独立服务、受验证证书、三个域名的无消费验收和重启／备份验证。S5-A1 的 DNS 观察及 TLS 主机名校验失败是历史记录，本批没有重新探测或切换。
4. 正式切换前停新登录／新派发并阻断旧 writer，明确处理旧登录事务、handoff、会话、在途与 pending；迁移命令不提供一键 drain／全局注销。
5. 本人通过新 issuer 登录，核对原 user／wallet／admin／role 与账本，再做有界真实模型和正式商户验收。未知费用保留 held，禁止重发余额或回滚快照继续消费。

历史 S5-A1 wheel／ZIP 保留，新 migration 010 必须使用本批新 wheel 与对应 SHA，不能把旧包当成已经包含本次实现。

本批[可审阅交付包](../../outputs/_run/central-identity-continuity-20261023.zip)包含新 wheel、锁文件、公开部署配置／模板、操作手册和安全验收记录，逐文件 SHA256 清单另附在包内；不含 dump、连接文件、真实密钥或用户数据。它仍是部署准备包，`production_ready=false`。
