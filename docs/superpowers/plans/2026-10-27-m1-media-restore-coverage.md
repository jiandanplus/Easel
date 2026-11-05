# M1.1 数据库与媒体恢复验证

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. 按任务完成，敏感执行另由 root 审核固定脚本、计划、nonce。

**Goal:** 为冻结的 M1.1 size-v2、016 迁移、私有 media.json 和受管媒体目录提供新的恢复证据，不外推 R2 证据。

**Current status:** 新 nonce `cae1ad9dffe04d9986df17e8a81647c4` 的真实初始空媒体恢复及独立后验已通过：中央75表734行、身份79表446行、016及11配置恢复严格匹配，源/进程/密文/隔离目标后验通过。safe bundle SHA `6d807131fe83544df45ab8dcc3665ebc8644c02285179abe0749b24fcf91f75f`；真实非空媒体与日常 timer 覆盖另待。

**Architecture:** 新增独立工具，复用 SHA 固定的 v6 严格数据库恢复核心。双库仍分别导出只读快照；同中央快照读取媒体 registry，在新 root 私有目录还原认证密文中配置和受管文件，核验权限及逐字节摘要；不启动恢复应用。首轮真实验收要求两个图片模型 disabled、媒体调用/任务/registry/budget 均为空、目录为空，以保持文本和登录服务运行。非空文件覆盖先由本机合成夹具验证。两张真实生图完成后另建范围明确的新 nonce 非空恢复演练。

**Tech Stack:** 现有 Python 标准库、固定 PostgreSQL 16 镜像、原服务器 RSA/AES 加密 helper；不新增服务。

## Global Constraints

- 当前分支保留，禁止 commit/push/merge；旧 R2 工具、备份服务和 timer 不改。
- 真实 secret/config/SQL/registry 行值和明文归档只留 154 服务器 root 私有内存/目录，不输出或传到本机。
- 新 release `/srv/easel/releases/20261027-images-m1-1-size-v2`，wheel SHA `f50e52511e941d3bea8e724eddbd68330e20eaa6339c5e2437b3880ca9705abf`；016 SHA `a5a1dcfdb90299ebd063b18377a900ad68e1338bb2137cba9a7010f9694d6cba`。
- 原 raw schema hash、完整约束、角色/owner/ACL/序列、全部数据和财务严格比较。只保留经审七项旧 R2 CHECK 的精确目标修复。
- 捕获上限仍 32 MiB；超过即失败，不丢弃内容或声称完整日常媒体备份。逐库而非跨库原子，不是 PITR。
- 目标 PG 顺序执行、512 MiB、0.75 CPU、128 PIDs、network none、无公开端口；成功失败均停止并保留自有对象。
- 不调用真实供应商、不发点/延期/付款，不改原服务/env/DB/凭据，不清理已有对象。

## Task 1：文件与配置边界

**Files:** 新增 `services/platform/scripts/verify_media_restore_drill.py` 与 `services/platform/tests/test_media_restore_drill.py`。

- [x] 先写测试：已登记 attached 文件完整恢复、600/700 和 owner 保留；哈希/长度/漏文件/多文件/穿越/符号链接/硬链接/错误 owner/读取中变更拒绝。
- [x] 私有 media.json 必须是固定路径、600、当前 easel-central UID；精确 storage_root，不执行 JSON 内容；配置仅入加密 member，safe receipt 只有摘要。
- [x] 每次打开目录和文件使用 O_NOFOLLOW/dir_fd，前后 fstat 身份与内容摘要一致；拒绝 `.part`、未知文件、staged/gc_pending，未来恢复这些状态另行设计 quarantine，不能静默跳过。
- [x] 目标只用 nonce 独立私有目录，O_EXCL 创建，逐 bytes/hash/UID/mode 重读核验；任何失败保留现场，绝不触碰源目录。
- [x] 新 cutover 的 2 个 main unit + 2 个固定 drop-in 连同原私有配置共 11 文件捕获/还原 bytes、SHA、UID、GID、mode；API MEDIA 由精确 drop-in 提供，不要求改旧 central.env。实际 MainPID cwd/argv/exe/MEDIA 校验，原 argv/environ 不外发。

