# 待核算与执行并发分离执行计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

> 当前分支 `codex/ui-redesign`。用户已批准[方案A](../specs/2026-10-25-pending-concurrency-proposal.md)，不合并main，保留自定义接口与原未知费用。

状态：方案A部署、v10与本轮真实工具/扣点及只读恢复已验；本阶段计划记录、[最终安全回执](../../validation/2026-10-25-execution-stability-safe.json)与本轮测试资源cleanup已完成。

**Goal:** 原体验并发1仍有效；原9728微点不释放，待核算单独计数，达到3笔时拒绝新操作，新任务可使用剩余余额。

**Architecture:** 原中央钱包锁下完成所有准入检查，保留旧key优先幂等检查。并发与待核算只改变准入计数，不更改任何资金分桶、执行状态转换或模型绑定。客户端兼容旧服务缺字段，所有新增计数均为安全整数。

**Tech Stack:** Python 3.12、PostgreSQL 16、psycopg、React/TypeScript、内置Pi；沿用现有锁定依赖，无SQL迁移。

## Global Constraints

- 实际执行并发沿用原套餐1；待核算按全钱包跨产品/模型/会话/资金模式统计，3为新操作准入阈值。既有在途请求随后失败时，待核算数量可能超过3，仍须保留全部未知hold并拒绝后续新准入，不能删减、裁剪或清零。
- 原9728微点、未知actual和原冻结费率完整保留，新增请求必须从剩余余额正常预留。
- 不自动重试、不手改资金表、不重开旧writer、不合并main。
- 中断恢复GET、保存设置和管理员页面读取不调用模型或改变账务；钱包沿用原有到期入账流程，本次不改变该流程。
- 新中央代码修改位于`services/platform/src/platform_service/credits.py`；客户端为`easel/platform/central.py`、`execution_proxy.py`、`easel/harnesses/pi.py`和账号组件/API类型。

1. **中央测试先行。** 在全新随机schema的真实PostgreSQL中，复现1笔pending阻断新任务，再验证pending达到3时拒绝新操作、原active限制、跨产品/资金/会话计数、隔离、同key幂等和并发竞争。
2. **最小中央修改。** `credits.py` 新reserve保留原幂等优先顺序；pending全钱包计数达到3拒绝，active仅reserved/dispatched；只读wallet加active_requests和pending_reconciliation_limit。保持所有holds、actual、费率、journal及执行状态转换不变，无SQL迁移。
3. **客户端回归先行。** 白名单解析新增可选整数；旧服务器缺字段显示未知。Pi/owned proxy只映射固定pending_reconciliation_limit，不透传错误正文。账号页显示执行中与待核算上限，自定义模式不变。
4. **独立复核与完整回归。** 中央真实PG全套、客户端相关回归、前端全套、TypeScript/Vite。验证查询不发模型和原pending不变；不将模拟证明描述为真实扣点。
5. **版本与备份。** 新中央release使用原锁定依赖，比较源变化和旧迁移；最终macOS新包重建来源与资产清单、实际隔离启动。154生成认证加密备份，原数据不回滚。
6. **受控更新。** 阻断新的模型准入并排空在途请求，保存原private配置和unit；切新release后健康/权限/原pending完整财务快照一致，再恢复新准入。当前中央身份与权威不变。
7. **一次真实模型闭环。** 原账号登录沿用，不另发点；新合成对话用只读fixture执行文件工具，验证流式结果、冻结费率、真实usage和整数扣点。原未知job及9728预留完整不变；禁止重发未知结果。
8. **只读恢复与交付记录。** 额外GET查询前后财务快照一致；记录本轮费用、管理员验收、版本和备份。保留未知费用、现有账号、自定义配置和旧writer隔离；正式支付、媒体、云Agent及灾备压测仍为后续范围。

## 执行状态

- [x] 用户批准方案A；完成设计边界与原账务基线。
- [x] 中央真实PG红/绿及完整回归；最终完整轮次953 passed。
- [x] 客户端白名单、UI旧服务兼容及全前端验证；最终前端轮次251 passed、0 failed、0 skipped。
- [x] 独立审查；新wheel与v10客户端包完整性及隔离启动。
- [x] 新备份自校验、准入维护门禁、排空和受控部署；原钱包九表财务投影前后一致。
- [x] 管理员用户本人登录后只读跨页验收。
- [x] 原账号一次浏览器UI真实合成工具/流式/扣点；保留旧pending，两次完整只读恢复不重派。
- [x] 更新本阶段计划记录、已验结果和最终未验边界。
- [x] root写入[最终安全回执](../../validation/2026-10-25-execution-stability-safe.json)，完成本轮自有测试资源cleanup及最终只读探测。

