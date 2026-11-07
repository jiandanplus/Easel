# R2 核算恢复部署与处置

中央代码发布与历史费用的经营处置是两个独立阶段。发布新版本和追加迁移不会自动释放旧预留；仅部署 R2 也不会绕过每钱包最多 3 笔待核算的准入限制。普通产品 API、Console 和桌面查询继续保持资金只读。

2026-10-26 已发布中央 release `/srv/easel/releases/20261026-reconciliation-r2-v1`，桌面 17868 为 Pi portable v17。获批的三笔旧 GLM 请求已在独立操作中转为 waived，释放 29184 微点预留，客户收费 0，原 actual=NULL 保持；待核算及在途均已清零。[本轮验证记录](../validation/2026-10-26-reconciliation-r2-recovery.md)给出版本哈希、1101 项中央回归、发布保全快照、真实资金操作和独立只读后验。之后本人账号的内置 Pi／DeepSeek Flash 文件工具对话与可信用量扣点也已通过，共 0.00228 点，最终余额 9.971423 点；这些真实调用的审计与三笔经营处置分别保留。

## 两种恢复路径

新请求在真实出站前记录原 job／attempt／binding、上游模型、载荷哈希及关联 ID。中央严格校验带可信用量的终态，先提交不可变终态证据，再执行冻结费率结算。若资金提交失败，worker 的恢复只重试原结算，不再发送供应商请求；没有原回复时，结果状态为 `not_available`，不能伪造回复或重新执行工具。

历史请求仍无可信用量时，不用近似日志、SDK 估算或零费用 receipt 核清费用。明确批准的运营承担通过独立 `waived` 终态结束客户预留：客户收费为 0，job／reservation 的 actual 保持 NULL，供应商费用与 usage 仍未知；处置、批准依据、原 attempt／binding／scope 摘要和 release journal 不可变留档。晚到终态不能覆盖该决定或补扣客户。过期额度进入 expired，不能复活为可用额。

## 代码发布契约

生产发布由 root 私有目录中的专用计划／切换辅助脚本完成，使用新 release 的 Python。默认只读取并冻结计划；`--apply --reviewed-plan-hash` 才进入代码发布。该辅助脚本没有费用承担命令或真实模型请求。

1. 验证新加密备份收据及密文 SHA／权限、新 wheel 与全部 payload、旧迁移逐字节保持、新迁移哈希、原配置 SHA／元数据，以及实际 schema owner；确认 runtime／Console 是独立受限角色。
2. 对 `/v2/responses`、`/v2/task-tokens` 和 `/v2/executions/{id}/cancel` 关闭准入，验证旧 Nginx worker 排空和无在途任务；保存全部八张权威财务表的全钱包快照。
3. 停止 API／Console，只追加 012／013，重新授予并校验受限权限；原 systemd unit 仅把旧 release 路径原子替换为新 release，保留 owner／group／mode。
4. 重启后核对 API／Console health、Console assets ready、公网 Console 404、全财务投影及原待核算完全相同，再恢复原 Nginx 配置和准入。
5. 最终核对配置／数据投影及迁移校验和。失败关闭准入并保存服务器私有日志，不自动回滚数据库；迁移提交应答未知时不能记作“未提交”。

数据库、运维端口和中央后台保持原私有边界。供应商 Key、New API 业务令牌、账号库凭据、真实连接和私有计划内容不进入桌面包、浏览器、源码或聊天。备份状态 selfverified 不等同于 fresh restore、offsite 或灾备验收。

## 平台承担 CLI

入口是中央服务器上的 `python -m platform_service`。需通过原服务器安全配置在进程环境注入 `CENTRAL_MIGRATION_DATABASE_URL`、runtime 配置与 schema；不把 DSN 放在命令行、网页表单或操作记录中。CLI 除检查 URL 目标外，还通过数据库查询验证真实会话用户就是当前 schema 的 owner，才赋予本次独立 `execution:writeoff` 能力。

以下是操作形状，参数中的占位值需要由已批准计划替换。计划输出包含私有业务快照，使用受限文件接收，不粘贴聊天或保存在公开输出目录：

```sh
python -m platform_service execution-writeoff-plan \
  --operator HOST_OWNER_OPERATOR_ID \
  --job EXACT_JOB_ID \
  --reason APPROVED_REASON \
  --approval-reference APPROVAL_REFERENCE

python -m platform_service execution-writeoff-apply \
  --operator HOST_OWNER_OPERATOR_ID \
  --plan /root/PRIVATE_OPERATION_DIRECTORY/approved-plan.json \
  --reviewed-plan-hash EXACT_APPROVED_PLAN_SHA256 \
  --key ORIGINAL_OPERATION_IDEMPOTENCY_KEY

python -m platform_service execution-writeoff-operation \
  --operator HOST_OWNER_OPERATOR_ID \
  --key ORIGINAL_OPERATION_IDEMPOTENCY_KEY
```

`--job` 可重复，计划绑定精确集合及原资金／执行快照。计划文件只允许当前操作者拥有的普通私有文件，拒绝符号链接、组／其他用户可读和超限内容。apply 在原钱包锁序下复核快照、可处置条件和计划哈希，全部原子提交；原操作查询只读。已提交响应丢失时先查原幂等键，不换键重做，不重发模型。

通用运营角色、Console 的 `execution:review`／publish 和产品 runtime 不自动拥有 writeoff 能力。Console 不提供资金处理按钮；其工单处理进度独立于实际财务终态。

## 媒体任务（迁移 021）

媒体待核算任务使用同一入口和同一套规则（[决策 0007](../decisions/0007-media-writeoffs.md)），命令为 `media-writeoff-plan`、`media-writeoff-apply`、`media-writeoff-operation`，参数与上面相同。计划多一个 `"subject": "media"`，快照还包含存储预算与异步任务阶段。可处置条件：已派发、租约已过期、无 receipt、无终态证据、`media_work` 不存在或为 `manual`。apply 在同一事务内释放点数预留与存储预算，任务变为 `waived`、`delivery_state=unavailable`，不重新生成、不恢复产物。

## 查询协议与用户显示

对已承担的请求，安全查询字段必须同时满足：

```json
{
  "status": "waived",
  "actual": null,
  "input_tokens": null,
  "output_tokens": null,
  "customer_charge": "0",
  "resolution": "platform_absorbed",
  "provider_usage_known": false,
  "result_state": "not_available"
}
```

后台和桌面显示“平台承担费用，用户不扣点；供应商用量未知”。`actual=null` 与 `customer_charge="0"` 分属供应商费用未知和客户收费两个事实，不能互换。取回模型回复遇到该终态返回明确的 `execution_waived`，不当作成功回复，不触发自动重发。R1 的 finished／all 查询能读取该终态，包括没有 review case 的历史记录。

真实三笔处理后，需要独立核对原操作收据、不可变审计、journal 平衡、额度有效期、余额与待核算数量，再由用户本人登录验证真实 Pi 文本、工具、用量和扣点。菜单可用、健康检查通过、本地合成测试或历史真实验证均不能代替这次验收。自定义接口模式继续使用原配置，不因中央钱包处置而修改。

已产生 waived 的生产库不能直接配回缺少完整 waived 协议的旧 release；采用保留新语义的向前修复。不能恢复旧数据库来配合旧代码，覆盖已经发生的真实账本与处置记录。
