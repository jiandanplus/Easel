# 待核算运营工作台 R1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans task-by-task. Track steps with checkboxes.

**Goal:** 在私有 Console 提供待核算队列、服务端详情、处理进度和可查询的幂等审计，不改变资金。

**Architecture:** Console-only元数据服务复用现有认证和审计，一张当前工单表，金融记录按现有安全列读取。后台进度与财务状态独立；无供应商调用或结算命令。

**Tech Stack:** 现有 Python/psycopg/PostgreSQL 与 React/TypeScript/Vite；不新增依赖。

**阶段状态：** R1授权范围已验收：实现、本地真实PG/前端/合成浏览器验证、生产切换、本人管理员队列/详情只读UI验收及浏览器后的独立数据库财务复核均已完成；最终安全汇总、测试服务cleanup和交付记录已完成。R2及下述外部限制保持。

## Global Constraints

- 用户已批准[设计](../specs/2026-10-26-reconciliation-workbench-design.md)，当前 `codex/ui-redesign`，不合并主分支、不覆盖既有261项脏文件、不自动提交或推送。
- 原9728微点、未知actual与原hold保持；禁止调用finish/settle/provider，禁止新模型消费或发点。
- 管理GET不写库；处理POST只写新工单元数据和原审计；不扩展财务及原私密列权限。
- 金额字符串、null未知、BigInt显示；未知POST结果只GET原键，不自动重新POST。
- 所有shell使用rtk；测试库仅字面loopback和easel_s1_test前缀；生产数据不能作为合成写入测试对象。

## 固定跨任务契约

状态枚举：`unreviewed`仅投影；可写为`investigating/awaiting_evidence/disputed`。`financial_status=pending|finished|all`默认pending，finished仅真实终态且有工单历史，all为两者合并；`review_state=all|unreviewed|investigating|awaiting_evidence|disputed`默认all，另有funding_mode=all|test|live默认all及可选product_id、q、limit、offset。

列表每行：`id,model,product_id,billing_account_id,status,amount,actual,funding_mode,created_at,updated_at,rate_version,review_state,review_revision`。列表协议沿用`items/limit/offset/has_more`。

详情：`{job:row,review:null|{state,revision,actor_id,reason,snapshot_hash,updated_at},observed_at,snapshot_hash,usage:{present,input_tokens,output_tokens,diagnostic},history:{items,has_more}}`。详情job额外含安全固定`rate={version,input:{numerator,denominator},output:{numerator,denominator}}`。history项仅含`id,actor_id,reason,created_at,state,revision,snapshot_hash`，最多50条。diagnostic为固定安全枚举，不含raw reason。

POST `/api/reconciliation-cases/{job_id}/reviews`，Header Idempotency-Key；body严格`{state,reason,expected_revision,snapshot_hash}`。结果`{operation_id,job_id,review:{state,revision,actor_id,reason,snapshot_hash,updated_at}}`。

GET同路径的`review-operations`，Header原Idempotency-Key；返回`{found:boolean,result:null|POST结果}`，按当前actor/job/原键匹配，未找到仍是未知，不作为换键补提依据。

新表`console_reconciliation_cases`：`job_id`为text主键/FK现有text型执行job，API校验标准UUID字符串，state文本enum，revision正bigint，actor_id FK，reason 1–500无控制字符，snapshot_hash小写sha256，created_at/updated_at，last_operation_id唯一且延迟FK console_operations。仅当前state/revision/actor/reason/hash/updated_at/last_operation_id允许UPDATE，不改job_id/created_at，不允许删行；数据库延迟约束绑定最后操作结果中的job/state/revision/actor/reason/hash，更新revision必须递增1。

审计action=`execution.review`，target_type=`execution`；新迁移011只追加动作枚举和工单表。复用console_operations和console_audit_events的原子绑定，不增重复history表。

## Task 1：权限和迁移（root）

Files：`migrations/011_console_reconciliation.sql`、`console_permissions.py`、`console_auth.py`、`tests/test_console_reconciliation_permissions.py`。

- [x] 先写真实PG红测试：新表首次不存在；新动作不能审计；Console没有review能力。示例断言：

```python
assert 'execution:review' in ROLES['admin']
assert 'execution:review' not in ROLES['viewer']
assert await allowed_columns(console_role, 'execution_jobs') == prior_safe_columns
```

- [x] 运行`rtk proxy uv run --project services/platform --group test pytest services/platform/tests/test_console_reconciliation_permissions.py -q`，确认因新能力缺失而红。
- [x] 最小迁移/按列授权；给admin/operator增加execution:review，viewer仍只读；不授予金融UPDATE或原始receipt列。
- [x] 同命令转绿；现有permissions、auth、migration测试回归。

## Task 2：服务、路由与业务测试（backend agent）

