# Easel 当前状态

更新：2026-11-06。这是项目现状的唯一入口；与历史计划、验收记录冲突时以本文为准。每完成一项交付就更新本文，不再为单次改动新建验收文档。

状态用五级说明，不混用：**源码**（代码在仓库）、**测试**（自动化测试通过）、**隔离验证**（本机或临时环境实测）、**已部署**（在生产或用户机器上运行）、**正式发行**（签名公证并对外发布）。

## 1. 现在能用什么

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| 桌面工作台（三栏 UI、五套主题、内置 Pi） | 已部署 | 用户本机 `http://127.0.0.1:17868`，v38 |
| 应用内更新 | 已部署 | 设置 → 版本更新；签名清单 + 哈希 + 库存校验，失败自动回滚；更新源为 GitHub Release `desktop-v38`（最新） |
| 中央账号、会员、共享点数钱包、私有 Console | 已部署（据交接手册，未重新巡检） | 生产 154；最后发布 `20261027-images-m1-1-size-v2`，迁移到 016 |
| 订阅文本模型、两款图片模型 | 已部署（据交接手册） | 有真实调用与扣点记录；新模型需逐个验收 |
| 自定义接口渠道 | 已部署 | 用户自配供应商，不经中央钱包 |
| 社媒账号登录与发布（7 个平台） | 源码 + 测试 | 依赖各平台页面，改版易失效；小红书自动发布有风控风险 |
| 微信 / 支付宝付款 | 源码 + 隔离验证 | 按用户决定保持关闭 |
| 订阅媒体 M2/M3/M4（视频、音乐、语音） | 源码 + 测试 | 未部署；维护许可已改为可选、默认关闭（[决策 0006](decisions/0006-media-maintenance-optional.md)），可随下一次中央发布逐个开放 |
| 异地备份、恢复演练、飞书告警（P1） | 源码 + 隔离验证 | 等用户提供 R2 与飞书后部署 |
| B2/B3 媒体备份恢复、AV 自建构建 | 冻结 | 代码与测试保留，不再开发或部署（[决策 0001](decisions/0001-freeze-b2-b3-av.md)） |

## 2. 部署与运行位置

| 对象 | 位置 |
| --- | --- |
| 用户工作台程序 | `~/Applications/Easel/current -> v38`（v36、v37 保留用于回滚）；启动/停止：同目录 `Start Easel.command`、`Stop Easel.command` |
| 用户工作区 | `~/Library/Application Support/Easel/workspaces/main`（2026-10-30 备份恢复；原 `/private/tmp` 工作区已被系统清理） |
| 工作台日志 | `~/Library/Logs/Easel/workbench-17868.log` |
| 桌面构建输入与产物 | `~/Easel-build/python-inputs`、`~/Easel-build/pi-runtime-v37`、`~/Easel-build/releases/` |
| 更新签名私钥 | `~/Easel-build/release-signing/update-signing-key.pem`（仅本机，需另行备份） |
| 备份解密私钥（新） | `~/Easel-build/backup-keys/backup-private.pem`（仅本机，需另行备份；公钥在 `services/platform/deploy/backup/`） |
| 中央服务、Console、Logto、两个 PostgreSQL | 154.217.245.179（`ssh easel-zhiyin`），详见 [部署手册](handbook/2026-11-05/03-deployment.md) |
| New API 网关 | 火山引擎 115.190.211.112 |
| 生产备份 | 154 每日加密备份，**仅同机保存、私钥同机、从未恢复演练**，P1 部署后解决 |

## 3. 代码与质量

- 分支：接手后的全部工作在 `claude/takeover-baseline`（已推送 `jiandanplus/Easel`）。尚未合并到 `codex/ui-redesign` 或 `main`；主目录仍保留前任未提交的同内容工作区。
- 结构：`web/app.py` 只保留启动、中间件、静态页、状态、画像、技能与对话执行引擎核心（1,913 行）；各领域路由在 `easel/routes/`（[决策 0004](decisions/0004-web-routes-by-domain.md)）。中央服务在 `services/platform/`（独立 uv 项目）。
- 测试（2026-11-05 本机）：根目录 2701 passed；前端 597 passed；中央服务非 B3 单进程 3229 passed（真实 PostgreSQL），B3 35 个文件逐个进程全部通过。
- CI：`.github/workflows/ci.yml` 已含中央服务 job，PR [jiandanplus/Easel#1](https://github.com/jiandanplus/Easel/pull/1) 已开（暂不合并）。2026-11-06 首次运行时因 **GitHub 账号账单问题被锁定，任务未启动**，需账号所有者处理后重跑。

## 4. 待办与卡点

| 事项 | 卡在 | 下一步 |
| --- | --- | --- |
| P1 生产数据安全 | 用户提供 R2 桶与令牌、飞书机器人 | 按 `services/platform/deploy/backup/README.md` 部署，逐步确认 |
| 桌面签名公证 | Apple 开发者身份 | — |
| 订阅媒体开放（P3） | 无（屏障已改为可选） | 构建中央候选版本，在临时环境验证后，随迁移 017–020 部署；视频、音乐、语音逐个真实验收 |
| 创作者价值（P4） | 用户确定最重要的创作流程 | — |
| 合并策略 | 用户决定 | 开 PR 让 CI 真正运行，再决定合入哪里 |
| 商业付款、第二产品 | 商户信息、产品定位 | — |

## 5. 文档怎么找

见 [文档索引](README.md)。历史计划与验收记录（`docs/superpowers/`、`docs/validation/`）只作留档，不再维护。
