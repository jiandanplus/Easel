# 中央平台正式部署准备：独立代码与规格审查

日期：2026-10-23。范围：S5-A1 本批八份文件及 `services/platform/README.md` 公开上下文、实施报告和实际生成的 v1 公开部署报告。审查分支为 `codex/ui-redesign`；本次只读源码和公开资料，仅写本报告，未修改实现、运行测试、访问网络／数据库、读取私密配置或执行部署。

## 两项结论

- **规格符合性：最终 PASS（S5-A1 离线准备范围）。** 离线工具、严格公开输入、wheel／当前源码逐文件一致、排他输出、双服务隔离模板、身份／资金迁移阻挡与未完成证据边界均符合。初审发现的数字 IP 别名 P2 已完成最小修复和独立源码复审；历史发现及修复证据保留于下文。
- **实现质量：最终 PASS（无未关闭的重要发现）。** 初审唯一 P2 已关闭，未发现其他 P0／P1 或重要的自动执行、凭据外泄、既有服务覆盖问题。本次并未运行生成器或测试；实现者与root的79通过、真实wheel和新venv结果按各执行者归属记录。该结论针对离线准备包，不表示三域名或生产服务已发布。

## 已关闭 P2：初版目标 DNS 校验接受分段十六进制 IPv4 别名

位置：[prepare_deployment.py](../../services/platform/scripts/prepare_deployment.py:68) 的 `_dns()`，以及第82行的 `_url()`。

`_dns` 排除 `ipaddress.ip_address()` 能识别的规范 IP 和末段纯数字 hostname，但对 `0x7f.0x0.0x0.0x1`：末段 `0x1` 不是 `isdigit()`、各段符合当前 DNS label 正则，规范 IP 解析失败后就继续作为 DNS 接收。传统数字 IPv4／URL 解析可以把这类表示解释为 loopback 地址。这违背了明确的“目标只接受 DNS hostname，拒绝 IP”合同，也会使原样生成的 Host 与某些客户端规范化后的目标不同。

本发现来自逐分支源码推导，本次没有执行利用输入、DNS 或测试。其影响是公开配置严格校验缺口，不是本工具发起 SSRF，也不影响已确认的三条 `drawbookai.cn` 域名。工具本身仍不联网，严重度为 P2。

建议：对目标域名拒绝传统 IPv4 数字别名（至少覆盖末段十六进制数值形式，并审视混合／八进制形式），在 public_url、console_public_url、oidc_issuer 添加真实断言 RED→GREEN，保持 source_oidc_issuer 的 literal-loopback HTTP 合同和现有有效域名。修复后由执行者复跑 focused suite，并重新生成新的最终目录，保留 v1 中间证据。

状态：初版 Changes requested；现经下述最小补丁源码复审关闭。以下描述保留初审问题，不代表最终源码仍有该缺口。

## 其余规格与质量核查

**输入与错误。** 配置最多64KiB、对象重复／缺失／未知字段拒绝；版本与端口用精确整数类型拒绝 bool；目标 HTTPS／443、独立 hostname、固定 issuer `/oidc`；凭据、query、fragment、控制字符、百分号／反斜线等拒绝。部署路径用 ASCII 组件白名单阻止模板插值及 shell／Nginx／systemd 语法注入。原 issuer 精确保留，不能用批准布尔值消除迁移门槛。CLI 错误仅固定 JSON 代码，无输入内容、路径或 traceback。未发现 subprocess、socket、数据库、平台运行时导入或环境凭据加载路径。

**wheel／源码。** 只在内存读取 wheel，不安装或解压。压缩包体、成员数、单文件、展开总量有界；拒绝重复名、穿越／绝对路径、链接和异常文件类型、加密或未支持压缩。所有当前服务 Python／SQL 和 frontend/dist 静态文件按集合和字节匹配；服务外仅允许该项目 dist-info 范围。项目 Name／Version 与公开 pyproject 和 dist-info 一致。`Requires-Dist`、第三方依赖内容、签名和 lock 解析未认证，生成报告明确 `dependency_metadata_authenticated=false`，没有把“源码相同”写成供应链安全证明。当前dist与wheel相同不独立证明前端构建输入新鲜度；现有前端构建／资产回执验收是另一个证据层。

