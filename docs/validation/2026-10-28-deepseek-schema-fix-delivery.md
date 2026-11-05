# DeepSeek Flash 工具 schema 修复与交付状态

状态已更新：空间恢复后，v28完成真实模型／write_file／下载／扣点验证；随后v29修复连续输入模型就绪问题并完成新的真实文本及下一草稿就绪验证。当前原17868运行v29，以 [v29实际交付报告](2026-10-28-workbench-v29-delivery.md)为准。旧unknown保持，下文的早期准备与磁盘阻挡是历史记录。

下面的磁盘阻挡和准备记录保留为当时证据：首次尝试时本机剩约159MiB，低于200MiB安全余量，未执行v28构建，已请求恢复空间；对应原safe JSON不追改。随后复核约2.1GiB可用，才恢复构建，旧包、输入、历史及配置均未删除。

## 已确认的原因与修复

用户明确批准后，仅在 115.190.211.112 服务器检查北京时间 2026-10-28 10:43:14–10:43:44、deepseek-flash 的错误记录，匹配一条。导出仅为固定错误类别、公开参数名及关联请求 ID；没有原始响应、提示词、账号或密钥，没有数据库写入或模型重试。详见 `2026-10-28-deepseek-flash-schema-diagnostic.safe.json`。

错误为 `create_media` 多模型工具 schema 缺少根级 `type: object`。`runtime/pi/worker.mjs` 只将 `Type.Union(variants)` 改为 `Type.Union(variants, { type: 'object' })`，保留原 `anyOf` 和每个模型的参数、必填项、上限及权限约束。新 worker SHA256 为 `7fc068daae8e9cc9dc201252dbb412de9adb935569e0cb20e641ad6e2b193f6d`。

作者相关闭包最终 168 passed／0 skipped；独立审查另以实际锁定 Pi／OpenAI SDK 做 8 次序列化运行，比较 0／1／2／100 模型：有媒体模型时旧版本被严格模拟网关拒绝，新版本通过；去掉新增根 type 后所有工具 parameters 完全相同。全部 host 调用与非 Mock fetch 均为 0。此结论是实际 SDK 与受控协议验证，不是供应商真实验收。详见 `2026-10-28-pi-media-root-schema-local.md`，以及 `/private/tmp/easel-pi-root-schema-independent-20261028/review.md`。

## 同批待打包内容

请求状态卡片以“文本模型”和真实预留／已扣／已释放金额展示状态，隐藏内部模型 ID、执行编号与本机转发／缓存实现说明；未知实际费用仍显示未知，旧历史正文没有改写。前端完整回归为 485 passed／0 failed／0 skipped，TypeScript 与 diff 检查通过。

新的 Python 输入已经从独立核验的官方 CPython 3.12.13＋20260510 stripped 归档完全离线重新准备，固定 uv 0.11.16，原 26 个 wheel 的 SHA 和版本不变。新 verifier 的实际 archive／metadata／runtime／wheel 校验通过，独立 13 项篡改反例全部通过。证明链为已审离线派生记录、冻结库存和每次构建的实际输入校验；不声称每次重放或可复现构建。v27 历史 `python_archive_verified=false` 保持不变。正式签名、公证、Gatekeeper 和干净设备仍未验收。

为降低打包峰值，新增显式 `--copy-on-write` 候选，只用于打包期复制已核运行时；默认仍使用普通复制。macOS `clonefile` 无回退，核对独立 inode、原树前后及副本完整内容和属性，分阶段检查剩余估算加 200 MiB。作者 15 项测试通过；独立另 9 项反例通过，结论为 APPROVE_CODE_ONLY。阶段检查不锁定磁盘块，此批准不代表容量门槛已满足；实际 App／ZIP 均未执行，空间门槛不会因采用写时复制而降低。独立报告为 `/private/tmp/easel-v28-cow-independent-review-20261028/review.md`，SHA256 `93d6366c7bd0697a0b992335a1701841d7f982a750da2086f1143407c716d2a1`。

## 真实请求和资金保持原状态

原 v27 回合 `b01ee330-6497-479b-9ce3-90eb913c02ba`、中央 execution `bdbc4509-7b03-42ac-9b68-3c1381e977c5` 只有一次供应商尝试，HTTP 400，目标文件未创建。状态继续为 `pending_reconciliation`，`actual=null`，预留 9,728 microcredits（0.009728 点），没有重放或释放。

北京时间 2026-10-28 11:54:25 再次只读核对，账号仍已登录、登录流程未挂起、没有活动任务；钱包为可用 6.942425 点、预留 1.009728 点、已用 2.047847 点，active=0、pending=2／上限3，与原失败后的余额一致。其中原 Flare 1 点预留保持不变。本轮没有新增点数、延期、付款、退款或平台承担决定。

## 恢复构建后的执行顺序

1. 核对用户释放空间或提供的新磁盘目录，重新核算容量与所有冻结 SHA。
2. 独立审查通过后，新建 Pi stage，只更新受审 worker 和 manifest；统一冻结公共来源及构建输入，再构建全新 v28，旧 App／ZIP 保留。
3. 完成 ZIP CRC、文件库存、凭据扫描、实际包内运行环境及严格 SDK Mock 验证，在全新显式 workspace 运行，HOME 不变，只停止本次自有实例。
4. 对原工作区做新的状态备份与账号／钱包／历史／配置逐项核对后，才升级 17868。
5. 另建全新会话和文件路径做真实模型／写文件／扣点验证，不能重放原未知请求；未执行前不得标记通过。

所有工作保留在 `codex/ui-redesign`，本批没有 commit、push、merge 或中央生产部署。
