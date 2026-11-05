# R2：可信结算恢复与运营承担未知费用

用户已要求继续开发并修复待核算占满导致无法对话的问题。本批沿用 `codex/ui-redesign`，不合并主分支，保留自定义与订阅模式，不自动提交或推送。

## 根因与范围

2026-10-26 实查三笔旧 GLM 请求均已派发并进入 pending：一笔 upstream_unavailable、两笔 upstream_error；无中央 receipt/result，也没有持久化精确上游关联。原预留每笔 9728 微点、总计 29184 微点，actual 未知。不能从近似 New API 日志或本地 SDK 估算制造可信用量。

实现两个独立路径。可信终态先单独持久化，资金提交暂时失败后可安全恢复；仍未知的历史费用只能通过明确的运营承担决策结束客户预留，不能称为供应商零费用或已按真实用量核清。代码与隔离验证可直接推进；真实三笔承担费用操作须在生成精确 plan 后获得对应经营决策确认。

## 可信证据路径

新 attempt 在出站前保存 job/attempt/binding 和实际 provider payload 的 SHA256、上游模型及本机生成的 request ID。收到上游响应时记录经限长校验的 request/response ID。关联记录与终态证据不保存 prompts、headers、Key 或回复正文。

仅在原协议严格校验 completed/incomplete、response id、非负整数 input/output usage 且无 error 后，先独立提交不可变终态证据，再执行原冻结费率结算。证据精确绑定原 attempt/binding，不能由浏览器或 SDK 自报。后台恢复只能对已保存证据做幂等费用结算，不重派模型。

有证据而没有原回复时，补结算不创建结果缓存，result_state 明确 not_available。预算、输入/输出上限、账号/产品/资金模式及原锁顺序保持；超限仍 pending 并阻断异常 binding。无证据不自动释放。普通网页断流继续沿用原后台执行，native stop/上游中断没有可信终态时继续保留未知费用。

## 平台承担路径

新增明确终态 `waived`，仅允许 pending 转入；job/reservation 的 actual 保持 NULL，独立不可变处置记录注明 platform_absorbed、用户收费 0、供应商费用未知、原预留、原 attempt/binding/scope 摘要、操作者、原因及批准依据。不得伪造 usage/receipt/回复。

受信任服务器 CLI 使用独立 `execution:writeoff` 能力，默认 plan-only，apply 必须携带已审 plan hash 和原幂等键。Console 保持财务只读，原 execution:review/publish 不自动得到新能力。计划绑定精确 job 集合及其原快照；apply 在原钱包→invocation→job→reservation 锁顺序下复核，全部原子提交。

账本仍通过平衡 release journal 从 held 返回 available 或 expired；额度过期不复活，spent 不变。新增状态必须纳入不可变终态守卫、账本/执行 deferred reconciliation；waived 的客户支出按独立处置记录的 0 校验，不能让 SQL NULL 比较绕过金额一致性。处置记录必须与 waiver transition 同一事务匹配，失败回滚；重复原操作不重复释放。晚到 finish 不覆盖 waived 或补扣用户。

本次真实三笔释放是独立需确认的运营决策；如未批准，部署代码也不会自动处理旧记录或提高待核算上限。

## 查询与用户体验

中央、Console 和桌面按新终态显示“平台承担费用，用户不扣点；供应商用量未知”，保持费用未知与客户无扣点分开。原历史记录与费率留存，不把 waived 当成功模型回复。R1 工单进度和财务状态仍分开，允许只读查看已由平台承担的记录。

## 验证与发布

先用真实隔离 PostgreSQL 验证所有账本平衡、过期 lot、权限、幂等、CAS、并发、审计回滚、无证据拒绝、无假回复、超限和 late finish。协议恢复测试证明资金失败后只重试结算，供应商 POST 总次数为一。再跑中央及桌面相关回归、构建与权限启动检查。

发布前新加密备份与配置/全表快照、检查真实库没有在途任务、门禁和原 release 保留。迁移只追加 012/013，不改历史迁移。启用新服务后先只读核对原三笔与所有原始资金事实仍保持，之后才对已审计划执行明确获批的处理。真实验证不使用后台伪造用户会话，原账号需用户亲自登录。
