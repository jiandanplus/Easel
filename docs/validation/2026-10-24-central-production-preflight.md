# S5-A3：真实部署预检、Logto enum 兼容与目标安装验证

日期：2026-10-24（Asia/Shanghai）。继续在 `codex/ui-redesign` 开发，保留自定义接口和中央订阅，不合并主分支，不自动提交／推送。本批完成真实只读预检、发现的身份库类型兼容修复及隔离验证；**正式中央平台尚未切换到三个新 HTTPS 域名**。

## 本批结果

| 层级 | 实际结果 | 不能据此推导的结论 |
| --- | --- | --- |
| 目标服务器 | SSH 现有管理连接可用；核对 hostname、4 核／4 GB、Python 3.12.3、磁盘、现有服务和端口 | 未证明真实业务容量、SLA、重启恢复或监控就绪 |
| 公共 DNS | 1.1.1.1 与 8.8.8.8：三个 A 均为 `115.190.211.112`；AAAA 均 NOERROR 空答案 | 不代表站点、TLS 或登录已上线 |
| 代理／TLS | 原 OpenResty 配置检查通过；没有 accounts／console／login 三个 server_name；服务器 loopback 严格 TLS 均 hostname mismatch（verify code 62） | 外部探测是 reset／TLS 失败，没有取得 HTTP 健康响应，不把两种失败原因混为同一证据 |
| 真实中央库 | 位于服务器持久卷，001–009 SHA 匹配；2 用户、2 钱包、1 会员、1 管理员；7 个任务在观察时均 settled，资金桶与 journal 平衡 | 不是正式写入截止；统计中的 NULL state 不能证明其他连接空闲 |
| 真实 Logto 源 | 本机固定 Logto 1.45.0／PG 16.15；更新 collector 后单源只读完整指纹通过，default tenant 5 用户／4 应用，两个必需应用存在 | 没有恢复真实身份库，没有两真实库比较、proof／apply 或新 issuer 本人登录 |
| 合成实际恢复 | 新增 native enum、enum[]、未使用 enum 后，实际 dump／restore、proof、alias、中央库恢复和低权校验通过 | 不代表真实 Logto 数据或真实账本已迁移 |
| 代码回归 | 中央服务全量 **886 passed in 93.30s**，没有跳过、筛除或替换 PostgreSQL | 不代表生产已部署或商业支付／媒体／云执行已验收 |
| 目标 Linux 安装 | **成功**：目标 Python 3.12.3／x86_64 全新 venv，26 个 distribution；API／Console 构造和 56 文件逐项校验通过 | 全新 venv 构造不等于常驻服务、TLS、真实 DB／IdP 或负载验收 |

## 实际拓扑与保护范围

中央 API、Console 与真实钱包库已在 `115.190.211.112`；Logto 仍在 Mac 的专用 Docker 环境，服务器通过 SSH 反向转发访问其 `18763`。不能把浏览器里的 localhost 入口推断成账本也在本机，亦不能把现有 HTTP 预览当作正式服务器独立登录部署。

中央权威库为 `easel_workbench_preview`，schema 为 `workbench_e6467e17dc334faabcba1ad78d6c23ad`，固定 PG 16，数据卷 `easel-workbench-e6467e17dc33-db-data`。本批未改真实库、未执行 migration 010、未授予／撤销真实权限、未改账号／钱包／点数／会员或订单。模型与付款调用均为零；供应商配置仅检查路径存在标志，没有打开配置或读取／导出 Key。

三个新服务站点未安装，原 API `18874`／Console `18875` 未停；新 `19874`／`19875` 在预检时空闲。末次[只读保护回执](2026-10-24-production-preservation-receipt.json)中的行数、迁移 SHA、任务／管理员状态与资金平衡投影均与初次相同，原 issuer 保持；这不是全库字节不变或写入截止的证明。未变更 DNS、申请证书、reload 原代理或更改 New API 服务。资源快照约 2.73 GB MemAvailable、27.12 GB 磁盘可用、无 swap；这只是时点观察。

## 真实兼容缺口与修复

真实 Logto `public` schema 存在 8 个原生 enum，被 9 列使用；另有 153 条 RLS policy、85 个 trigger 与 10 个 PL/pgSQL 函数。原 collector 拒绝全部自定义类型，不能直接用于此真实源库。没有通过删除 enum、跳过 RLS 或忽略权限来通过。

