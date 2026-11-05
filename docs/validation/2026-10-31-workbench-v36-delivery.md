# Easel v36：复制回复反馈修复

v36 的 macOS 15+ Apple Silicon 离线包已构建并完成独立静态核验。原 `http://127.0.0.1:17868/` 仍运行 v35，尚未执行切换、用户数据备份或账号操作。开发分支保持 `codex/ui-redesign`，没有提交、推送或合并。

[macOS v36 ZIP](../../outputs/_run/pi-portable-20261031-v36/Easel-Pi-macOS15-arm64.zip) 为 129,751,204 字节，SHA-256 `d618b5c38c4ab71dc4065fced771c5f71ced4458a98fd0a8dfafe54da78fb7bb`。内置 Python 3.12.13、Node 24.22.1 和 Pi 1.4.0；仍未签名、公证或完成独立净机验收。

相对 v35，904 份公开来源只有 ChatPage 的复制回调变化，60 项构建输入不变。缺少 Clipboard API 或写入拒绝时使用现有“复制失败”，写入完成后才显示“已复制”。没有增加界面说明、修改样式或改变订阅／自定义接口。行为及 TDD 边界见[复制回复验证](2026-10-31-reply-copy-fix.md)。Root 完整前端回归 589 passed／0 failed／0 skipped，随后 `tsc -b && vite build` 退出 0。

新 source authority SHA 为 `293603b84bd18ccc937c4eee046ffc59001b2ba08158929f6137a73fc0e10fe5`；新前端 receipt SHA 为 `4a8e8e114206ff910b293ee89d60e5a51c4da994a3238cb2f10ea0ced7018c05`，绑定 100 份前端来源及 13 份输出。旧 13 份输出和旧 receipt 已先复制保留。打包使用原固定 Python 归档／库存／26 个离线 wheel 及 v35 同一 Pi runtime，未下载新依赖。首次以旧默认输入目录调用在创建输出前因缺少来源元数据失败；现场确认新目录不存在后，使用逐项通过来源校验的 `/private/tmp/easel-python-v28-inputs-20261028` 完成构建，两份日志分别保留。

独立公共包核验 10 组全部通过：904 来源／60 输入、804 份包内非前端来源、15,402 份完整资源库存、9 个内部链接、100／13 前端 receipt、11,931 份 Pi 记录与 v35 一致、provenance，以及 ZIP 的 36,118 个唯一安全名称、全部 CRC 和 15,407 个精确 payload。Root 阅读完整成功 checker 和报告后再次计算 ZIP SHA，与独立结果相同。独立 package receipt SHA `cd7e2c2da7eca7a90705eddc97ac8e06401019f05f1e6fc5665d6d1fc28e51df` 已归档在 `outputs/_run/workbench-v36-delivery-20261031/validation/package.safe.json`。旧 v35 ZIP 的 SHA `984cc3060091a00babcfdd6f90205e5b92ab698a24adf3ae7321797ffec975d9` 保持。

原激活脚本存在已确认的保护缺口：将 `/api/settings/harness.locked` 误作任务空闲标志，而该字段实际只表示 Agent 被环境固定；旧 owner stop 接口也没有完整忙状态拒绝。因此本轮没有凭该字段自动停止原实例。Root 已向用户提供新包并请求确认当前无任务后正常切换，或继续保留 v35。后续激活候选必须由明确用户答复绑定新 ZIP、source、原工作区及本次 stage 后才可运行；不能用默认选项或等待时间代替答复。

本次证据不包含真实系统剪贴板、IME、VoiceOver、模型／工具／扣点、付款或生产服务操作。B2、F2、AV、完整恢复及其他交付门继续使用各自记录；不将这个界面增量当作全部目标完成。
