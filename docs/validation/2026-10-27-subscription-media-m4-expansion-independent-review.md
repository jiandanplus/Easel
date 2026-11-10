# M4 桌面/Pi 多媒体扩展独立审查

日期：2026-10-27。结论：**changes requested；1 个 P1 阻断项**。仅绑定本次 11 文件冻结候选，不审整个 dirty branch，不改变被审实现。

## 已确认问题

### M4-R1 / P1：rate 的未知字段直接越过公开目录和 Pi JSON 边界

定位：`easel/platform/media_contracts.py:118`，相关验证为 89–96 行；发送点 `easel/harnesses/pi.py:126–127`。`public_definition` 校验 rate.version 和 meters 后仍执行 `deepcopy(rate)`，没有白名单限制 rate 本身的键。顶层字段、meter、limits 和 parameters 都有明确投影或拒绝规则，rate 成为遗漏的子对象。

最小复现：对本来有效的合成目录行添加 `rate.provider_id="synthetic-private-provider"`、`rate.owner.user_id="synthetic-private-owner"`、`rate.internal_note="nonpublic"`。`MediaHost.models()` 原样保留以上字段。随后用真实 `PiAdapter._run`，仅将 worker 替换为读取 JSONL stdin 的本地 Python 测试进程，进程断言能读取两个私有 ID，整个调用正常结束。相同字段放在目录顶层会被过滤，确认缺口在 rate 子对象。

影响条件：中央目录响应中只要混入内部费率/owner/provider 字段，这些未获允许的内容就会进入 Pi 子进程 JSON。`CentralClient._safe` 只按部分凭据字段名及已知 secret 拒绝；worker 的黑名单也未覆盖 provider_id/owner。这里证明的是桌面防线缺口，**没有声称真实中央已返回或泄漏私有信息**。即使当前中央严格投影，此桌面合同仍承诺只向 Pi 传安全公开定义。

最小修复：rate 使用严格键集合 `{version, meters}`，拒绝未知键或者仅显式构造这两个字段；meter 保持现有精确白名单。增加 catalog 与 PiAdapter JSON 边界的负向测试，确认未知嵌套字段被丢弃或整行关闭，同时完整有理数 meters/basis 不被压扁。

## 独立验证与证据

- 冻结清单来自 `docs/validation/2026-10-27-subscription-media-m4-expansion-safe.json`；11 个文件审查前、审查后 SHA256 均与该清单一致。
- patch `/private/tmp/easel-m4-expansion-freeze-20261027.patch` SHA256：`78389de962eceb5b111d35edb367373d380ffd216350487847fd7364f3f205ba`。这是 untracked 完整源快照，不是 v23 增量 diff。
- 独立复现脚本：`/private/tmp/test_easel_m4_independent_review.py`。
- 命令：`rtk proxy .venv/bin/python -m pytest -q /private/tmp/test_easel_m4_independent_review.py`；结果 **2 passed / 0.15 s**。两个通过断言表示成功复现缺陷，不代表安全验收通过。
- 使用合成账号、临时工作区与 HTTPX MockTransport；实际执行 PiAdapter 的 stdin 序列化路径，未重复作者的实际 Pi SDK 12 场景，未重新跑广泛 257 项测试。
- 完整 pre/post SHA 和测试脚本 SHA 见同名 `.safe.json`。

## 本次范围内的其他结论和限制

静态检查了图片共享 bucket 与独立 capability bucket、旧记录只读兼容、unknown 后不重发、输入文件冻结、owned upload、完整 accepted scope 与同 owner 恢复、产物归属/哈希/头/独占发表和 stop/logout fence。除上述问题，本轮没有额外确认的阻断；该句不是全部并发/崩溃窗口已经动态验证的声明。

MP3/Ogg/MP4 的桌面检查按已确认分工理解为头/容器和 hash/size 完整性检查，完整解码及 duration/channel 计量由中央固定工具承担。本轮未把“桌面没有完整 AV 解码”列为缺陷，也没有把现有素材 fixture 等同于真实供应商支持。

未读真实凭据、未访问真实 API/PG、未 SSH、未改 services/platform、未 build/package/部署/commit。新的中央→host 实际联调、真实供应商验收仍由对应后续阶段单独证明。本报告不授权发版或生产开放。
