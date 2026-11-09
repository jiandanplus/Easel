# Easel 当前状态

更新：2026-11-09。这是项目现状的唯一入口；与历史计划、验收记录冲突时以本文为准。每完成一项交付就更新本文，不再为单次改动新建验收文档。

状态用五级说明，不混用：**源码**（代码在仓库）、**测试**（自动化测试通过）、**隔离验证**（本机或临时环境实测）、**已部署**（在生产或用户机器上运行）、**正式发行**（签名公证并对外发布）。

## 1. 现在能用什么

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| 桌面工作台（三栏 UI、五套主题、内置 Pi） | 已部署 | 用户本机 v47，日间默认主题 B，对外名称“优创”（[决策 0009](decisions/0009-product-name-youchuang.md)），独立 Mac 窗口（[决策 0008](decisions/0008-native-mac-window.md)），服务在 `http://127.0.0.1:17868` |
| 应用内更新 | 已部署 | 设置 → 版本更新；签名清单 + 哈希 + 库存校验，失败自动回滚；公开更新源目前是 GitHub Release `desktop-v40`；11-06 本机 v38→v40 实际更新成功 |
| 桌面 v47（独立窗口 + 优创品牌 + 日间主题 B + 蓝色图标 + 断网不掉登录 + 兑换码） | 本机已部署，未公开发布 | 提交 `a45f77b` 构建（`~/Easel-build/releases/pi-portable-20261107-v47`，安装包 `c3e0a663…`）；v46 由 `95752b1`、v45 由 `aaea2c7` 构建，已手动装到本机；清单未签名，发布前要跑 `publish_update.py`。v41 的隔离演练含 v40→v41 应用内更新 8 秒完成，文件选择、确认框、下载、复制、外部链接、登录弹窗实测正常。公开 Release 被权限系统拦下，最新公开版仍是 v40 |
| 登录注册页品牌 | 已部署（2026-11-06） | 生产 Logto 已换成优创标识、主题 B 的冷白蓝配色、中文标题，语言固定简体中文（`services/platform/deploy/login-branding/`）；改前备份在 `/root/easel-login-branding-20261106/before.sql`；底部 “Powered by Logto” 受开源版规则保护，保留 |
| 中央账号、会员、共享点数钱包、私有 Console | 已部署（2026-11-06 升级后巡检正常） | 生产 154；当前发布 `20261107-redemption-v1`，迁移到 026；回滚到上一版：用 `/root/easel-redemption-20261107/91-*.before.conf` 还原两个 unit 的 `91-p3-media.conf` 后重启。旧版 Console 启动检查不认识新增权限，回滚后要先用旧版执行一次 `grant-console` |
| 订阅文本模型 | 已部署 | 网关迁到 154 后 11-06 DeepSeek Flash 实测通过；图片模型暂无网关渠道，不可用 |
| 自定义接口渠道 | 已部署 | 用户自配供应商，不经中央钱包 |
| 社媒账号登录与发布（7 个平台） | 源码 + 测试 | 依赖各平台页面，改版易失效；小红书自动发布有风控风险 |
| 微信 / 支付宝付款 | 源码 + 隔离验证 | 按用户决定保持关闭 |
| 订阅媒体 M2/M3/M4（视频、音乐、语音） | 代码已部署，模型未开放 | 2026-11-06 随 P3 上线，维护许可为本地模式（[决策 0006](decisions/0006-media-maintenance-optional.md)）；视频、音乐、语音模型尚未发布定义，需逐个真实验收，见 [P3 清单](../services/platform/deploy/P3-MEDIA-RELEASE.md) |
| 中央统一模型和订阅套餐（[决策 0014](decisions/0014-subscription-catalog.md)） | 源码 + 测试，待上线 | 迁移 027。后台“模型”页统一管理文本、生图、改图、视频、音乐、语音合成、语音转文字，可添加模型、改单价、设试用名单、启停；“发布套餐”可选可用模型、总点数、天数、并发、默认倍率和单模型倍率。倍率跟点数批次走，单价只影响之后的调用。价格由用户上线后在后台设置。026→027 干净安装演练通过 |
| 六类接口在后台配置（[决策 0015](decisions/0015-console-provider-configs.md)） | 源码 + 测试，待上线 | 迁移 028。“接口与模型”页按对话与脚本、语音转写、配音、生图、视频、音乐分页，填写地址和 Key 后自动生效并检测。Key 用公钥加密，后台读不回。 |
| 运营后台改进第一批 | 已部署（2026-11-07） | 用户列表加已用点数、调用次数、最近使用，可筛选、排序、导出；用户详情分概览、调用记录、额度流水、登录记录、后台操作页签；新增“数据统计”页（近 7/30/90 天，只计正式点数）。只读，发布后账目零变化。第二批 11-07 也已上线（迁移 025，[决策 0012](decisions/0012-console-user-management.md)）：停用或启用账号、强制下线、备注和标签、按已发布套餐发放（上限 100 点、365 天，只限管理员），发布后账目零变化。第三批 11-07 也已上线（迁移 026，[决策 0013](decisions/0013-redemption-codes.md)）：管理员在“兑换码”页按套餐批量生成一次性兑换码（只存摘要），用户在桌面 v47“账号与订阅”里兑换；“高级 → 后台账号”可在网页批准运营者和只读查看者。发布后账目零变化，桌面经中央兑换的完整链路已用无效码验证 |
| 运营后台（第二、三期） | 已部署（2026-11-07） | 用户列表和详情显示用户名、邮箱并可搜索（用户名在本人下次登录后才有）；管理员可在用户详情发放固定所有者额度（100 点、365 天，[决策 0011](decisions/0011-console-owner-allowance.md)）。发布后账目快照零变化；Console 角色可执行发放函数，runtime 不能 |
| 运营后台（第一期） | 已部署（2026-11-06） | 11 个按数据表分的页面改成 5 个按任务分的入口：今天、用户、用量与费用、模型与套餐、操作记录，其余收进“高级”；用户详情直接显示余额、套餐和最近使用；品牌改为优创 |
| 媒体平台承担（迁移 021） | 已部署并已使用 | 10-27 那笔 `81a6155d…` 已于 11-06 平台承担：任务 `waived`，预留 1,000,000 点释放，客户不扣点，存储预算释放（[决策 0007](decisions/0007-media-writeoffs.md)）。同日文本任务 `bdbc4509…`（10-28 上游报错，9728）也用 `execution-writeoff-*` 平台承担；生产现已没有待核算预留 |
| 异地备份、恢复演练、飞书告警（P1） | 生产已部署（2026-11-06） | 154 每日备份改为只用公钥封装（drop-in `sealed.conf`），每小时同步到 R2 `easel-backups/central`（90 天锁定、400 天过期）；首次恢复演练通过（中央库 75 表、身份库 79 表、账本核对通过）；Mac 端每小时 R2 检查、每周一 10:00 恢复演练已加载；11-06 起每小时检查也读取服务器巡检结果并推飞书（只推错误级），并检查网关备份新鲜度，每周演练同时恢复网关库 |
| B2/B3 媒体备份恢复、AV 自建构建 | 冻结 | 代码与测试保留，不再开发或部署（[决策 0001](decisions/0001-freeze-b2-b3-av.md)） |

