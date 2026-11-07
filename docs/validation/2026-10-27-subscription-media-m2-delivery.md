# M2 视频／音乐异步执行候选交付验证

2026-10-27，工作区 `codex/ui-redesign`。这是代码候选与隔离验证，不是生产媒体开放或真实供应商验收。

- 最新 14 文件候选 SHA256：`abb2e0efe34956c834d4a1aa744934114a4c9d6939a73e033a716c2b15411da8`。
- 最终全中央回归绑定的 212 个 Python／SQL／scripts／tests 输入 SHA256：`223a4c82831216673c4096066223ae64d2911e2e0a4dd3b1de7469cb65a0e5f6`。
- 本轮开始、结束均逐文件复验，两份清单完全匹配；`git diff --check` 通过。没有 commit、push、merge、reset 或 clean。
- 安全清单／收据：`/private/tmp/easel-m2-candidate-20261027/p1-p2-fixed-manifest.json`、`final-all-inputs-manifest.json`、`p1-p2-fixed-receipt.json`。
- 旧 `9798b204…` 清单与 1702／7 skip 结果仍保留在相邻 `manifest.json`、`receipt.json`、`report.md`，只作为独立复审修复前历史，不替代最新候选结果。

## 当前实现

首次提交路径是唯一生成 POST 所在位置：HTTP 头关联先持久，解析出原 job ID 后先持久绑定，再解析状态。已有 ID 的任务恢复只补 GET work；无 ID 的未知提交不重发。视频、Suno、DashScope 按显式 profile 分开，GET／下载与提交使用分离且有界的连接池。签名 URL 只能从同一原 ID 的可信 GET 刷新，并保持来源身份、计量与首次终态快照。

worker 仅做原 ID GET、固定 content GET 和显式允许 origin／路径前缀的 CDN GET。音乐缺下载配置、任何异步 profile 缺 AV 工具或版本不符、worker 被关闭、资源定义不全，都保持 closed。M1 图像 binding 序列化保留原语义，M2 另冻结 AV 路径／版本与下载前缀。没有新增默认模型、供应商能力、费率或密钥。

018 提供稳定 job／attempt／binding／provider scope 关联、不可变首次终态观察、稳定 source／artifact／storage 身份及租约。数据库约束拒绝未结算标 done、终态状态／hash／原 job ID／provider usage 与首次观察不一致。启动只检查 018 表与权限，不以 runtime 角色迁移。角色按显式表和列授权，真实非 owner 隔离角色已完成交付且不能重绑、改历史、删除或 truncate。

网络与解码不持资金锁。写入按 wallet → invocation → job/reservation → work → storage 的同向顺序，终态和结算都校验当前 token、原 attempt／binding／provider scope／job ID 与 deadline。generic finance recovery 不绕过异步结算租约。过期队列进入 manual，保留点数预留和存储预算；cancel 不发猜测 DELETE，不把失败／取消自动当成零收费。

输出保留原字节，经既有受限 ffprobe + ffmpeg 全解码，核验 MIME、文件数、字节、时长、像素、声道、采样率。实际 video_ms 与短音轨 audio_duration_ms 独立。Suno audio_clip 仅统计已完整解码、登记的 owned 音频，fixture 故意只给一轨；DashScope duration 按明确 usage/hash 精确转毫秒。下载重试、重启、迟到 worker 不增加生成 POST、artifact、预算消耗或 receipt。完整 .part 只有在权限／inode／大小／hash 验证后晋升；不完整 .part 保留并继续占配额。已接受任务可在退出后按冻结 scope 完成，公共读／下载仍验证当前身份。

## 两项独立 P1 的红→绿

1. 独立慢流反例：HTTP 每个 chunk 都在 read timeout 内，却能持续占用 GET，记录截止后仍阻塞 run_once。原测试 `test_worker_deadline_independent.py` 在修前实际失败。现在 execute 总时限取 `min(worker_wall_seconds, record deadline 剩余)`，默认 wall ceiling 120 秒、配置最大 300 秒；到期取消 inflight GET／解码，原 token 仅可做过期 manual 收口。claim／seed／heartbeat 的 lease_until 均封顶到记录 deadline，过期不可续。原反例转绿；仓库另有真实 PG 200 ms 记录窗口的慢流取消、manual、资金／存储 hold 保留验证。
2. 独立并发反例：正常 in_progress GET 后，原提交 60 秒 job lease 已过期；generic finance 将其转 pending／unknown，concurrency=1 的第二任务实际获准。原测试 `test_job_lease_independent.py` 在修前真实 PG 失败并记录 active=0／第二 admit 成功。现在成功 queued／running GET 与有界 heartbeat 只续仍 dispatched 的原 job，且不越记录 deadline；generic finance 识别仍有效的 query work，不因原提交协程结束移出执行准入。显式 GET 失败、无 ID、过期及既有 pending 的策略未放宽；pending 不被普遍重算 active 或强行恢复 dispatched。