Files：新增`console_reconciliation.py`、修改`console_app.py`、新增`tests/test_console_reconciliation.py`和必要web测试；不编辑Task1文件。

- [x] 写红测试覆盖空队列不创建行、详情null、快照一致性、CAS、幂等、原键GET、权限/CSRF/Origin、原财务行逐字节不变。
- [x] 实现`ConsoleReconciliation(db,auth)`：`list(principal,...)`、`detail(principal,job_id)`、`review(principal,job_id,body,key)`、`operation(principal,job_id,key)`，严格使用以上契约；GET用READ ONLY事务。
- [x] command验证ACL后actor/key advisory lock，先返回已提交同键结果；首次case/job advisory lock及case revision CAS，审计与元数据同事务；业务job不加UPDATE锁。
- [x] 路由使用既有same-origin/CSRF边界，严格字段集合、UUID、body和参数限制；异常只返回固定码。
- [x] 用真PG转绿，不跳过；不mock金融不变量。

## Task 3：Console前端（frontend agent）

Files：`frontend/src/reconciliation.ts`、`ReconciliationPage.tsx`、必要`core.ts/App.tsx/style`及前端tests。只修改frontend目录。

- [x] 前端测试覆盖筛选固定路径、详情GET、未知费用不变0、viewer无写入按钮，以及双击/未知响应不发第二个POST。
- [x] 在追踪组增加“待核算处理”，独立详情；三种进度、原因1–500、revision与快照绑定。复用当前主题/组件；不重建Pi客户端。
- [x] 独立review operation保留同一键/内容，unknown后只显示“查询原操作”；found=false仍未知，found=true恢复原结果；切换页面/关闭详情不自动创建新操作。整页reload后的原键未持久恢复，不能据此推断未提交或自动补POST。
- [x] Console前端最终38 passed、0 failed、0 skipped；`tsc -b`与Vite build成功。

## Task 4：集成及独立审查（root＋review agent）

- [x] 对照设计审查权限、投影、并发、网络未知结果；权限guard、最小授权与资金不变量核对通过。
- [x] 完整中央真实PG最终1030 passed；Console前端/类型/构建通过，合成角色的财务权限与资金不变量保持。
- [x] 隔离本地Console与合成账本的浏览器验证：一次POST后模拟响应丢失，仅以原key GET恢复已提交结果；没有为此发第二个POST，也不将该模拟写成生产本人验收或整页reload持久恢复。
- [x] 记录独立测试数与源码/资产绑定，不累计重叠轮次；wheel独立SHA256与60个ZIP条目CRC通过。
- [x] 仅停止本轮合成预览及专用PG容器，验证自有隔离schema/role已清理；保留容器、真实后台私有隧道与用户工作台。

## Task 5：可用交付与文档（root）

- [x] 阶段验收报告和[最终安全汇总](../../validation/2026-10-26-reconciliation-workbench-safe.json)已整理，区分代码/真PG/合成浏览器/生产切换及本人UI证据。
- [x] 154先加密备份、维护准入并排空，再追加011迁移与最小role授权，切独立新release；原财务权威表与配置保持，原NGINX及模型admission恢复，未回滚真实钱包或重开旧writer。
- [x] 生产本人管理员有效会话只读验证真实pending队列及两笔独立详情；未填表、未点击登记，不在旧任务上创建合成review事件。
- [x] root完成最终安全汇总、运行手册与最新版本绑定，核对branch/diff/check；不合并main。

## 2026年10月26日阶段证据

开发与本地验证已完成。早期Task2资金模式补漏后的77项相关PG/HTTP回归保持为独立阶段记录；最终中央真实PG轮次为1030 passed，耗时114.88秒，前后源码manifest均为`5677892152f5784b30cb5b61689a108aa2bc52bdc233a0dc94d4c5707af26d4b`。Console前端最终轮次为38 passed、0 failed、0 skipped，`tsc -b`与Vite build成功；不同测试轮次不相加。

交付wheel SHA256为`94ca79973b12cab2b552edf426473ad127785d7118c20e7b817589c67f9b7857`，57个payload与当前source/dist绑定，独立复核60个ZIP条目CRC通过；旧迁移与financial execution模块逐字节保持。合成浏览器的一次POST加原key GET响应丢失场景已通过；它验证本轮原操作查询协议，不证明生产本人UI或整页reload后的原key持久恢复。

生产已切到`/srv/easel/releases/20261026-reconciliation-r1-v1`，迁移011已应用，SHA256为`ad3219dcfe496137a32493bd97e69519799f6cc221494ac54e8209bf7efef7d5`。最小Console按列权限及guard通过，四个服务active；配置哈希不变，模型admission与原NGINX配置已恢复。部署阶段生产review写入为0、模型调用为0。

切换前后全钱包八张财务权威表的哈希均为`3fcd787bf72e79a76d6669255e8e15feb2eda2a2ae68312e73d385503c248e79`；范围仅为下表，不包含`execution_results`，不能表述为九表或结果正文审计。

