# S5-A4 真实私有备份与隔离恢复

本记录属于当前 `codex/ui-redesign` 的迁移准备。生产目标是 `115.190.211.112`，保留自定义接口与订阅两种模式，不合并主分支。时间以实际回执为准；以下中央库验收于 2026-10-24 02:33:49 Asia/Shanghai 完成。

**后续状态：** 用户已明确授权敏感账号归档传输，本轮实际完成服务器私有传输与隔离恢复；数据与权限一致，完整 schema 仅剩两个索引 predicate 的表示差异尚未完成结构证明。下文“等待授权／未传输”保留早先时点状态，最新结果以[后续账号库验证记录](2026-10-24-logto-transfer-restore.md)为准。

## 已完成的中央库真实恢复

根代理在独立复审通过后实际执行冻结工具 `b7a0cdcc89eb060a38ca070803f27f2b93cc2cffc439a0e1eb7012e6491e960f`。三项审查发现已修复：源／目标全局角色和数据库元数据重新采集、实际卷与容器资源回读、超时的结果未知与保留现场处理。根代理及独立审查各重跑 14 项纯测试通过；这些纯测试与下述真实恢复分别记账。

真实源为原服务器 `easel-workbench-e6467e17dc33-db` 内的权威业务 schema。源 holding 只读事务持续到 `pg_dump --snapshot` 完成，数据指纹与归档共享同一 PostgreSQL 快照。真实归档 308224 bytes，SHA256 为 `57e4bb81b40ff9ad1b5868f99fb807f1b682ec79a537c0d681af55591e7f6324`。归档与两份原 operator 配置仅保存在服务器 0700 私有目录，文件 0600，不纳入源码、公开包或本机副本。

目标为全新独立 PostgreSQL，nonce `4feac8d463f2948a07227384b9093081`，仅监听服务器 loopback 19976，固定镜像、独立持久卷、384 MiB／0.75 CPU／128 PIDs、无自动重启。只启动了恢复用 PostgreSQL，没有启动 API／Console／Logto／消费 worker。

| 核验 | 实际结果 |
| --- | --- |
| 业务表 | 46 张，源与恢复的逐表行数一致 |
| 全数据指纹 | `20f69b168aa210c8ac451b2a0744ddc0edf46baff90ec9db01a60bf3a2d3837c`，相等 |
| 结构指纹 | `c950d691aa34d79a691f07d0c9fe43602d4ca86f66f0717458e52917fcfff216`，相等 |
| 序列指纹 | `ce45eb0b40510336bdc671ef3d968f39c05076e11b4cc1bc72f3a32e23bc376d`，相等 |
| owner／ACL 规范化指纹 | `d5b69ee133315ea8044fa7a788c0481cdbedec580755da8fa1291e7f84a443f9`，相等 |
| 批准的 3 个业务角色、membership、数据库 owner／locale／ACL／扩展 | 备份时重新采集，并与恢复目标一致 |
| 实际低权限连接 | API runtime 与 Console runtime 均成功只读读取 2 个钱包 |
| 资金语义 | credit lots 恒等式成立，credit journal 分录合计平衡 |
| 连续性对象 | 2 用户、2 租户、2 钱包、1 会员、1 active admin，7 个任务全部 settled |

安全回执保存在本次私有 staging 的 `central/restore-safe-4feac8d463f2948a07227384b9093081.json`。完整连接配置、真实归档与用户行不公开。

02:41:17 Asia/Shanghai 又独立核验实际落盘归档哈希、备份和 operator 文件的私有权限、原 API wrapper／API／Console PID 与启动时间、原权威数据库仍运行，以及七份旧代理文件 SHA 均未变，`nginx -t` 通过。随后只停止已验证 nonce／镜像／唯一卷的本次隔离恢复 PostgreSQL 释放内存，保留其容器、持久卷和所有备份。未停止任何原服务、未删除对象。

可公开的实际回执为 [中央恢复](2026-10-24-central-backup-restore-safe.json)及[恢复后保留与原服务复核](2026-10-24-central-retention-postconditions-safe.json)。

## 账号库

账号库工具五项 P2 修复已通过独立复审，作者、根代理与独立审查各实际重跑 18 项纯 fixture 测试通过。冻结主工具 SHA256 为 `e68695987276e4832a9f5fd51424a2446edb90122be4278082709a329ef80aa1`，只读权限 verifier 为 `caa47297eedfeb08f158a5cd487472a1bf4e592347fcc94294f433498cb00261`。超时与中断有操作 intent／phase journal／结果未知语义；传输与恢复绑定四文件的完整哈希链；目标资源、内部网络与唯一卷必须回读；排除无关 `central_runtime` 有依赖证据和显式许可；两个租户角色的口令仅在目标数据库内部恢复，不输出动态 SQL。

根代理签发本地 backup 阶段许可后，已实际生成真实账号库归档 399811 bytes，SHA256 `40d20ed3f7181e58272058e23d566a2350ed341df60d1313cbdcfbc8d9a588fc`。角色元数据不含角色密码；归档、角色元数据、权限 inventory、manifest 四文件均 0600，所在目录 0700。备份工具没有写入源库，原 Logto 持续运行；活跃源不视为已冻结。可公开的[账号库备份回执](2026-10-24-logto-backup-safe.json)只含大小、哈希和状态。

下一步 SSH 传输的命令被自动审批拒绝，命令未执行。审查要求用户具体授权把真实账号归档中包含的密码哈希、客户端密钥与租户数据库凭据，传到指定的 `115.190.211.112` 私有目录。已发出载荷／目的地／保管范围的明确确认，当前等待回复，不绕过此拒绝。**尚未传输账号库、尚未创建身份恢复目标，尚未实际恢复／权限与全数据核对。** 不以本机备份、只读 inventory 或合成测试代替这部分结果。

## 公网阻断与生产边界

三个新域名的公网 HTTP 实际返回火山云 Suzaku 的 webblock 重定向，严格 HTTPS 在握手阶段复位；原服务器 loopback 请求与公网表现不同。用户说明 `drawbookai.cn` 原在阿里云备案、当前服务器在火山引擎，应在火山办理根域名的接入备案，保留仍使用的原接入记录。官方流程见[接入备案](https://docs.volcengine.com/docs/Record/Accessfilingprocess?lang=zh)与[备案基本概念常见问题](https://docs.volcengine.com/docs/Record/BasicConceptsofFilingFAQ?lang=zh)。具体控制台拦截状态尚待核对，不能仅凭通用拦截页确定唯一原因。

操作顺序：火山控制台 → 备案 → 我的备案 → 开始备案，按已有备案的同一主体填写，域名填根域名 `drawbookai.cn`，资源选择 `115.190.211.112` 对应服务器，按页面完成材料、核验与工信部短信流程，等待管局审批。域名注册商与 DNS 服务商可保留在阿里云；原阿里云接入继续使用时保留。根域名接入通过后再验证 login／accounts／console 三个子域名的拦截状态和严格 HTTPS。接入备案未通过前，改解析、换证书或重启应用均不能视为该门槛已解除。

原服务持续运行；本阶段未停止 writer、未撤销会话、未变更原角色、未迁移 010、未签发证书或重载代理，未生成／应用最终身份 continuity proof。真实模型调用 0，付款调用 0，供应商配置读取 0。隔离恢复通过不代表公网正式上线或商业支付验收通过。
