# 2026-10-26 R2 核算恢复验证记录

状态：**R2 代码、迁移及桌面兼容版本已发布；用户明确批准的三笔旧 GLM 平台承担已真实提交并独立复核。** 中央服务运行版本为 `/srv/easel/releases/20261026-reconciliation-r2-v1`，本机 17868 工作台已切换到 Pi portable v17。真实隔离 PostgreSQL 的中央全量回归为 **1101 passed，212.19 秒**。代码发布与实际财务处置分开执行，两者均未调用供应商模型。

本轮继续使用 `codex/ui-redesign`，不合并主分支。订阅、自定义接口、既有账号和对话历史保留。财务处置后的待核算及在途均为 0，原新请求入口已恢复；随后用户本人登录原账号，内置 Pi／DeepSeek Flash 的真实文件工具对话、可信用量和扣点验收通过，证据另列如下。

## 已交付行为

| 路径 | 行为与边界 |
| --- | --- |
| 新请求的可信证据 | 出站前保存精确 job、attempt、binding、上游模型及实际请求载荷哈希。符合原协议的终态与非负整数用量先单独提交，之后执行原冻结费率结算；不保存 prompt、鉴权头、密钥或回复正文到关联证据中。 |
| 资金提交失败后的恢复 | 仅依据该 attempt 的可信持久化终态重试原结算，不再提交供应商请求。有费用证据而没有原回复时，不制造结果缓存；`result_state=not_available`。 |
| 未知费用的平台承担 | 独立服务器所有者 CLI 提供只读计划、精确计划哈希确认、幂等处置及原操作查询。`waived` 是独立财务终态，actual 继续为 NULL，用户收费为 0，供应商用量仍未知；不创建虚假的 usage、receipt 或回复。 |
| 账本和并发 | 使用原钱包锁顺序、平衡 release journal 和不可变审计。过期额度释放到 expired，不重新变为可用额。晚到 finish 不覆盖 waived。执行并发和待核算分别限流，待核算上限仍为 3。 |
| Console | 保持财务只读。R1 的“已完成／全部”可查看 waived，包括没有 R1 工单的记录；没有新增财务按钮或把 review/publish 权限扩大为 writeoff。 |
| Easel 桌面 | 按严格协议展示“平台承担费用、用户不扣点、供应商用量未知”。不把 waived 当模型成功回复，不自动重发，不自动恢复工具调用。普通状态查询不写资金。 |

## 本地验证

最终中央测试日志为 `/private/tmp/easel-reconciliation-r2-20261026/central-final-all-green.log`，尾部结果：

```text
1101 passed in 212.19s (0:03:32)
```

本轮覆盖真实隔离 PostgreSQL 下的账本平衡、NULL 金额守卫、未知用量、过期 lot、权限隔离、计划 CAS、幂等、并发、审计回滚、迟到 finish，以及可信终态保存后只恢复结算的路径。隔离供应商验证用于确认供应商 POST 不会因结算恢复而增加；此类合成协议证据不等同于真实供应商兼容性验收。

代码发布辅助脚本另有 **61 项纯本机测试全部通过**，语法检查通过。最终脚本 SHA256 为 `7f3956390ee5b5d99a950f8682045aee1305d882b078118b23ab79c2e86c3302`。默认仅生成只读计划；发布模式必须显式提供 `--apply --reviewed-plan-hash`。代码发布辅助脚本不包含财务 apply 或模型调用。

## 中央代码发布

下表来自本机安全回执 `prepare-release-safe.json`、`pre-backup-safe.json` 及 `switch-applied-safe.json`，原始文件位于 `/private/tmp/easel-reconciliation-r2-20261026/`。读取和部署敏感连接均限定在服务器，本记录不包含真实连接信息或私有归档正文。

| 项目 | 已核对证据 |
| --- | --- |
| 目标与 release | 新服务器 `154.217.245.179`；`/srv/easel/releases/20261026-reconciliation-r2-v1`。 |
| 安装包 | 中央 wheel 240,132 字节；SHA256 `fa0cb2064ce9ce196a9cca3da244344952f9d80be9c2f70e4d10ec0c34259065`；ZIP CRC 和 62 个 payload 文件通过。 |
| 独立环境 | fresh venv、依赖声明未变、`pip check` 通过；历史迁移 001–011 与原 release 逐字节相同。 |
| 新迁移 | 追加 `012_execution_writeoffs.sql`：`690580de82453616858b0af49d721b70781ebdfef3e0f16634b7a76ee191990d`；追加 `013_execution_evidence.sql`：`a36c19e9121baa472dd3685e63cc134cf09b28cd28dfe0a391a015aa7601c0ab`。 |
| 发布前备份 | 13 文件的加密归档 840,522 字节，密文 SHA256 `aa02ee9592117405674e0de76dce1251503f31bde6a65545e43c13471e67c8cb`；新文件哈希与权限通过，状态 selfverified。此次没有 fresh restore 或 offsite 证据。 |
| 切换控制 | 对 responses、task-tokens、execution cancel 三路准入关闭，旧 Nginx worker 排空、无在途任务后停 API／Console；保留原 release 和配置备份。 |
| 权限 | 实际数据库 schema owner 校验通过；产品 runtime 与 Console 的独立最小权限授予、启动 guard 及受限连接复核通过。 |
| 健康与公开边界 | API／Console 健康，Console assets ready；公网 Console 为 404。原 Nginx 配置及准入恢复，原配置哈希保持。 |
| 发布操作范围 | `waiver_operations=0`、`model_calls=0`、`database_restore_attempted=false`、`secrets_left_server=false`。 |