| 表 | 行数 |
| --- | ---: |
| credit_lots | 1 |
| credit_reservations | 27 |
| credit_holds | 27 |
| credit_journals | 53 |
| credit_entries | 131 |
| execution_invocations | 11 |
| execution_jobs | 27 |
| execution_receipts | 25 |

切换验收快照的available为9954247、held为19456、spent为26297微点，pending为2笔；原9728微点旧任务仍为`actual=null`，没有因R1工单功能释放预留或结算。此处保留2026-10-25设计历史基线与本次生产快照的时间差异，不能将两者直接解释为本批发生消费。

| 服务器加密备份 | SHA256 | 字节 | 文件数 | 已验范围 |
| --- | --- | ---: | ---: | --- |
| 切换前 | `7ce95a0240db601513e1484b4dff479bc4d03c16b9a1f2433b8625862de244e1` | 809802 | 13 | 0600、self-verified |
| 切换后 | `5aef207f6fddeb9c4ce1e7974d5dda83aa7d2c7c79eec878d147d8ee0d495907` | 830282 | 13 | 0600、self-verified |

两份备份均未进行fresh restore或offsite验收。

18885管理员隧道接口返回200。最新新建IAB tab 13打开该入口成功，点击统一登录约3.8秒后用户名、密码和登录按钮正常显示，截图为`docs/validation/assets/reconciliation-r1-20261026/admin-login-recovered.jpg`。此前白屏未复现，本轮未修改认证、代理或服务器配置，不能宣称根因已修复。

随后用户完成一次登录，实际进入待审批申请页。只读比对确认本次issuer为当前issuer，但subject在任意issuer下匹配admin为0、匹配alias为0；原active admin仍为1、当前issuer原管理员alias为1，近三天原管理员session为3。当时使用的是不同于原独立管理员的账号，未授予新权限；不将该结果归因为权限丢失或映射损坏。这次进入申请页不作为本人管理员验收，历史结果保留。

后续重新打开18885时，已有有效本人管理员会话，AX显示“管理员”“独立管理会话”及“已验证管理会话”。实际只读核对队列两笔及两笔独立详情：原旧job `690ef707…` 与另一job `33344a42…` 均为pending，每笔原预留0.009728点、actual未知、revision 0、无中央用量记录、tokens未知且暂无处理历史。全程未填表、未点击登记；验收后tab 15保留队列。截图为[生产队列](../../validation/assets/reconciliation-r1-20261026/production-admin-queue.jpg)、[原旧pending详情](../../validation/assets/reconciliation-r1-20261026/production-old-pending-detail.jpg)和[另一pending详情](../../validation/assets/reconciliation-r1-20261026/production-new-pending-detail.jpg)。这是最终安全汇总初始落盘后的本人只读UI追加记录。

随后root新增独立只读数据库复核，原始回执为`/private/tmp/easel-r1-20261026/post-browser-verification-safe.json`，已并入安全汇总`production.post_browser_verification`。本次after-UI八表全钱包hash仍为`3fcd787bf72e79a76d6669255e8e15feb2eda2a2ae68312e73d385503c248e79`，各表计数及available 9954247/held 19456/spent 26297均与切换快照相同，pending为2，原9728微点actual仍null；cases、operations、audit均为0，财务及journal平衡，四服务active，复核模型调用为0。该独立新回执证明本轮页面读取未登记事件或改变资金，不以旧切换回执替代。

本阶段不提供R2的供应商凭据采集、精确attempt绑定或金融结算命令；不能清零未知费用、补扣、释放预留或恢复旧结果。最终安全汇总`docs/validation/2026-10-26-reconciliation-workbench-safe.json`已生成，65个测试时源码/资产哈希与wheel再次校验一致，原自定义配置哈希不变。自有合成schema/role已清理，专用测试容器已停止并保留，真实后台私有隧道与工作台保持运行。不合并main。

## 当前执行状态

- [x] 用户批准R1，接口/权限/不改账设计已固定。
- [x] Task1迁移和权限；真PG红绿及回归完成，011与最小授权已生产应用。
- [x] Task2后端；早期77项相关回归记录保留，最终中央1030项真实PG全绿。
- [x] Task3前端；38项最终测试与类型/构建通过。
- [x] Task4本地集成、合成浏览器及wheel独立复核；生产切换已完成。
- [x] 本轮测试服务cleanup与最终安全汇总/交付文档收口。
- [x] 原独立管理员有效会话的R1队列与两笔详情只读UI验收；不同账号进入申请页的历史记录保留，不新增权限、不登记处理事件。
- [x] 本人浏览器读取后的独立数据库财务复核与安全回执归档；八表全钱包hash及余额保持，工单/操作/审计为0。
