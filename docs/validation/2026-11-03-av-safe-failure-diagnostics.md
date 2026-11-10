# AV 受限失败诊断验证

2026-11-03，`codex/ui-redesign`。本批补齐未来调用的失败诊断，没有重跑原 exact-log 案例，也没有给旧失败记录补写状态码。

## 完成的行为

- HTTP 解析器增加可选数字状态观察器。完整且受限的有效状态行可以报告一个 100–599 的整数；非预期状态仍立即拒绝，不继续读取头部或正文。未传观察器时，原调用和 guard 行为保持一致。
- API 保存只读、冻结的首次失败事实：操作名、request／transport／decode／publish 阶段、状态码或未知，以及来源标记。它们不表示已发送、远端已创建或整个响应已被接受。
- 容量控制器在原取消／停止逻辑之后，以原剩余清理预算和原目录身份尽力写入 `failure.json`。诊断写入失败不跳过停止、不续期、不覆盖旧文件，也不授予重试权限。已有或执行中出现的失败标记会阻止成功接受。

没有增加响应正文、原始异常、头部、路径或命令的日志输出。新文件只包含既有 case 绑定、已知 CID 或空值、固定阶段、当地 START 尝试标记和经过验证的 API 事实；文件缺失仍表示未知。

## 回归与审查

Root 新鲜运行 HTTP、API、profile、executor、capacity 五份关联测试：**659 passed in 29.11s，exit 0**。核对了八个 live 文件及冻结副本、十个受保护文件、完整八文件 diff、最终清单和完整 pre-latch 历史归档。执行器生产变更额外检查为仅 API 源码哈希、精确记录名以及过期文档标签三处替换。

作者先保存了三项基线功能失败，再完成新用例。首轮完整测试中的旧签名断言失败也保留，并只更新了已授权的可选参数断言。

Root 在初步交接中找到并修正另一处诊断身份问题：首次快照构造失败后，后续被拒绝的调用可能把自己补记为首次失败。因果 RED 已复现；修正用独立 `_failure_seen` 标记在构造之前消耗首次失败资格，使最初的未知继续保持未知。原快照、完整交接包和失败输出已归档，最终作者测试为 659 项通过，Root 另行复跑通过。

独立审查结论：**规格 PASS、代码质量 PASS，无阻断性修改要求**。审阅者读取完整变更和测试证据，独立核对所有哈希，没有运行测试或原生探测。报告 SHA：`db358ce0b885b5b7d750064a0b041af2239599095451b2c902a3c36647259193`。

最终关键源码 SHA：

| 文件 | SHA-256 |
|---|---|
| pack_http.py | `786040a3f3b0118bc09de487d987eae4bfc457c878db6e550acf0aeaebeebea0` |
| pack_api.py | `501b68832e88a5b0eb21b480420ea6de70135238d0253fb98aded1ecf91d26fb` |
| pack_executor.py | `6280485d00f9904cda40f44de1cfd909057288e2876f695f6dffc21e0ddc6b09` |
| capacity_probe.py | `f063698a95886a36eaeaad336e389601f8fbebbd2cadf72578bb245604edfc0a` |

## 证据边界与下一步

上述结果关闭本次源码／合成协议诊断项。原 exact-log 失败的阶段和状态仍未确定，日志文件系统容量仍未测得。没有调用 Docker、供应商、钱包或生产服务；没有新建／删除／重启容器、升级旧 stage 或打包应用。

下一次实际运行须使用新冻结的 Root loader／parent，对失败标记增加接受检查，并绑定本批新源码；不能直接运行历史脚本或重放旧 once 记录。收到数字状态也不能将失败案例当作容量通过。原 worker／guardian 原生控制、产物保留、真实 pack／完整工具链、备份恢复和发行验证继续保留各自的验收门。

本批没有改动前端，原工作台登录、历史和接口配置保留，没有 commit、push 或合并。

[Root 新鲜结果](../../outputs/_run/av-diagnostics-20261103/ROOT-FRESH-COVERING5.safe.json) · [完整测试输出](../../outputs/_run/av-diagnostics-20261103/ROOT-FRESH-COVERING5.raw.txt) · [完整变更](../../outputs/_run/av-diagnostics-20261103/full8.diff) · [独立审查](../../outputs/_run/av-diagnostics-20261103/INDEPENDENT-REVIEW.safe.md) · [首次失败身份问题](../../outputs/_run/av-diagnostics-20261103/ROOT-FIRST-FAILURE-CAPTURE-FINDING.safe.md)。