代码发布的已审计划哈希为 `7a304b2559bf728e83da48687690273895dafc3fe400cbc97b6e86dbe102aaa1`。这是版本切换计划，不能用于批准或替代财务处置计划。

## 发布完成时的财务保全基线

八张权威表在迁移及切换前后的完整全钱包投影相同，SHA256 为 `5cbca7501ab4e16c1eedbae4f298d7cc1329bafbe1497843057cebd6d33730c0`。

| 表 | 记录数 |
| --- | ---: |
| credit_lots | 1 |
| credit_reservations | 28 |
| credit_holds | 28 |
| credit_journals | 54 |
| credit_entries | 133 |
| execution_invocations | 14 |
| execution_jobs | 28 |
| execution_receipts | 25 |

此时三笔旧 `easel-glm53-internal-v1` 请求仍为 `pending_reconciliation`，每笔预留 **9,728 微点**，合计 **29,184 微点（0.029184 点）**，actual 均为 NULL。没有释放预留、修改历史、提高待核算上限或制造用量凭据。该快照说明发布本身没有动钱，不表示后续财务处置后的当前状态。

独立的三笔平台承担计划已获得用户批准；`waiver-plan-approved-safe.json` 是 apply 前的计划回执，明确 `plan_only=true`、`production_financial_writes=0`。财务计划 SHA256 为 `09406a6489f4c8d3d74e40011d088ab751c9939f250d95b43c36b81ace99702d`。下节以独立 apply 和只读查询收据记录实际结果。

## 获批三笔的实际处置与独立后验

用户明确批准仅处理这三笔旧 GLM 请求，批准依据为 `human-approval-call_0ef311c5d63a4f9aa94bba6c6526dc3e-20261026`。服务器所有者入口在核验精确原计划后，只提交一次固定幂等键 `r2-approved-legacy-glm-20261026`；操作 ID 为 `400ecc4e-554e-4137-87e7-caa651d7414c`，返回 committed。没有扩大到第四笔或后续请求。

| 核对项 | 实际结果 |
| --- | --- |
| 原三笔 | job 与 reservation 均转为 waived，每笔原金额 9728，actual 均保持 NULL。 |
| 客户收费 | 0 微点；供应商用量及费用仍未知，不因经营承担伪装成零用量。 |
| 释放预留 | 共 29184 微点，即 0.029184 点；本次有效额度全部回到 available。 |
| 新增记录 | 3 笔 release journal、6 条平衡分录、3 条不可变 resolution、1 条不可变 operation。 |
| 原账保护 | 原 hold、receipt、result、provider evidence 及其他旧行完全保持；未新增假回复或假 receipt。 |
| 余额后验 | available=9973703（9.973703 点）、held=0、spent=26297；这是本次处置后、下一次模型调用前的快照。 |
| 准入 | job 与 reservation 的 active/pending 均为 0；原 Nginx 恢复，新请求路由恢复。未提高待核算上限。 |
| 权限和模型 | 两个受限角色均无处置审计表/列的读写权限；证据表不可变触发器及迁移校验通过；仅 DeepSeek Flash/Pro 两个已验证运行映射启用。 |
| 操作边界 | 服务未重启、模型调用 0、未恢复数据库、密钥未离开服务器；公网 Console 仍 404。 |

处置脚本的 **26 项纯合成差异验证测试通过**。实际提交后先逐表校验完整差异，再恢复准入；另一次原幂等键只读查询再次确认同一操作、释放金额与完整差异，没有再次 apply。独立 `REPEATABLE READ READ ONLY` 核对再次确认三笔精确关联、全 lot 守恒、全 journal 平衡、模型及权限。安全证据为 `approved-waivers-applied-safe.json`、`post-waiver-readonly-safe.json`。

处置后又运行原加密备份服务，归档 `20261026T115014Z-046d17e4-60fe-48ae-931c-2e3404d5c48c.sealed`，881482 字节，密文 SHA256 `bc4de668d2eb9d45fbfa807a659631f23fb1f22e14dd31049cdaa6da9698708d`。新文件权限与哈希、systemd done 均通过，状态 selfverified；这不代表本轮完成了新的恢复演练或异地备份。

## 桌面 v17 与原用户状态

`activation-safe.json` 确认 17868 已运行新包，页面 HTML 与新包一致；继续使用原 HOME 和工作区，原应用保留，旧启动器转向 v17。`preservation-safe.json` 确认 **95 个受保护文件无差异**，对话历史和自定义接口配置保留，账号业务归属保持。