## 2. 部署与运行位置

| 对象 | 位置 |
| --- | --- |
| 用户工作台程序 | `~/Applications/Easel/current -> v47`（v36–v46 保留用于回滚；v36–v40 的 `Contents/MacOS` 已换成窗口外壳，从旧路径或系统搜索打开都会转到当前版本，原脚本备份在 `~/Easel-build/old-launcher-v*`）；`Start Easel.command` 现在只是打开 App |
| 用户工作区 | `~/Library/Application Support/Easel/workspaces/main`（2026-10-30 备份恢复；原 `/private/tmp` 工作区已被系统清理） |
| 工作台日志 | `~/Library/Logs/Easel/workbench-17868.log` |
| 桌面构建输入与产物 | `~/Easel-build/python-inputs`、`~/Easel-build/pi-runtime-v37`、`~/Easel-build/releases/` |
| 更新签名私钥 | `~/Easel-build/release-signing/update-signing-key.pem`（仅本机，需另行备份） |
| 备份解密私钥（新） | `~/Easel-build/backup-keys/backup-private.pem`（仅本机，需另行备份；公钥在 `services/platform/deploy/backup/`） |
| 恢复演练与 R2 监视 | `~/Easel-build/backup-drill`（`rclone.conf` 为只读令牌；launchd 任务已加载，日志在 `~/Library/Logs/Easel/backup-{watch,drill}.log`） |
| 中央服务、Console、Logto、两个 PostgreSQL | 154.217.245.179（`ssh easel-zhiyin`），详见 [部署手册](handbook/2026-11-05/03-deployment.md) |
| New API 网关 | 154 上 `/srv/easel/newapi`（2026-11-06 重新部署并切换，见 [说明](../services/platform/deploy/newapi/README.md)）；管理页 `https://api.drawbookai.cn/console`，先过一道访问密码（Mac 上 `~/Easel-build/newapi-console-access.txt`），SSH 转发 18887 作备用；11-06 按用户要求，域名网站和公网证书改由 1Panel 管理（OpenResty 在 18480/18443，不对外；80/443 仍归主机 nginx，账号和登录在上面）；当前只有 DeepSeek 渠道；火山引擎 115.190.211.112 已停用不续费，旧渠道数据未能取回 |
| 生产备份 | 154 每日封装备份 + R2 异地副本，已恢复演练；旧的 23 份备份只能用旧私钥打开，已另存到 `~/Easel-build/backup-keys/transfer-private-legacy.pem`（仅本机，哈希与服务器一致），服务器上的 `transfer-private.pem` 暂留 |