**输出及既有对象。** 仅新建用户指定的不存在输出目录，0700，六个文件排他创建0600；已有目录、文件或最终路径符号链接不覆盖。新目录的部分失败保留，不自动删除旧内容。生成器没有服务、DNS、数据库、Logto 或 New API 操作，也不读旧预览／工作区或迁移余额。任意安全路径输入生成模板仍需要运营者核对真实目录、端口、所有权和服务身份；工具不声称远端检查过这些事实。

**systemd 模板。** 正确使用 release `.venv/bin/python -I -B`、两个独立非 root 用户、固定127.0.0.1和单进程。owner DSN／内部发点 policy、Python／动态加载／代理／PG 覆盖及 worker 覆盖从环境排除；Console 不加载产品执行／支付配置，API 不加载 Console DSN／secret。只读系统和配置、私有tmp／设备、无capabilities、重启限速、内存／任务上限明确。env模板不被unit直接加载，REPLACE值不能当成已配置secret。真实主机 systemd 兼容性、运行用户／目录权限、稳定解释器、可安装锁定依赖、容量仍必须在部署时核验，未通过静态模板宣称完成。

**Nginx 与隐私。** API／Console各自443站点，固定Host并拒错Host，Origin保留，不添加任意CORS；上游仅loopback；API1MiB／Console64KiB；API关闭buffering，180秒读／发超时覆盖当前120秒期限。证书api／console子路径与手册一致，无全局default_server或现有NewAPI站点覆盖。access_log关闭、error_log指向/dev/null，避免原始callback／认证／正文日志；这需要独立无敏感数据指标和告警，报告已保留观测能力缺口。生成器不包含LogtoHTTPS站点／部署；Logto是明确的单独迁移前置阶段。

**身份、财务与回退。** 原P1写入分叉意见已在当前spec／runbook补齐：默认复用原权威DB／schema，恢复副本仅演练；需要明确旧writer fencing、晚到结算／recovery、最终核账截止点和唯一可写账本再开放。CLI尚无drain/fence，文档明确不得伪造该能力。身份迁移保持issuer+subject严格匹配，产品别名与Console保留admin ID能力未验收，不按邮箱合并／重新审批冒充迁移；禁用状态、历史身份／审计、user／tenant／wallet ID和pending均须保留。旧快照不能恢复为新可花余额。备份归档、角色和真实映射保持私有；恢复命令明确全新隔离目标且`--no-owner --no-acl`不等于保留角色权限。回退不清pending、不删审计、不重建钱包。未再发现该批文档间重要矛盾。

## 已读证据及未执行边界

实施报告记录 TDD collection RED、初版49通过、数字域名补测RED→55通过、root复审8个RED→最终64通过。root另行报告fresh64 passed／0.15s；这些测试没有由本审查者重跑。实施报告逐文件证明实际wheel52文件＝39Python＋9SQL＋4静态文件，wheel SHA为 `7addda835c3f4201d63c6c1f68e8173e5e8bb41e4f68f63dc98e44551745c24b`。root报告新的隔离venv按锁定哈希安装26包并构造API／Console成功，属于root的装包证据，不是本审查的远端运行或生命周期启动。

已实际读取 v1 `deployment-report.json`：三个真实公开域名正确，52项源清单；`offline_only`、`production_ready=false`、`identity_migration_required=true`、依赖元数据未认证和真实部署待办均保留。v1 是本次P2修复前的中间生成物，不得在修复前标为最终 clean 包。

根据root的独立外部观察，用户修改后公共DNS三条A均为115.190.211.112且无AAAA，但三站当前TLS均hostname验证失败；没有绕过证书校验或发送业务body／凭据。本审查未复测这些网络结果。DNS指向正确不等于TLS／Logto／正式API／Console已发布。尚未安装或重启正式服务、申请证书、迁移真实身份／管理员映射、执行恢复演练、切换权威账本、进行新地址真实登录／模型／支付验收。独立预览此前验收不能替代这些生产证据。

本批实际验收记录 `2026-10-23-central-production-preparation.md` 在初次读取时尚未落盘，属于root的closeout工作；正式交付前应补齐并链接实际最终生成物／修复测试，不能保留断链。

## 初版审阅文件哈希