## Task 2：016 严格恢复与首轮窗口

- [x] 冻结 v6 核心 SHA，验证目标 release 的完整 wheel Python/SQL 内容，016 checksum 与数据库 schema_migrations 全部记录一致。代码/合成验证完成，真实执行另待部署。
- [x] 两个图片模型 disabled，首轮媒体表调用/jobs/blobs/budgets 为 0 的计划、中央 snapshot 捕获及结束复验均已真实通过；blob_count0 / nonemptyfalse 如实记录。
- [x] 合成完整 001–016 DB 实际 dump/restore，包含非 public schema、完整约束、财务、非空媒体文件；原七 CHECK 精确修复后所有库存严格相等。最终 39 passed / 31.16 秒，75 表 / 529 约束 / 29 行 / 1 个 34 字节受约束合成 blob / 11 配置。
- [x] 新独立 tool SHA、最小 diff、测试结果给 root 审核；root 上传固定68dccf / af190并核哈希，新两unitactive / modelsdisabled后只读计划通过，root 核准具体a837…plan并单次启动真实 DR。
- [x] 本机 DR 独立复核与 prepare / cutover 合跑 56 passed / 0 skipped / 31.79 秒，输入 hash 不变；之后真实154同一freeze也已通过，两个证据分别保留。
- [x] 新增 server-only plan capture wrapper 与 12 项红绿测试，完整 plan 保持 154 root0600，仅显式白名单对外；不变原 DR / hash 重建合同。wrapper独立审核、上传及真实只读捕获通过。
- [x] 初次 root 的 stop + apply-disabled 被自动审批完整拒绝且未执行；后续用户通过 `call_3e06c3000db64793b08a843291a76bc3` 精确批准限定维护 / 迁移 / 恢复后启用 / 原余额真实验收，root 已独占启动原精确计划。子任务不代理该命令，不把授权当执行成功，真实 DR 仍待 root 新 unit active 和具体调度。
- [x] root 完成新服务切换 / production smoke7项全真及冻结工具上传后，限定执行 wrapper 只读计划；nonce `cae1ad9dffe04d9986df17e8a81647c4`，完整 private plan SHA `a837da51f337dbd602f4bd429316a70c62cee8d58595aca242aed8b4534274f1`，11 配置 / 16 迁移 / 两实际进程 / 空媒体闸通过，完整计划仅154 root0600，外传safe白名单。
- [x] root 核准该具体完整 plan SHA 后另行单次调度真实恢复，原InvocationID `0bbd02e7b7b94c9aae2b7ebaa938b090`，29阶段到complete，PID1 journal同Invocation成功终态；不重派。

## Task 3：真实证据与上线门槛

- [x] 只从 durable ciphertext 重新读取/认证的字节恢复；配置独立还原并核验，不启动恢复应用。
- [x] receipt 实际计算 `success=true`、`schema_016_restore_verified=true`、`media_blobs_covered=true`，真实 blob_count0；明确 `nonempty_media_blobs_restore_verified=false`。
- [x] receipt 固定 f50e candidate / size-v2 source_release，enable-proof与restore-safe字节相同、SHA `847c3161ade52fb502e1fae48416e795c9441e8896b05c90640c78b71398b6df`；未手工填布尔，后续enable由root另行执行。
- [x] 独立核对目标 stopped/保留/networknone0ports、原2PG ID/image、两真实appPID/cwd/argv/exe/MEDIA、四服务/Logto/旧timeractive、密文1393483B root600 hash匹配及11文件权限；safe bundle保存并校验serverreceipt canonical SHA。
- [ ] 两张 M5 真实图片后的非空演练和覆盖 M1 的常规备份 timer 另审，不把本轮空目录或合成证明当真实媒体恢复完成。
