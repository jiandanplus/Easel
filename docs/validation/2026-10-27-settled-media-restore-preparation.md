# 已结算图片的非空恢复准备

当前结论：**本机合成的已结算非空恢复已通过；尚未上传或执行新的生产非空 DR。** 先前真实 `cae1…` 演练只覆盖初始空媒体目录，不能当作 M5 两张实际图片的恢复证明。初始工具 `68dccf…`、wrapper `af1906…`、旧备份 timer 及既有数据均未更改。

独立完整复核已完成：**46 passed / 1 个显式旧 handle 检查 skip / 33.73 秒**，9 个冻结输入在执行前后及写报告时哈希一致，无剩余已知限定阻断。独立回环自行生成新的合成源和恢复目标，三个专用 PG 对象均已停止保留，没有 SSH 或真实供应商调用。报告为 `docs/validation/2026-10-27-settled-media-restore-independent-review.md`，safe 回执 SHA `e4450eeb0513280077c75b60d6cb562b90866d29a8f9b5669a9207b13b48759f`。该复核只证明本机 central-only 合成流程，不替代未来 154 双库真实 M5 恢复。

## 冻结候选

| 文件 | SHA256 |
|---|---|
| `verify_settled_media_restore_drill.py` | `49df4b72db760f88022c0dd3e7b4d8e566463fb12ab1a6044a8a2b59bf7f857d` |
| `capture_settled_media_restore_plan.py` | `356667ad623b31739af2c6431312f7f5c250b9f40265edd79c453becab93b1a3` |
| `test_settled_media_restore_drill.py` | `fc919f41d67f0e563cc186e0b979ebd7b123ef98cca7536331fd57dd72ce1397` |
| `test_settled_media_restore_plan_capture.py` | `6b43e1605645b45148215d059ec588486f3cc8defd29a7bed714dbe15c8ef764` |
| `settled_media_restore_fixture.py` | `65ebab81664f1df36fda2443af637496917d5c6d804ea707a3a71155424ae51c` |
| `test_settled_media_restore_fixture.py` | `b62bacfd0d2f79db4d26a5f317b7534d58bb82037df2a6a7f5ded24ac6c9eb48` |

最小 diff 与冻结副本保存在 `/private/tmp/easel-settled-media-restore-review-20261027/`：相对原工具的 DR diff 166 行，相对原 wrapper 的 diff 42 行。数据／文件／配置安全 helper、原 v6 raw schema 比较器和七项旧 CHECK 精确目标修复保持原逻辑；没有放宽 schema、全部约束、roles／owner／ACL、全部表行、序列或财务比较。

源代码权威固定为 f50e size-v2 wheel 和 001–016。独立 `-I` fixture worker 先验证轮子及 76 个 Python／SQL 文件，再导入该 staging，避免共享 repo 的 017 被混入。

## 实际本机回环

执行：

```sh
rtk proxy env EASEL_SETTLED_RESTORE_DOCKER=1 EASEL_SETTLED_RESTORE_EVIDENCE=outputs/_run/settled-media-restore-20261027/synthetic-settled-nonempty-safe.json services/platform/.venv/bin/python -m pytest --noconftest services/platform/tests/test_settled_media_restore_drill.py -q --tb=short
```

结果 **26 passed / 1 skipped，29.89 秒**。skip 是需要显式旧稳定 handle 的额外只读检查；完整回环自行生成新专用源，已在该源和恢复目标分别实际执行同一窗口 SQL，未跳过核心 PG 或文件恢复。此前该额外非 public 检查已单独真实通过。

源为模拟 live 语义的隔离测试库，使用真实平台 internal grant／feature／媒体／账本 API 和 `httpx.MockTransport`，两笔实际供应商网络 POST 为 **0**，fake transport POST 为 **2**。两指定模型各生成并结算一笔 1 点任务，`provider_response_id=NULL`，New API typed HTTP references、terminal evidence、receipts 各2。钱包 issued10m / spent2m / available8m / held0，两预算均已释放、remaining0。两个模型在 seed 结束后 disabled，没有输入资产、文本任务或支付订单。

