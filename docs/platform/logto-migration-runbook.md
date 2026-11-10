# Logto 与中央会员额度迁移运行手册

日期：2026-10-16。状态：**计划与演练规范，尚未执行迁移**。此文件中的新 CLI 由 [身份计划](../superpowers/plans/2026-10-16-logto-identity.md)、[额度计划](../superpowers/plans/2026-10-16-central-membership-credits.md)、[Easel 计划](../superpowers/plans/2026-10-16-easel-platform-migration.md) 创建。当前仓库不能把这些命令视为已可运行功能。

## 1. 迁移对象、权限与不变量

迁移源是现有独立 `easel_platform` SQLite；目标是中央 PostgreSQL 的用户映射、会员、历史与共享额度服务。**不上传 Easel profile、用户作品、媒体、代理聊天正文、供应商配置或本机模型密钥。** 本地产品数据继续留本机。

旧服务的 accounts、plans、grants、requests、reconciliations 用作只读历史与核对输入。旧账号 ID、grant ID、request ID、时间和 `token-v1` 规则保留；密码哈希、旧会话、refresh／task token 不导入中央。中央 user_id 由统一身份体系分配，`legacy_accounts(source_system, legacy_account_id, user_id)` 关联旧主键，历史不重写为新 ID。

每个迁移run绑定 `run_id + source_sha256 + source_system + rule_version`。任何一项不一致都停止；不能用同run_id套另一个源库。grant额度、used、reserved、pending按整数逐项核对，迁移不发共享额度，不把token当credit。源系统email未验证，匹配邮箱只生成冲突提示，不能认领身份。

操作者需有独立 operator identity；终端不展示凭证。只读盘点角色无发额／写账权限；迁移角色仅能写legacy映射和历史；发测试额度为另一受审计权限。公开产品API不能执行这类操作。

## 2. 环境与进入条件

开发演练使用自己创建的临时HOME、workspace、SQLite副本、Logto数据库、中央数据库、邮件捕获器和随机loopback端口。配置、secret和备份置于非输出目录；禁止使用7860或用户正在使用的服务。开发和生产的issuer、client、数据库、密钥、钱包完全隔离。

真实切换前必须满足：