独立测试文件由 reviewer 保留，本任务没有修改。修后复用原两项：**2 passed，0 failed，0.73 秒**。

## 最终验证

| 验证 | 结果 |
| --- | --- |
| M2 协议、runtime、worker、真实 PG 租约／资金／权限、真实 AV 定向 | 73 passed，0 failed，15.47 秒 |
| 原两项独立 P1 反例，修后复用 | 2 passed，0 failed，0.73 秒 |
| 冻结输入上的完整中央服务回归 | **1710 passed，7 skipped，0 failed，350.69 秒** |
| 两份 manifest 开始／结束逐文件 SHA | 全部匹配 |
| git diff --check | 通过 |

实际运行使用既有、literal loopback 的 easel_s1_test PostgreSQL 随机 owned schema；私有 DSN 内容未打印、未改变。测试命令使用已有 `.venv`：

```sh
rtk proxy env PYTHONPATH=services/platform/src EASEL_CENTRAL_TEST_DSN_FILE=<private-local-test-dsn-file> services/platform/.venv/bin/python -m pytest services/platform/tests -q
```

7 个 skip 已在不设置额外开关的独立 `-rs` 定向重读中确认（1 passed／7 skipped，0.19 秒）：

| 来源 | 数量 | 未运行原因／边界 |
| --- | --- | --- |
| encrypted restore exported snapshot | 1 | 缺显式 synthetic Docker 环境开关 |
| encrypted restore public R2 raw inventory，两参数 | 2 | 缺同一显式 synthetic Docker 环境开关 |
| settled media 的 nonpublic source handle 检查 | 1 | 缺显式 owned synthetic source handle |
| settled media 两张已结算 PNG 的 fresh restore | 1 | 缺显式 owned synthetic restore 环境开关 |
| full 016 owned registry fresh restore | 1 | 缺显式 owned synthetic Docker 环境开关 |
| mixed 两张 PNG + EROFS pending hold fresh restore | 1 | 缺显式 owned synthetic restore 环境开关 |

这些 skip 不能计为通过，也不能以本次全量结果宣称相应真实／Docker 恢复演练完成。现有捕获候选的 Python／SQL／wheel 校验项在同一定向重读中为 passed；它不代表本任务生成了新的 M2 wheel。

## 保留边界

所有供应商 HTTP 都是受控 fixture。AV 是本机真实生成、探测并全解码的字节，未做真实供应商请求或付费。没有 SSH、生产部署、真实 auth／密钥更改或 17868 包操作。生产仍为原 f50 through016；既有 017 voice 增量保留，001–016 未由本任务修改。

实际供应商渠道／能力、授权模型、费率、下载规则、Linux decoder／许可环境及 M5 真实任务闭环仍未接入。当前 download_path_prefixes 仅接受非根目录前缀和未编码规范 path；根目录 CDN 文件或编码 path 若有真实协议需求，应另行明确规则评审，不能从 fixture 路径推定兼容。

修后独立复审完成：**404 passed，1 个显式 Docker-DR 环境 skip，88.51 秒**；26 个 closure 模块前后 hash 不变，原两项 P1 均闭合，限定范围无剩余已知阻塞。它与本任务 1710／7 skip 全套分别记录，不加总。独立记录：

- [独立复审报告](2026-10-27-m2-async-independent-review.md)
- [独立复审安全证据](2026-10-27-m2-async-independent-review-safe.json)，SHA256 `b0cfecb0823baed4a5e2b3ca909cae39d215b8099d9d81fd5bfb3c6b915f1197`。

本报告的结构化安全证据位于 [M2 delivery safe JSON](2026-10-27-subscription-media-m2-delivery-safe.json)。实现、测试、14 文件候选与 212 输入保持冻结；只更新上述验证文档。