两张 PNG 实际编码、落盘并解码，均为 1024×1024、各 >1MB，合计 **2,101,644 字节**。数据库为 **75 表 / 529 条约束 / 82 行**。同一个导出只读 snapshot 内读取 registry、捕获文件、逻辑 dump 和完整库存，前后检查同 inode／size／mtime／内容摘要及 fresh registry／窗口状态。临时合成 RSA 私钥仅在本机内存中生成；新密文 **2,704,203 字节**落盘后重新读取并认证，随后恢复到全新无网 PG 和私有文件树。原始 schema、完整约束、全部数据、roles／owner／ACL／设置、序列和资金平衡均匹配；目标的 settled／typed 窗口、registry 与文件摘要再次匹配。11 个配置文件及其 UID／GID／mode 还原通过，坏密文拒绝。

安全回执为 `outputs/_run/settled-media-restore-20261027/synthetic-settled-nonempty-safe.json`。自有源与目标均已停止并保留，无生产数据、会话、模型或服务操作。

实现阶段先观察到 24 个窗口缺失失败，之后静止条件绿；非空 proof 的 0／1 文件及缺目标 typed 断言先失败再补齐。实际 PG 另复现了非默认 schema 中 `media_storage_pinned` 内部未限定表名的问题：外部限定函数 schema 仍不足，因此窗口查询显式 `SET LOCAL search_path` 到受配置绑定的 schema，原函数和 pinned 判定不改、不放宽。首次完整测试使用根 venv 时缺少 psycopg，在 fixture 复核前失败、尚未创建目标；改用项目既有 platform venv 后对新独立源完整通过，未安装依赖或重 seed 失败源。

fixture helper 自身另有 **7 passed / 2.93 秒**，覆盖冻结源码、完整结算和拒绝非本机／非专用库／PG 参数。新 wrapper **13 passed / 0.02 秒**，覆盖旧空 plan 不能冒充 settled、继承调用 venv、完整 plan 私有独占落盘和显式白名单。以上不是与独立 46 项重复相加的总数；独立 46 项已包含这些模块。编译、diff 空白检查通过。

## 实际两张 PNG 完成后所需的准入

1. 先由 root 确认 M5 两个实际任务均成功交付并 settled，各 amount=actual1,000,000；不能以“已请求／有图片”推断已结算。
2. root 另行批准并执行两 model 临时 disabled／idle 窗口。保持文本和登录服务运行，不改用户会话、原点数、价格、权益或 TTL；本子任务不能自行关闭／重新启用模型。
3. 新只读计划必须实际检查只有两个预期 media jobs／invocations／artifacts／blobs、每模型一笔，无输入 assets；全部 completed／available，held0、预算释放0，typed 终态与回执各2，同一 owner 归属。
4. 无 reserved／dispatched／pending reconciliation、pinned、GC token／lease、staged／gc_pending／deleted／partial 或未知文件。两 PNG 的原 expiry 在每次窗口检查时仍距当前至少30分钟，原文件997:997／0600、目录0700、单链接无符号链接、SHA和长度可稳定读取。未知状态直接拒绝，不删除文件或释放资金。
5. 源仍须是冻结 f50e／016 和对应实际进程／11配置。若先部署017或另一个 release，本工具拒绝，需重新制定覆盖范围。
6. 用新的私有目录、nonce、密文和严格 safe-plan wrapper。root 审核具体 code SHA／plan SHA、资源余量及目标／卷／unit 不存在，再单次调度恢复；失败只观察原 handle，不重派。
7. 真正非空 proof 只能由新密文重读、两个原文件恢复、目标全部行与 typed／settled 查询和独立源／目标／密文后验全部通过后生成。之后由 root 按另外批准的步骤 reenable 原模型。

这仍是明确静止的两张已结算图片的一次性演练，32 MiB 上限、逐库快照，没有跨库原子性、PITR、恢复应用登录或覆盖日常 GC／`.part` quarantine。日常备份 timer 升级另审。
