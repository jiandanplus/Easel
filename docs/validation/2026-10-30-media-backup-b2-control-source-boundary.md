# B2 完整业务源与维护审计边界验证进展

本轮继续已批准的本地开发／隔离验证；当前分支 codex/ui-redesign，不部署生产、不读取真实账号／密钥／业务数据、不调用真实模型、不自动提交或合并。原17868 v35工作台不变。此报告为组件进展，不是完整B2交付。

## 实现

两份业务custom dump明确包含public全部DDL与全部数据；同一实际导出snapshot的schema-only/COPY对照仍保留，public所有序列before=dump=after和最终观察继续严格一致，文本credit_entries变化仍拒绝。

完整维护控制历史以easel-maintenance-snapshot-v1进入中央source_facts并经现有bundle/v2认证：同snapshot控制DDL、完整目录／owner／ACL的canonical SHA、七张原始控制表全部数据（包括inactive worker历史）、实际控制序列定义和值。活动worker投影必须能从原始workers/attestations重建。它是历史记录，不自动恢复live token/OPEN/sequence，不增加B1文件。

后置检查绑定原owner/window/epoch、静态控制表、完整审计前缀、所有worker/domain及零permit，只允许固定VERIFYING审计尾项和恰好一步sequence推进。控制采样本身要求同原window/epoch的严格DRAINING→CAPTURING阶段链，最后真实CAPTURING审计ID必须恰等当前sequence；未知空转或额外动作不能成为基线。历史内部旧gap保留。

原R3设计保持24487字节/SHA f9614a317705fd25663bd942e847759910a8d1c4fcf9c9a70e9107b8d8603cff，实施澄清在完整执行计划末尾单独记录，没有改写历史设计。

## 证据与边界

- 原合法VERIFYING转换实际RED（/private/tmp/easel-b2-control-boundary-20261030-red.log），修复后28纯组件＋实际audit/完整worker历史两例30pass7.94s。
- 扩展实际PG捕获矩阵阶段32pass59.99s，包含配置、role/密码变更、原keeper重启、正确/错误snapshot、业务sequence推进、未知control sequence/审计/静态表变化、取消及固定原预算。此为阶段快照；R后续控制SQL还在收尾，最终全树会重跑。
- 新官方Logto1.45.0纯合成fixture actual seed0／1.451s／79表8enum，新的catalog与实际snapshot capture两项2pass3.60s，旧连接失败保留不覆盖。
- 新控制原始行loader使用实际named ServerCursor、每次一行、单行和总8MiB预算。8×2MiB真实合成audit行先1RED，收紧后与当时28项pure组合29pass0.58s。
- 独立复审实际发现P1：EX后、初始采样前的unknown nextval被旧<=吸收。原探针正常PASS＋负例RED7.40s已保留；root新增3纯＋2实际反例先5FAIL28PASS，修复后31纯＋4实际35PASS11.75s。独立fixed-pin复跑已关闭原normal/nextval，并验证历史内部gap保留、其他window/epoch尾项拒绝；最终附加探针收尾仍以独立报告为准。
- Root新test_media_backup_b2_integrated.py无SQL/rawfacts/coverage/OSidentity替身，包含001–016/001–020、空源/实际模拟图像（受限runtime角色、真实文件和合成账本）及独立进程读取/私有B1文件恢复。当前empty16和nonempty16均真实停在缺失的fixed coverage registry门，不能算通过，不做旁路。

控制新模块SHA62563040b194464b044bd7f0f5642496869e08da7fa605c724e1e3147efa6965；其余精确输入见outputs/_run/b2-development-20261030/control-source-boundary-progress.safe.json。此前35/32/30/29包含重叠，不能累加为总测试数。

## 仍需完成

T4/T5媒体全部入口／nonce与GC收束和固定coverage、旧业务回归迁移、当前最终SQL下完整producer矩阵、无替身wholepipeline与非空源、Linux／资源／实际root私有边界、f50的001–016受限guard delta、最终installed wheel和独立T14。B3生产备份／定时器／真实旧密文／数据库恢复仍另门。


## 原P1最终独立复核闭合

新冻结6文件下独立r4实际6PASS／13.11s：normal、窗口前历史gap保留、EX后未知nextval拒绝、foreign-window tail拒绝、foreign-epoch tail拒绝、同window/epoch错误details的CAPTURING尾项拒绝。原r2 RED和r3在capture前被immutable-trigger拒绝的setup失败全部保留。当前依赖在6例间变化，正常例安装9659ac…，其余5例安装3b5de023b5bde483ee71c40bcc54dcf5d0c6bc25b39e00dbed4db5cdfad91957；精确逐例回执见r4-dependencies.safe.json。该结论关闭原P1，不替代最终控制SQL全树／wholepipeline／Linux验收。安全报告、XML、精确输入和探针已归档 outputs/_run/b2-development-20261030/control-boundary-independent/。