- `docs/superpowers/specs/2026-10-23-central-production-preparation-design.md` — `978cd4f1e8e7775e3216074a3187033cdf029d8e894d2fa1fd011cb194fa8ed7`
- `docs/superpowers/plans/2026-10-23-central-production-preparation.md` — `29befc32139ab27da0c9f8594267cd65b6e1bae8b4ea2764e087e6300d2437b5`
- `services/platform/scripts/prepare_deployment.py` — `9a3dd95be7d76549d2482af65af818dae6e8c68de777975b936d856d8810b00a`
- `services/platform/tests/test_deployment_preparation.py` — `bef0d76261345234316d0dfe5dc5cb5665710a02f35a5b9ae16e212ccd1529a2`
- `services/platform/deploy/production.example.json` — `0e0b47626b9d51f27c94b1a1aa1764f754cc03293bd7d23f5c98e688ea4fc393`
- `services/platform/deploy/drawbookai.public.json` — `01cf97a4acebcc82c2333879b231d27b50d5e29b28facc826a302441cfee0732`
- `services/platform/deploy/README.md` — `9bab7c892e35e207d5c14e88f68b48da2d24ebf4357c966951d385b3fa327cc1`
- `docs/validation/2026-10-23-central-production-identity-audit.md` — `83b9846e81cee0e2d627c37c261386c5aad00a51f51c1ae6694d5712c7674ae5`

## P2 修复复审与最终关闭

本次只读复审 `/private/tmp/easel-production-preparation/hex-alias-fix-review.patch`、当前两文件与初始八文件审查包之间的精确差异，并读取已追加实施报告。没有导入生成器、重跑测试、调用 DNS／TLS／数据库或修改实现。

生产代码仅在 `_dns()` 既有末段纯数字拒绝条件后增加 case-insensitive `0x[0-9a-f]+` 末段拒绝。目标public／console／issuer共用该校验，现覆盖分段十六进制及hex／decimal混合别名；末段decimal／octal数字仍被原isdigit条件拒绝。未改变正常三条公开域名、模板、wheel校验、输出路径或source issuer分支。原source literal-loopback HTTP仍由ipaddress解析精确IPv4／IPv6，保留字符串并拒绝hex别名，不新增解析或网络行为。

新增15个参数化用例包含三目标字段×四种hex／mixed别名、两个合法source literal-loopback和一个source hex拒绝。实现者记录先RED6failed／9passed／64deselected（六个明确DID NOT RAISE），最小修复后target15passed、完整79passed／0.15s。root另独立fresh完整79passed／0.15s。两者运行归属明确，本审查者只读测试设计及结果报告，未将其冒认为独立重跑。测试既验证原漏洞也确认source合同未改变；修复与问题范围相称，无新增重要发现。**P2关闭；最终spec compliance PASS、quality PASS。**

最终源码SHA（实际读取文件哈希，与补丁冻结值一致）：

- `services/platform/scripts/prepare_deployment.py`：`6d3c6b6d1c3dd68980c51cf5d9dca4e77c7b8450319a093013638de4e3f529c3`
- `services/platform/tests/test_deployment_preparation.py`：`bef0d76261345234316d0dfe5dc5cb5665710a02f35a5b9ae16e212ccd1529a2`

## 最终生成物与限制

最终包位于 `outputs/_run/central-production-preparation-20261023/deployment/`，替代v1作为本批最终生成物；v1保留为中间证据。已只读核对其公开 `deployment-report.json`：SHA `918b19c122b975f981ab8b37bdc2036cbda5a3b07c5388a8470b638ad75cf806`；三条已确认域名正确，52项源码清单，wheel SHA仍为 `7addda835c3f4201d63c6c1f68e8173e5e8bb41e4f68f63dc98e44551745c24b`。此次改动仅离线生成脚本与其测试，不改变wheel内服务Python／SQL／静态资源，因此无需重建wheel；原锁定依赖新venv构造证据保持原归属。

最终报告仍为 `verification=offline_only`、`production_ready=false`、`identity_migration_required=true`、`dependency_metadata_authenticated=false`。真实域名语法与52文件一致只证明该层准备完成；目标TLS主机名校验仍失败，Logto迁移、受审身份映射、原权威账本writer fencing、恢复演练、实际unit／Nginx安装验证和新地址真实登录／业务验收都不因本次PASS自动完成。最终PASS仅表示本批离线准备符合规格；生产仍未部署，证书校验没有被绕过。

root的本批验收记录现已落盘；初次读取时未存在的记录保留为时间点说明，最终链接不再以该初版状态判断。