## 2026年10月25日阶段记录

中央已切换到 `/srv/easel/releases/20261025-pending-concurrency-v1`，API与Console静态资产健康；切换前后原钱包九表投影一致，旧pending仍为9728微点、`actual=null`。原NGINX配置已恢复，两个公开准入路径的无认证GET恢复405；切换过程模型调用为0，无SQL迁移。部署验收快照为执行中0笔、待核算1笔、待核算准入阈值3笔，余额为available 9982185、held 9728、spent 8087微点。

v10已运行在本机17868，归档SHA256为`337af54acb527da5360f45638e7e3cbc70a6326663359e90adf15f67da954cf4`；全新HOME隔离smoke通过，原用户状态保持。中央953项真实PostgreSQL完整回归与前端251项最终回归为各自独立轮次，不与先前轮次或补验相加。

v10浏览器UI仅发送一次合成任务，session为`mvnlnsnc0ltqn5`、turn为`1792919039646-i24yznjy9lo`。随机fixture内容未预先泄露给模型，最终回复与fixture精确一致；Pi结果为`clean_end=true`、`completed`，SSE只读重放确认工具序列恰为`['read_file']`且`done`。该次任务形成两个不同operation与attempt的`settled/live` job，每个job仅一个attempt：

| job | input tokens | output tokens | actual微点 |
| --- | ---: | ---: | ---: |
| 第一笔 | 667 | 438 | 991 |
| 第二笔 | 727 | 49 | 437 |

本轮合计扣费1428微点，即0.001428点。issued仍为10000000微点，available为9980757、spent为9515、held为9728；原有行除lot余额外逐行保持，原pending仍为9728微点、`actual=null`，没有grant或payment，也未以新结果回填原未知费用。

两次完整GET恢复均为`found`、`truncated=false`，可查到新两个`settled/live` job及旧pending，lookup的chat POST为0；查询前后财务哈希均为`15d5f24fe9eee71dd9c91ad2f5b061004ea0f6dd0f36e158132f568b8b8ad0eb`。同一管理员session跨页查看确认15个job、1个pending、0个order及上述相同余额。过程中曾短暂出现`platform_unavailable`，另有一次10秒恢复查询被截断；随后在原会话进行的两次全量GET成功。本轮结果不归因该短暂异常，也不构成长期稳定性证明。

新认证加密备份为`/var/backups/easel-encrypted/20261025T091036Z-59975318-f7f7-4268-913a-23ffeccbf322.sealed`，SHA256为`6679e11d3b5f7df84965909562c5cceccaf51a3be34fd11cf6982ed238f983a4`，809802字节、13个文件，状态`done`、self-verified，权限0600；它仍是本机备份，fresh restore和offsite未验。

最终核对确认自定义`config.json`与bundled-provider仍和原文件逐字节相同。本轮自有PG `test-v2`已停止，inspect确认未运行且为exited，容器保留，生产资源未操作。最终只读探测确认原账号已认证、wallet 200且为上述新余额，执行中0笔、待核算1笔、准入阈值3笔；OIDC issuer正确、API 200、公网Console 404、私有Console 200。最终结果已写入[安全回执](../../validation/2026-10-25-execution-stability-safe.json)。

日志正文读取被自动审批拒绝，未执行；安全替代为服务器固定分类计数，所查分类计数均为0。这不等于所有日志均不存在，也不代表短暂异常根因已查清。旧pending未知费用、显式stop、正式支付、媒体、cloud Agent、HA、整机reboot、负载、fresh restore、offsite和告警仍保留未验；本阶段不合并main。

## 可复现验证命令

```sh
rtk proxy env EASEL_CENTRAL_TEST_DSN_FILE=/private/tmp/easel-zhiyin-migration-20261024/pending-test-dsn.json services/platform/.venv/bin/python -m pytest services/platform/tests -q --tb=short
rtk proxy node --test tests/frontend/*.test.mjs
rtk proxy npm --prefix web/frontend run build
rtk proxy git diff --check
```

DSN文件只指向本轮专属loopback合成测试库。缺失应失败，不使用生产库或SQLite代替。具体容器和端口以本轮安全回执为准，不固定复用历史端口。
