# 文档索引

先看 [当前状态](STATUS.md)。下面分两类：**现行文档**随代码一起维护；**历史留档**只记录当时的计划与验收，不再修改，内容与现行文档冲突时以现行文档为准。

## 现行文档

| 主题 | 文档 |
| --- | --- |
| 项目现状、部署位置、待办 | [STATUS.md](STATUS.md) |
| 已做出的关键决定 | [决策记录](decisions/README.md) |
| 架构、开发、部署、运维、路线（2026-11-05 交接快照） | [交接手册](handbook/2026-11-05/README.md) |
| 执行底座（Pi、Codex、OpenCode、ACP、仅聊天） | [agent-harnesses.md](agent-harnesses.md) |
| 技能规范、能力地图、提示词层次 | [SKILL-SPEC.md](SKILL-SPEC.md)、[skill-function-mapping.md](skill-function-mapping.md)、[prompt-stack.md](prompt-stack.md) |
| 已知问题 | [known-issues.md](known-issues.md)、[English](known-issues_EN.md) |
| 桌面打包、应用内更新与发布 | [packaging/pi/README.md](../packaging/pi/README.md) |
| 异地备份、恢复演练、飞书告警 | [services/platform/deploy/backup/README.md](../services/platform/deploy/backup/README.md) |

### 中央服务

| 主题 | 文档 |
| --- | --- |
| 服务总览、依赖、数据库角色 | [services/platform/README.md](../services/platform/README.md) |
| 运营后台 Console | [CONSOLE.md](../services/platform/CONSOLE.md) |
| 订阅文本执行 | [EXECUTION.md](../services/platform/EXECUTION.md) |
| 订阅媒体 | [MEDIA.md](../services/platform/MEDIA.md) |
| 支付、订单、退款 | [PAYMENTS.md](../services/platform/PAYMENTS.md) |
| 账号与身份连续性 | [IDENTITY_MIGRATION.md](../services/platform/IDENTITY_MIGRATION.md) |
| 部署准备与切换 | [deploy/README.md](../services/platform/deploy/README.md)、[三机分工运行手册](../services/platform/deploy/SPLIT-DEPLOYMENT-20261024.md)、[健康检查](../services/platform/deploy/HEALTH-WATCH.md) |
| 核算恢复处置 | [platform/reconciliation-r2-deployment.md](platform/reconciliation-r2-deployment.md) |
| Logto 与会员额度迁移 | [platform/logto-migration-runbook.md](platform/logto-migration-runbook.md) |
| 媒体网关与 New API 客户端 | [媒体网关设置](platform/media-gateway-settings.md)、[图片](platform/new-api-image-client.md)、[音乐与语音](platform/new-api-media-client.md)、[视频](platform/new-api-video-client.md)、[Gemini TTS](platform/gemini-tts-new-api.md) |
| New API 参考与评估 | [设计参考](platform/new-api-design-reference.md)、[插件评估](platform/new-api-plugin-evaluation.md) |
| 开发平台 v1（已被中央 v2 取代） | [platform/README.md](platform/README.md) |
| 媒体备份 B1（已冻结，见决策 0001） | [platform/media-backup-b1.md](platform/media-backup-b1.md) |

## 历史留档（不再维护）

| 目录 | 内容 |
| --- | --- |
| `superpowers/plans/`（98 份） | 2026-10 至 11 月的实施计划 |
| `superpowers/specs/`（40 份） | 设计方案，部分从未实施或已冻结 |
| `superpowers/reports/`（3 份） | 阶段报告 |
| `validation/`（206 份记录与 assets 证据） | 每次改动的验收记录、截图与回执 |

这些文件引用的 `outputs/_run/` 证据只在当时的本机存在。文档路径校验（`scripts/validate_document_paths.py`）不检查这两个目录。新的进展写进 [STATUS.md](STATUS.md)，新的决定写进 [决策记录](decisions/README.md)，不再新增单次验收文档。