## 3. 代码与质量

- 分支：接手后的全部工作在 `claude/takeover-baseline`（已推送 `jiandanplus/Easel`）。尚未合并到 `codex/ui-redesign` 或 `main`；主目录仍保留前任未提交的同内容工作区。
- 结构：`web/app.py` 只保留启动、中间件、静态页、状态、画像、技能与对话执行引擎核心（1,913 行）；各领域路由在 `easel/routes/`（[决策 0004](decisions/0004-web-routes-by-domain.md)）。中央服务在 `services/platform/`（独立 uv 项目）。
- 测试（2026-11-06 本机）：根目录 2704 passed；前端 597 passed；中央服务非 B3 单进程 3237 passed（真实 PostgreSQL；原先因 B2 耦合在 CI 中排除的 2 个图片测试已修复并恢复），B3 35 个文件逐个进程全部通过。
- CI：`.github/workflows/ci.yml` 已含中央服务 job，PR [jiandanplus/Easel#1](https://github.com/jiandanplus/Easel/pull/1) 已开（暂不合并）。2026-11-06 首次运行时因 **GitHub 账号账单问题被锁定，任务未启动**，需账号所有者处理后重跑。

## 4. 待办与卡点

| 事项 | 卡在 | 下一步 |
| --- | --- | --- |
| P1 收尾 | 服务器旧私钥暂留 | 已另存到 Mac；需要时由你手动删除 `/root/easel-migration-20261024/transfer-private.pem` |
| 桌面签名公证 | Apple 开发者身份 | — |
| 订阅媒体开放（P3） | 升级与核销已完成（11-06）；权益已恢复（所有者额度 100 点、365 天，文本和图片，[决策 0010](decisions/0010-owner-allowance.md)；11-06 用户改用另一账号，该账号也发了一份 `97c8b97c…`，原账号额度保留）；图片回归被下面的网关故障挡住 | 回归通过后，视频、音乐、语音逐个真实验收（产生供应商费用，逐个确认）；明细见 [P3 上线清单](../services/platform/deploy/P3-MEDIA-RELEASE.md) |
| 模型网关收尾 | 11-06 已切到 154 并验收：DeepSeek Flash 真实对话成功，1 条消息产生 5 次调用，网关记录与中央账本一一对应，扣 8143 微点，无预留和待核算；测试预留已核销（`94c667fa…`）；网关库每日封装备份到 R2 `newapi` 前缀并已恢复验证 | V4 Pro 未实测；图片需用户添加渠道后再回归。无渠道时图片请求返回 503，后台按“结果不明”挂起预留，累计 3 笔会挡住所有新请求（`credits.py` 的 `pending_reconciliation_limit`）；防护是把 `media.json` 的 `mode` 改为 `disabled` 并重启中央服务，用户 11-06 决定暂不关闭，图片功能保持现状 |
| 创作者价值（P4） | 用户确定最重要的创作流程 | — |
| 合并策略 | 用户决定 | 开 PR 让 CI 真正运行，再决定合入哪里 |
| 商业付款、第二产品 | 商户信息、产品定位 | — |

## 5. 文档怎么找

见 [文档索引](README.md)。历史计划与验收记录（`docs/superpowers/`、`docs/validation/`）只作留档，不再维护。