本次桌面切换没有模型调用。切换检查时没有已登录钱包观测，回执为 `wallet_observed=false`、`same_wallet=null`；该历史回执不代表后续本人登录结果。随后确认 authenticated=true，并逐字段比对原 user／tenant／billing account／product 相同，原用户钱包余额及权限已在客户端 API 和界面复核。

## 本人账号真实 Pi、工具与扣点验收

用户亲自在新的统一登录窗口登录后，本轮只通过 17868 工作台提交 **一条**新的合成测试消息，默认模型为 `easel-deepseek-flash-internal-v1`，实际上游 `deepseek-flash`。没有重试旧 GLM 的“你好”或重发费用未知的任务。预先在独立目录创建 probe 文件，随机标记未放进模型提示词；提示仅要求读取该文件，不执行命令、不写文件、不访问网络。

工作台展示 `read_file`、`list_files`，最终正常完成并返回 probe 的精确随机标记；测试文件保持不变。本机持久化结果为 `status=done`、`clean_end=true`、`stop_reason=completed`，route 为 subscription／local／pi。系统技能提示使 Agent 另做只读列目录，所以一条用户消息共发生三轮正常模型请求，而不是一次请求重试三遍。

| 中央 job | 输入 tokens | 输出 tokens | 冻结费率计算（微点） | 实际微点 |
| --- | ---: | ---: | --- | ---: |
| fe8057ee-763f-4c97-ae6c-379b07d12313 | 800 | 205 | ceil(800×1/2 + 205×3/2) | 708 |
| 8ab3ac8b-79ed-48ac-b969-3bb33af4664a | 1064 | 151 | ceil(1064×1/2 + 151×3/2) | 759 |
| 63156afe-43b9-4af6-afca-fb0d74111fff | 1232 | 131 | ceil(1232×1/2 + 131×3/2) | 813 |
| **合计** | **3096** | **487** | 每个 job 的输入输出相加后仅向上取整一次 | **2280** |

冻结版本为 `internal-deepseek-20261026-v1`。三笔都为 settled，provider attempt、HTTP／response 关联、不可变 terminal evidence、receipt、冻结费率与实际扣点精确匹配。独立 Fraction 计算得到 **2280 微点（0.00228 点）**，正好等于 lot.spent 增量及 available 减量。最终可用 **9971423 微点（9.971423 点）**，累计 spent=28577，reserved=0、active=0、pending=0。旧三笔 waived、actual NULL、原审计完全保持。

本机又只读取回本次完成结果两次，返回相同 done 结果和标记；随后服务器完整比较 13 张财务／关联证据表与验收快照完全一致，没有新增 provider attempt、重复扣点或预留。只读验收助手的 9 项合成测试通过。安全证据为 `real-live-pi-accounting-safe.json`、`real-pi-client-safe.json`、`real-pi-after-client-safe.json`、`result-get-stability-safe.json`，统一归档在 `outputs/_run/reconciliation-r2-20261026/evidence/`。

![真实工作台、内置 Pi 与文件工具验收](../../outputs/_run/reconciliation-r2-20261026/evidence/real-pi-tool-success.png)

本轮真实 Pi 验收针对 Flash；Pro 的授权目录及服务器运行映射已核对，不把这次 Flash 验收表述为本轮 Pro 或媒体能力验收。未额外模拟真实网络中断或制造新的未知费用；相关并发、保存终态后财务失败和恢复路径由隔离 PG／协议回归覆盖。

## 后续证据槽位

| 阶段 | 待追加内容 | 当前结论 |
| --- | --- | --- |
| 获批财务 apply | 原幂等键的操作收据、三笔 waived 结果、actual NULL、用户收费 0、供应商用量未知、原审计与 journal、额度归属、全钱包平衡及待核算计数。 | 已真实提交并按完整差异复核，见上节。 |
| 财务 apply 后复核 | 原键只读查询、三笔原始关联、模型权限、全账平衡、原 Nginx、新请求路由和公开／私有边界。 | 已通过；查询未再次释放或调用模型。 |
| 本人真实 Pi 验收 | 用户本人登录、Flash、三笔真实中央 job、正常终态、真实文件读取和回复、可信 usage、冻结费率算式与钱包差额。 | 已通过，合计 0.00228 点，最终余额 9.971423 点。 |
| 未知付费结果 | 若真实测试发生超时或断流，保存原键和未知状态，只查询或按可信证据恢复，不自动重发。 | 沿用原约束。 |

生产已经使用 waived 后，不应直接回滚到缺少 waived 完整查询协议的旧版本。旧版本不能表达“供应商用量未知、用户收费 0、平台承担”三者，桌面严格校验会拒绝不完整记录；应采用保留新协议的向前修复。不得为回退代码而恢复旧数据库覆盖新账。

相关文档：[R2 设计](../superpowers/specs/2026-10-26-reconciliation-r2-recovery-design.md)、[R2 实施计划](../superpowers/plans/2026-10-26-reconciliation-r2-recovery.md)、[部署与处置说明](../platform/reconciliation-r2-deployment.md)。