`identity_restore.py` 现在指纹化 schema 内原生 enum 及其 PostgreSQL 自动生成数组，覆盖名称、owner、ACL、真实数组关系、全部标签的相对次序，以及未使用／空 enum。逻辑备份恢复可能重分配 OID、枚举排序浮点值，比较不依赖这些物理值。domain、range、composite、外部 schema enum 和伪造数组关系仍拒绝。未改服务路由、CLI 合同或 migration 001–010。

修复后真实源只读 collector 耗时 0.607 秒，用户／应用与 schema、数据、上下文只输出摘要和计数，不输出 subject 或原始行。该次单源证据的 `proof_created`、`target_restored_or_compared`、`identity_mapping_applied`、`production_ready` 均为 false。公开证据见[源结构摘要](2026-10-24-logto-source-metadata-receipt.json)和[真实单源回执](2026-10-24-logto-source-compatibility-receipt.json)。

## 验证记录

| 检查 | 本批实际结果 |
| --- | --- |
| enum collector PostgreSQL 定向测试 | 60 passed in 8.86s，含新增 18 项；先观察不支持类型的失败，再实现修复 |
| 合成恢复 harness 单元测试 | 44 passed in 0.12s |
| Linux 隔离验证器纯测试 | v1 20 passed in 0.03s；移除 HOME 重定义；v2 超时进程组修复后 root fresh 25 passed in 0.03s |
| 中央服务全量 PostgreSQL 回归 | 886 passed in 93.30s；包括上述服务测试及批量候选／锁测试 |
| 新 release 公开配置的部署准备测试 | 79 passed in 0.19s |
| 含 enum 的实际合成恢复 | `success=true`，全表数据／schema／sequence 相等、真实低权守卫通过、私有读取拒绝 |

这些是不同命令／时点的结果，存在子集重叠，不相加成新的总数。完整回归命令：

```bash
rtk proxy env EASEL_CENTRAL_TEST_DSN_FILE=/private/tmp/easel-console-20261023/test-dsn-private.json services/platform/.venv/bin/python -m pytest services/platform/tests -q --tb=short
```

专用测试库只用于随机独占对象，真实源与真实中央库均未用于可写测试。合成实际恢复使用固定专用测试容器，恢复 51 张中央表、10 个 migration；保持 1 个 settled、1 个 pending 与正预留、会员、原管理员和禁用管理员状态。记录的模型／付款调用均为 0，脚本仅清理本次随机库／角色，归档与私有连接文件仍保留在受控临时目录。完整[合成恢复回执](2026-10-24-enum-restore-receipt.json)独立保存。本轮实际 PostgreSQL 测试完成后，仅停止该专用测试容器，保留容器与私有备份文件；其 tmpfs 测试数据库随停机释放，不涉及真实 Logto／其他 Docker 应用。

## 目标 Linux 独立安装

使用公开 wheel 和与 `uv.lock` 对齐的带哈希依赖，向固定目标主机独立随机 preflight 目录传输；不传入源配置、DSN、token 或模型密钥。在新 venv 安装，原预览 venv 仅提供 pip 安装器，未作为安装目标；未对旧 venv 做逐字节不变性审计。安装过程使用最小环境、禁用缓存和外部 pip 配置，只允许锁定的二进制依赖。

构造探针使用合成 HTTPS 配置、禁用模型与付款；校验安装 payload、SQL／静态文件与必要命令，构造 API／Console，禁止 socket API，不启动 ASGI lifespan、HTTP listener 或数据库连接。无生产服务启动，`production_ready` 保持 false。