- Logto固定版本／镜像digest与依赖锁已记录，中央SQL迁移校验通过；数据库角色隔离。
- 真正的托管注册／登录／邮箱验证／找回／SSO有浏览器证据；不能只有OIDC mock。
- 域名、HTTPS、邮件发送、限速、operator恢复入口及私有secret存储已配置；Logto Console不暴露给普通用户。
- 所有产品路由只信任注册audience／client；精确callback allowlist，不使用全局Origin放行。
- 备份还原在隔离环境成功；中央账本审计无差异；所有未决旧费用已明确处理或仍隔离在旧lane。
- [验收矩阵](#8-验收矩阵) 必需项通过，真实模型与支付若未验证仍单独标注。

本计划开发验证通过不自动授权公开部署或真实切换。生产域名／邮件商／支付商不在本次虚构取值，`.test`／example.invalid仅用于合成环境；公开注册前必须提供真实配置并验证。

## 3. 账号认领规则

默认采用双重证明：新鲜旧密码证明＋Logto新鲜身份认证，二者均≤300秒，新的身份邮箱须验证。旧邮箱未验证，所以不要求新邮箱等于旧邮箱，旧账号所有权由旧密码证明，而非邮箱文本证明。证明绑定迁移audience、claim_id、旧ID和一次性jti，绝不把密码传给Logto。

| 场景 | 处理 |
| --- | --- |
| 新用户，无旧记录 | Logto注册，创建个人tenant／wallet，默认无会员、无额度 |
| 旧密码＋新鲜Logto登录，映射未占用 | 原子建立legacy mapping；不自动发额，不改历史 |
| 邮箱相同但没有旧密码证明 | 不关联；提供“关联原账号”说明 |
| 一个旧账号被另一个subject先认领 | `legacy_link_conflict`；人工核对，不能覆盖 |
| 同一新用户请求关联第二个旧账号 | 拒绝自动合并；独立审核与显式迁移方案 |
| 遗失旧密码，旧邮箱从未验证 | `legacy_recovery_required`；人工恢复，不能以同名新邮箱绕过 |
| 已有两个中央身份或已活动钱包 | 不自动并户；冻结争议关联，原钱包／消费保持各自权威 |

所有冲突报告只含内部ID和原因，不输出密码hash、完整token或原始邮箱。对外错误避免账号枚举。

## 4. 演练与切换步骤

以下命令为将实现的操作合同，**仅在E4相关CLI与测试通过后使用**。示例目录 `/tmp/easel-logto-rehearsal` 必须由验证器创建并证明归属；不是用户数据的默认迁移路径。真实操作由run配置明确指定源路径和目标环境，不能自动寻找HOME下的数据库。

### 4.1 一致快照与只读盘点

snapshot命令通过SQLite只读连接和backup API取得一致副本，包含WAL中的已提交状态；目标文件原子写入、0600。不能直接 `cp source.db` 代替。盘点结果只包含表数量、规则、整数总额、状态计数、摘要及异常ID；dry-run不打开可写中央连接。

```sh
rtk proxy uv run --project services/platform python -m platform_service.migration.cli snapshot --source /tmp/easel-logto-rehearsal/legacy.sqlite --output /tmp/easel-logto-rehearsal/snapshot.sqlite
rtk proxy uv run --project services/platform python -m platform_service.migration.cli inventory --source /tmp/easel-logto-rehearsal/snapshot.sqlite --source-system easel-v1 --report /tmp/easel-logto-rehearsal/inventory.json
rtk proxy uv run --project services/platform python -m platform_service.migration.cli import --source /tmp/easel-logto-rehearsal/snapshot.sqlite --source-system easel-v1 --run-id rehearsal-001 --rules legacy-history-v1 --dry-run --report /tmp/easel-logto-rehearsal/dry-run.json
```

预期：退出0、`mode=dry_run`、`target_writes=0`、`credits_issued=0`，源摘要一致。任何schema不兼容、负数、孤儿引用、汇总不一致均退出非0，停止该批次。看不到源文件不等于没有旧用户，必须报告不可读。

### 4.2 先完成身份和新系统验证

合成用户注册Logto并认领旧合成账号，读取个人tenant和billing_account；Operator只在测试数据库授予明确test会员和额度。验证Easel/fixture-second可SSO、不能互用bearer、共享余额正确，随后进入迁移演练。

旧lane尚运行时，旧测试授权仍只在旧服务消费，中央不镜像可花余额。一个账户切到中央后不能再回旧lane创建新模型请求；旧历史与待核算仍可由运营者读取／结算。

### 4.3 关闭旧账户新准入，排空并最终快照

以账户为批次，小批仅先选合成账户；真实批次需另有明确操作范围。先写旧服务持久化围栏，mint和dispatch同时检查，不能只隐藏前端按钮。正在派发的请求等待最终usage；取消后仍无usage的记录保留pending。

```sh
rtk proxy uv run --project services/platform python -m platform_service.migration.cli fence --run-id rehearsal-001 --accounts-file /tmp/easel-logto-rehearsal/cohort.json --mode stop-legacy-admission
rtk proxy uv run --project services/platform python -m platform_service.migration.cli check-drain --run-id rehearsal-001 --report /tmp/easel-logto-rehearsal/drain.json
```

预期drain报告 `active=0`。若 `pending>0`，保持围栏，不启中央账户准入；使用可信usage／运营对账处理，不写0骗过门槛。默认本计划不把未决legacy账迁入可用credit，不能证明历史费用则该账户仍隔离。

排空后重新snapshot。它是新源指纹，使用新run `rehearsal-final-001`，引用原run作为parent，不能篡改旧run摘要。生成最终dry-run，人工读取差异和例外清单。若旧服务在围栏后仍有新dispatch，验收失败，修复围栏后重新演练。

```sh
rtk proxy uv run --project services/platform python -m platform_service.migration.cli snapshot --source /tmp/easel-logto-rehearsal/legacy.sqlite --output /tmp/easel-logto-rehearsal/final-snapshot.sqlite
rtk proxy uv run --project services/platform python -m platform_service.migration.cli import --source /tmp/easel-logto-rehearsal/final-snapshot.sqlite --source-system easel-v1 --run-id rehearsal-final-001 --parent-run rehearsal-001 --rules legacy-history-v1 --dry-run --report /tmp/easel-logto-rehearsal/final-dry-run.json
```

### 4.4 幂等导入与核对

```sh
rtk proxy uv run --project services/platform python -m platform_service.migration.cli import --source /tmp/easel-logto-rehearsal/final-snapshot.sqlite --source-system easel-v1 --run-id rehearsal-final-001 --parent-run rehearsal-001 --rules legacy-history-v1 --apply --report /tmp/easel-logto-rehearsal/applied.json
rtk proxy uv run --project services/platform python -m platform_service.migration.cli verify --run-id rehearsal-final-001 --report /tmp/easel-logto-rehearsal/verified.json
```

`--apply`命令要求该源摘要已有成功dry-run，目标环境与run指定环境一致，具备受限migration角色；不接受客户端提供的余额或用户token替代operator权限。每个批次写checkpoint，重跑同命令无重复数据；不同源、不同规则或并发run冲突拒绝。

核对包括：账户／映射数、每表记录数、逐行摘要、grant quotas、历史input/output及used总和、reserved与pending数、孤儿引用、中央credits_issued=0、钱包分录合计0。不能只对比总账户数量。

### 4.5 启用中央准入及旧测试权益处理

先确认最终核对通过和旧lane持久围栏仍有效，再开放中央账户准入。切换中间可能短暂停止订阅，不采用“两个服务都可以扣”的补偿方式。Easel要求显式重新认证并取得新Scope；旧会话不迁移、不向中央发送旧bearer。

```sh
rtk proxy uv run --project services/platform python -m platform_service.migration.cli activate-central --run-id rehearsal-final-001 --accounts-file /tmp/easel-logto-rehearsal/cohort.json --report /tmp/easel-logto-rehearsal/activated.json
```

该命令再次核验final报告、旧围栏、未决费用与绑定身份；失败保持中央准入关闭。账号关联到迁移批次后，中央task mint与每次reserve均读取migration_admission，不能只靠本机UI阻止绕过。

默认：旧grant归档并显示Easel-only测试历史，不兑换共享credit。若需要继续给测试用户权益，运营者另行创建Easel-only test会员／lot，指定数额、期限、来源和唯一issuance_key；其经济含义是新的测试授予，报告与旧token历史分开，禁止对同一补发重复发额。正式付费或跨产品通用会员不能由这个迁移命令产生。

观察一个完整测试会员周期，并至少通过一次过期、撤销、刷新、断流pending／对账、备份恢复演练后，才评估关闭旧密码端点和旧lane。合成环境可用注入时钟推进周期，不能更改设备系统时间或伪造真实IdP的token期限。旧历史保留只读；未认领账户和争议记录不删除。

## 5. 报告合同

每个报告带生成时间、代码commit、规则版本、环境ID、源摘要、run和parent。示例是合成数据格式，不是迁移结果：

```json
{
  "run_id": "rehearsal-final-001",
  "parent_run_id": "rehearsal-001",
  "source_system": "easel-v1",
  "rule_version": "legacy-history-v1",
  "mode": "dry_run",
  "status": "passed",
  "accounts_read": 2,
  "identities_linked": 1,
  "conflicts": 0,
  "legacy_active": 0,
  "legacy_pending": 0,
  "legacy_used_tokens": "123",
  "credits_issued": "0",
  "target_writes": 0,
  "checks": {"row_hashes": true, "references": true, "totals": true},
  "exceptions": []
}
```

真实报告必须额外包含实际source_sha256、checkpoint和证据文件摘要；源不存在或检查未执行时status为failed/blocked，不能写示例的passed。异常列表按旧ID和原因记录，可重跑、可解释；原始报告在私有审计目录，公开验证文档仅保留脱敏摘要。

## 6. 回滚与灾难恢复

| 发生点 | 操作 | 不变量 |
| --- | --- | --- |
| 仅部署新代码，无身份关联／新消费 | 关闭v2入口，使用原legacy入口；删除仅自己拥有的测试资源 | 不改原DB和用户配置 |
| 已关联身份，无新消费 | 回退UI／broker适配；保留mapping；按核对结果决定是否解除旧账户围栏 | 不按邮箱重新绑，不自动清账号 |
| 中央已消费／预留 | 暂停所有产品新reserve，排空或保留pending，回退接入层或前向修复 | 中央仍唯一权威，旧lane继续fenced |
| 中央数据库不可用 | 订阅fail closed，允许自定义；恢复流程在隔离实例验证完整性与上游未决费用后再开放 | 不从旧SQLite发放可花余额 |
| 日志／凭证泄露 | 撤销受影响会话／轮换机密、审计调用与费用、修复输出后验收 | 不删除账本掩盖费用 |

灾备采用加密全量备份＋WAL归档，Logto与中央分别保存，具备恢复顺序和外部subject映射检查。演练目标RPO≤5分钟、RTO≤60分钟，是待测目标；实际结果以演练证据为准。若灾难恢复丢失已提交但未备份的账务窗口，先冻结准入并与上游usage／outbox证据对账，不能把恢复快照当最新可花余额立刻上线。平时代码回滚不执行数据库倒退迁移或财务分录删除。

## 7. 验证命令与证据

新脚本尚需实施，运行时各自创建并清理资源，不能访问真实模型端点：

```text
rtk proxy uv run --project services/platform --group test pytest services/platform/tests -q
rtk proxy uv run --project services/platform python services/platform/scripts/verify_identity.py --fixture-only --evidence-dir /tmp/easel-owned-identity-evidence
rtk proxy uv run --project services/platform python services/platform/scripts/verify_shared_credits.py --fixture-only --evidence-dir /tmp/easel-owned-credit-evidence
rtk proxy .venv/bin/python scripts/validation/verify_central_platform.py --fixture-only --codex /Users/jiandan/.nvm/versions/node/v24.14.0/bin/codex --evidence-dir /tmp/easel-owned-central-evidence
rtk proxy .venv/bin/python scripts/validation/run_isolated_tests.py --pytest-path /opt/anaconda3/lib/python3.12/site-packages tests -q --tb=short
rtk proxy node --test tests/frontend/*.test.mjs
rtk proxy npm --prefix web/frontend run lint
rtk proxy npm --prefix web/frontend run build
```

上述Codex路径为2026-10-16本机通过PATH核对值；执行前再次验证存在及版本，换机器使用该机器已核对路径。pytest的site-packages路径为当前本机基线，换环境应先检查pytest位置再填写，不把此路径当跨机器依赖。`--with-owned-logto`模式独立验证真实IdP；mock通过不能替代。

所有验证记录包含准确commit、时间、版本、通过／失败／跳过／阻塞、私有资源清理结果、保留的已知限制。现有 [phase3账号验收](../validation/2026-10-16-platform-accounts.md) 为旧基线，不是Logto验收。

## 8. 验收矩阵

| 必需场景 | 期待结果 | 负责计划 |
| --- | --- | --- |
| 统一注册／验证／恢复／双产品SSO | 托管UI可用，两个独立产品会话，同一中央身份 | I2、I5、E5 |
| state／nonce／PKCE／回调重放／开放重定向 | 全部拒绝；无access/refresh/task token进URL／浏览器响应，单次协议码禁止日志外泄 | I2、E1 |
| 退出／refresh重放／成员撤销／IdP禁用事件丢失 | 中央即时撤销；外部撤销最迟身份租约到期拒绝 | I3 |
| 跨产品bearer、跨租户、错模型、普通用户发额 | 403且无账务副作用 | I3、C2、C4 |
| 会员缺失但有余额／有会员但余额不足 | 拒绝准入，错误原因分明 | C2、C3 |
| 两产品同钱包同时预留；32连接竞争 | 余额／并发不超限、无负数、平衡分录 | C3、C5 |
| 重复请求／不同body同key／重复usage | 重复复用、冲突409、不重复扣费 | C3、C4 |
| 断流、取消、崩溃、另一个worker重启 | 未知消耗保留pending；不释放其他worker的hold | C4、C5 |
| lot或会员到期、费率变更、费用超上界 | 已准入按冻结规则核算；超界熔断并保持争议记录 | C1、C4、C5 |
| 未验证同名邮箱、双重认领、遗失旧密码 | 不自动合并，进入明确恢复／冲突流程 | I4、E4 |
| 重复导入／批次中断／WAL／源改变 | 幂等、可恢复、快照一致、拒绝不匹配源 | E4 |
| 旧pending／测试grant补发 | 不清零、不自动兑换、不重复授额 | E4、E6 |
| 平台／Logto不可达、匿名用户 | 自定义模式仍可使用；不静默切换付费来源 | E3、E5 |
| 原生工具、SSE、凭证／文件／归档／包 | 工具本地执行，secret不公开；服务端不进桌面包 | E1、E3、E5 |
| 已产生共享消费后的回滚 | 保留中央账本，所有产品共用围栏后恢复 | E6 |

当前此矩阵全部是待实施验收条件。本次交付没有执行这些测试，也没有声称完成Logto接入、真实收费、真实模型或云端执行。