第一次执行因公开 PyPI 索引／下载较慢，在安装阶段达到 600 秒上限，返回 `TimeoutExpired`，未进入构造探针，未记为成功。两次只读诊断确认下载仍有进展且未见固定 TLS／网络／分发错误；超时后两个已定位 pip PID 均不存在，没有发送清理信号。这些诊断不能替代安装成功。v2 增加仅限本次新 session／PGID 的超时处理，pip 安装上限改为 1200 秒、外层 1500 秒；TERM／等待／KILL 仅针对本次组，不能据此保证清除主动逃逸该组的任意后代。修复经[独立增量审查](2026-10-24-linux-install-review.md)与 root fresh 25 项纯测试通过；原 v1 文件和失败回执保留。v2 实际执行最终成功：目标 `115.190.211.112` 的 `/opt/easel-central-preflight/20261024-921f7caabe29fff26b2ba397af17db6d` 中安装 26 个 distribution，API／Console 构造、10 条 SQL 与 56 个源 payload 校验通过，真实数据库／模型／付款调用为 0，没有启动服务或 lifespan。完整[目标安装回执](2026-10-24-linux-install-receipt.json)保留逐文件 SHA 和安装版本；`existing_venv_modified=false` 表示旧 venv 未作为安装目标，不代表对旧目录做过逐字节审计。

## 发布物与独立审查

本批 wheel：`easel_central_platform-0.1.0-py3-none-any.whl`，SHA256：

```text
ec2e5577c79cf39f27f3962e462025c3f37ab27f2979d076e1e723c4c65b4cc9
```

源与 wheel 严格匹配 56 个 Python／SQL／静态文件（42／10／4），既不漏文件也不接受多余 payload。`uv.lock` SHA256 `eb00eae7b6966760595376c65da0571d40406dc2d2a3019c1247f575dfa44f2a`；运行锁依赖清单 SHA256 `cc4ed5666cf61eb591c99ef7951fda4018e0c96e6d6d87e9ca98978c19958c84`。Console 本轮没有另做前端构建，现有构建产物通过当前源／wheel 清单核对。

部署文件使用新 release `/opt/easel-central/releases/production-preflight-20261024`，保留旧 S5-A1／S5-A2 ZIP 内容。生成器仍声明 offline_only，不能把本批独立 DNS、真实只读源检查或 Linux 构造证据手填成“已正式上线”。

[enum 独立审查](2026-10-24-logto-enum-review.md)、[切换清单审查](2026-10-24-cutover-review.md)和[报告证据审查](2026-10-24-preflight-evidence-review.md)分别通过；Linux 验证器经根代理独立审查并补充回归。审查不是正式环境执行证明。

## 尚未执行的正式门槛

下一阶段使用[本次实际切换清单](../../services/platform/deploy/CUTOVER_20261024.md)：先补齐可执行且可核验的旧 writer 隔离／恢复步骤、同版本 Logto 持久目标与私有角色配置，准备回调并纳入最终备份，完成真实库隔离恢复；再在写入截止内应用 migration 010 与身份 proof／alias，安装新服务与现有 OpenResty 的三个站点／有效证书，最后由本人登录核对原钱包与管理员。

回调更新与 Logto 启动都会改变身份库数据，必须在静态比较顺序中明确安排。旧新锁协议不得混跑；源／目标都静止后才能比较，只有一套账本开放消费。不能用快照回滚后继续花旧余额或清空 pending。正式商户支付、商业费率、订阅媒体、云执行、多产品实接、容量与告警另做有界验收。

[公开 DNS／TLS 回执](2026-10-24-public-endpoints-receipt.json)保留普通解析和固定目标的独立结果。本批没有把新站点开放为生产入口，也未移除原预览或 Mac 依赖。

## 交付与收尾

[本批安全汇总回执](2026-10-24-central-production-preflight-receipt.json)记录测试命令对应结果、真实源只读与合成恢复的分层证据、首次安装超时与最终成功、测试容器停止、代码与锁文件哈希，以及未执行的正式门槛。当前 HEAD 与 main SHA 保持，migration 001–010 与 S5-A2 旧 wheel 逐字节一致；本批修改 collector、验证 fixture／工具、公开部署目录配置与文档，没有改模型执行或付款业务逻辑。

[公开交付 ZIP](../../outputs/_run/central-production-preflight-20261024.zip)包含新 wheel、锁文件、公开配置与模板、手册、审查、安全回执及受限预检工具，明确逐文件 allowlist、SHA256 manifest、ZIP CRC 与条目校验；[外部 ZIP 回执](2026-10-24-delivery-receipt.json)保存归档 SHA／字节数。归档不含真实 env、DSN／连接文件、用户行、备份 dump、日志或密钥；不是完整源码 checkout，也不是已启动的正式服务安装包。历史文档引用需结合完整仓库，旧 S5-A1／S5-A2 ZIP 保持原 SHA。
