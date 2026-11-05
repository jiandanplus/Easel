# M3 r2 冻结包与部署预检计划（仅本地交付）

本轮只构建中央候选 wheel、校验其来源并交付预检材料。没有 SSH、上传、迁移生产数据库、修改服务或调用真实供应商。当前已知生产为 f50…／`20261027-images-m1-1-size-v2`，仅到 016；本计划中的后续命令尚未在生产执行，需要独立发布授权和受审环境参数。真实 mixed DR 当前 failed、models 已恢复、待诊断，不能把本地候选或之前 R2 恢复成功作为新的恢复通过证据。

## 1. 发布对象与证据

- 冻结来源：`/private/tmp/easel-m3-final-central-final-20261028/input-source-snapshot.tar.gz`，SHA-256 `657d8540c22dfcd1e65724580a4a777ca21ac2663bb7a2e7c6d168931ff12ffb`。
- 423 输入 manifest SHA-256 `a0881006646406dbabd78f112f7b6506c1bafdcb6b205a1a619d5edf434bbeed`。独立解包后的全部 423 文件构建前后匹配；不从当前工作树取中央代码，未纳入后续 Incho 协议。
- wheel：`/private/tmp/easel-m3-r2-wheel-20261028/dist/easel_central_platform-0.1.0-py3-none-any.whl`，SHA-256 `936b7cb181b60a9fae9c0b9f868ed8ea0a1df081d2f51d8b77ed865333be54f1`。
- 保留原项目版本 `0.1.0`，所以**必须按 wheel SHA／独立发布目录选择候选，不能只按版本号安装或判断已升级**。
- 使用既有 [最终全中央证据](../../validation/2026-10-28-m3-r2-central-final.md)：1794 passed／7 skipped／0 failed／0 errors；本轮没有重跑完整套件。7 项恢复 skip 仍不能算通过。另见 [r2 独立定向审查](/private/tmp/easel-m3-independent-review-r2-20261028/review.md)，限定本地候选 APPROVE。
- 本轮的实际打包和离线检查见 [打包收据](../../validation/2026-10-28-m3-r2-wheel-packaging.md)。它不覆盖 Linux 目标宿主、生产权限、真实渠道费率、备份恢复或供应商验收。

## 2. 构建补充输入与依赖

冻结 tar 已包含 `services/platform/pyproject.toml` 和 `uv.lock`。pyproject 的 force-include 所需 Console dist 与 Pillow 许可证未在 423 输入内，因此单独从已留存旧 wheel 提取，未静默引用活动工作树：

- 来源 `/private/tmp/easel-media-m1-wheel-20261027-review-fixes/easel_central_platform-0.1.0-py3-none-any.whl`，SHA `b070d12f7c48f65709ebdfb295a76a2e1e45b79b612404893e5e541f00d1dfd9`。
- 三个 `console_static` 文件和 `Pillow-12.3.0-LICENSE.txt`；每文件 SHA 见交付目录 `supplemental-inputs.json`。这些属于历史已记录打包资源，不声称包含本次中央测试未覆盖的新 Console 前端。
- 构建工具 Hatchling 1.32.4 满足冻结 `>=1.27,<2`，从现有本机缓存使用；工具版本记录在 `build-tools.json`。未下载或升级应用依赖。
- 运行 Python 必须 `>=3.12,<3.13`。26 个当前平台运行依赖闭包与冻结锁文件及本地测试环境版本逐一一致，见 `runtime-dependencies.json`／`runtime-requirements-pinned.txt`。Windows 条件依赖 colorama／tzdata 在本机不存在，不冒称缺依赖。
- wheel 的 `py3-none-any` 只表示本包为纯 Python；Pillow、psycopg-binary、cryptography、cffi、pydantic-core 等仍需适配目标 Linux／架构的二进制依赖。当前交付不含这些目标机依赖 wheel；发布前须独立准备锁定 wheelhouse、记录哈希、验证冻结 uv.lock 的目标平台 marker 闭包，禁止现场无约束升级。

后续候选环境安装（独立新目录，依赖 wheelhouse 已验证；不得就地覆盖旧环境）：

```sh
rtk proxy "${M3_PYTHON:?}" -m pip install --no-index --find-links "${M3_WHEELHOUSE:?}" -r "${M3_BUNDLE:?}/runtime-requirements-pinned.txt"
rtk proxy "${M3_PYTHON:?}" -m pip install --no-index --no-deps "${M3_BUNDLE:?}/dist/easel_central_platform-0.1.0-py3-none-any.whl"
rtk proxy "${M3_PYTHON:?}" -m pip check
rtk proxy "${M3_PYTHON:?}" "${M3_BUNDLE:?}/preflight.py"
```

`M3_PYTHON` 必须是新候选 venv 的绝对路径。安装后预检检查 98 个 payload 文件 SHA、所有 Python／SQL 文件集合及 Python 3.12；若混入其他协议、源码版本或少文件即失败。需在发布记录中绑定 venv、wheel SHA、依赖 wheelhouse SHA 与目标平台。

## 3. 迁移前置门槛与准确顺序

迁移前必须结束当前失败的真实 mixed DR 诊断，取得**完整匹配当前非空媒体状态**的可认证备份及 fresh restore 证据。备份须覆盖数据库完整 schema／函数／触发器／约束／owner／ACL／行／sequence、实际媒体原字节与 registry、明确缺失 staged key 和配置权限。不能使用旧仅 016／32 MiB 演练脚本宣称支持 019 或常规大媒体备份；其固定 SHA 和原失败记录保持。

在发布窗口明确暂停新派发及其他资金写入者，保存活动任务、attempt、hold、receipt、wallet 和 blob 的私有快照；原 unknown 不取消、不释放 hold、不重新 POST。停旧 worker 后再做冻结窗口备份。没有可恢复窗口和写入协调方案时不迁移。owner 凭据仅由既有私有凭据通道注入一次性进程，不在命令参数、日志或长驻 API／Console 环境中出现。

使用候选 wheel 的只读预检；配置明确 schema 与实际两个独立角色名，`CENTRAL_MIGRATION_DATABASE_URL` 指向同一目标库的 schema owner：

```sh
rtk proxy "${M3_PYTHON:?}" "${M3_BUNDLE:?}/preflight.py" --database pre016 --schema "${CENTRAL_DB_SCHEMA:?}" --api-role "${M3_API_ROLE:?}" --console-role "${M3_CONSOLE_ROLE:?}"
```

预检使用 READ ONLY 事务、5 秒连接超时和 10 秒语句超时，要求 current_user 为指定 schema owner，001–016 的文件名／checksum 集合完全一致，检查角色危险授权。它不迁移、不写权限、不打印 DSN。**本轮未连接数据库执行此模式**；上线前应先在受控恢复副本实际运行，再在获准的生产窗口执行。元数据存在和 checksum 不能替代完整 DDL 与恢复副本的约束行为核对。

生产仅到 016，因此候选必须依序应用 **017 → 018 → 019**；不能因为此次称 M3 而跳过 018：

| 文件 | SHA-256 | 变化 |
| --- | --- | --- |
| 017_voice_stages.sql | `c1292c5aa13f04e1ef72083766e6404e900fc37c1dbe43b88a2e9eedc76122e8` | 新增 voice steps、step observations、registration proofs；阶段／归属／登记终态约束与不可变保护 |
| 018_media_async_work.sql | `6c7f2ce777064d8a747c6de4fac9b33b0e77792d3a82bbc1a5ab44bf4b1b8496` | 新增 work、observations、sources；原关联复合唯一约束、队列索引及 async 完整性守卫 |
| 019_media_voice_runtime.sql | `f52ba0c5d4c83f3f39b802de93c1d00667d46458f3d6b20b1d09c7735291ef66` | 扩展 work adapter 闭集；新增不可变 protocol facts、严格数值函数和事实／attempt／binding／真实 response 关联守卫 |

完整 001–019 SHA 见 `migration-sha256.json`；001–016 与 018 原字节均保留。后续执行：

```sh
rtk proxy "${M3_PYTHON:?}" -m platform_service migrate
rtk proxy "${M3_PYTHON:?}" -m platform_service grant-runtime --role "${M3_API_ROLE:?}"
rtk proxy "${M3_PYTHON:?}" -m platform_service grant-console --role "${M3_CONSOLE_ROLE:?}"
rtk proxy "${M3_PYTHON:?}" "${M3_BUNDLE:?}/preflight.py" --database post019 --schema "${CENTRAL_DB_SCHEMA:?}" --api-role "${M3_API_ROLE:?}" --console-role "${M3_CONSOLE_ROLE:?}"
```

这些是**待授权执行**命令，不是本轮执行记录。API CLI 使用 `CENTRAL_MIGRATION_DATABASE_URL`，Console grant 使用 `CENTRAL_CONSOLE_MIGRATION_DATABASE_URL`，同时需要既有有效但不输出的 API／Console Settings。两套 owner DSN、runtime DSN 与显式 schema 必须指向相同受审目标；Console CLI 另要求 owner session 身份等于 current_user。`migrate` 用 schema 维度事务 advisory lock、校验历史 checksum，并在一个事务内执行新增迁移；DDL 失败应回滚，不通过手工补写 schema_migrations 规避。

## 4. 完整新增表与权限边界

从生产 016 到本候选新增七张表：

| 表 | API runtime | Console |
| --- | --- | --- |
| media_voice_steps | SELECT、INSERT；无 UPDATE／DELETE／TRUNCATE／REFERENCES／DDL | 无任何表／列权限 |
| media_voice_step_observations | SELECT、INSERT；同上不可变 | 无 |
| media_voice_registration_proofs | SELECT、INSERT；同上不可变 | 无 |
| media_work | SELECT、INSERT；仅 phase、due_at、lease_token、lease_until、polls、failures、last_safe_code、updated_at 可 UPDATE | 无 |
| media_observations | SELECT、INSERT；不可变 | 无 |
| media_sources | SELECT、INSERT；仅 location、metadata、artifact_id、updated_at 可 UPDATE | 无 |
| media_protocol_facts | SELECT、INSERT；不可变，严格已声明单位的事实，不代表价格 | 无 |

全量 ACL 精确清单从已安装冻结包的 `permissions.py`／`console_permissions.py` 导出至 `acl-contract.json`，包括既有表、列和 sequence；不采用 `GRANT ALL` 或未来表自动继承。Console 对既有 media_* 同样无权，不可向它开放私有 voice ID、provider task、body、signed URL 或素材。

API 与 Console 都不能是 DB／schema／表 owner，不能继承或 SET ROLE 到 owner／超级用户／危险系统角色；检查 current_user、session_user 及所有可达角色。迁移 owner 不进入长驻进程。API 仅原两条 sequence USAGE，Console 仅 audit sequence USAGE。public／父角色及遗留列授权仍须审查；权限 helper 失败即停止，不临时放宽。`grant-runtime` 已包含 voice/facts 表，无需再调用另一个宽授权脚本；`grant-console` 会清除独立列授权后重新施加既有闭集。

post019 预检检查全部 API 正向授权、两个角色的安全边界、voice 最小权限、26 个新增触发器存在且启用、7 个新增函数存在、没有未验证约束；仍须在受控恢复副本对照冻结 SQL 检查函数内容、触发器定义／deferred 属性及完整原始 DDL。019 替换的是 `media_work_adapter_check`；其余原 M2 018 字节不能修改。新增七表、关联到既有 media_voices／media_terminal_evidence／media_artifacts 的守卫、全部函数及索引／ACL 都必须进入新备份恢复范围。

## 5. 文件系统、解码与服务前提

在**实际 API Unix UID 与实际 systemd 沙箱**中检查，而不是仅 root shell：媒体根目录须为绝对规范路径、归属 API UID、0700；原图／blob 为0600。私有 media config 为 API UID 所有的普通文件、0600，不能符号链接。保持 API 现有精确媒体目录写入授权；不扩大根目录或 Console 的 `ReadWritePaths`。Console 不应能遍历0700媒体目录或读取供应商配置。预检 metadata 模式不创建文件，因而不能证明 systemd 的只读挂载已允许写入；需在受控新目录先验证独占临时文件写入／fsync／删除，再仅在授权窗口核验生产同等边界，不能以 `os.access` 代替实际写入验证。

ffmpeg、ffprobe 不随本 wheel 分发。AVTools 要求显式绝对路径，解析后的普通可执行文件由 root 或 API UID 所有，group／other 不可写；两个二进制版本首行必须与配置的同一个版本串严格匹配。目标 Linux 的二进制 SHA、动态库依赖、架构、许可证单独登记。运行时不走 PATH 猜版本，也不下载工具；缺任一条件能力 closed。执行子进程需保留受限 CPU／FD／文件大小及 Linux 512 MiB 地址空间，systemd 还要允许执行该固定文件及受限临时目录写入；不要放宽 URL/CDN 或网络协议白名单。

在受审 API UID／服务沙箱下运行以下只读元数据／工具版本检查（路径由部署记录明确指定）：

```sh
rtk proxy "${M3_PYTHON:?}" "${M3_BUNDLE:?}/preflight.py" --filesystem --media-root "${M3_MEDIA_ROOT:?}" --media-config "${M3_MEDIA_CONFIG:?}" --av --ffmpeg "${M3_FFMPEG:?}" --ffprobe "${M3_FFPROBE:?}" --av-version "${M3_AV_VERSION:?}"
```

还必须在该实际 Linux 沙箱完整解码本地合成 AV fixture，验证 worker 资源上限、临时文件与输出；本轮仅在 macOS 本机通过 wheel 内真实 WAV 全解码，不外推 Linux 成功。

## 6. 切换、验证和回滚边界

1. wheel 可交付不代表模型可开放。首次获准发布也先保持新增音频／视频／音乐／声音未配置或 disabled；不更改现有图像模型／钱包／旧 pending。缺真实费率或明示 meter 的能力 closed，旧 DashScope enrollment 因缺安全素材 producer 继续 closed。不能加零价兜底、伪造 usage 或别名登记。
2. schema／ACL 预检后才允许起新 API、Console 和单个受控 worker。长驻环境清除两套 owner DSN。只读 health、已有账户、目录与原图下载核验，保持两张原图 hash 和旧 pending／hold 不变；未获得独立真实请求授权时不发任何新供应商 POST。
3. 声音 POST 链受原 dispatch+5 分钟和单次绝对墙钟约束；只有原任务 GET／已完成登记 finish 可在原24小时内恢复。unknown 保留 hold／permit；切换、重启和回滚都不创造新 POST 许可或新预算。
4. **迁移事务尚未提交**：DDL 出错由事务回滚，验证 schema 仍精确016，保持旧服务候选不变，保留失败日志。
5. **已提交017–019但未切换／未产生新数据**：没有提供 down migration。禁止直接 DROP 七表或删 schema_migrations；旧 f50 在019数据库上的运行兼容性未验收，不能承诺只切二进制即可安全回退。优先停派发并做受审 forward fix；若需要回退016，必须使用已验证冻结窗口备份、完整恢复DB／blob／配置到新目标并复核，再授权切换。
6. **已产生新任务或账本写入**：不能恢复旧备份丢弃新增付款／hold／attempt／stage／receipt。暂停新派发、保留最新证据与所有未知任务，制定包含新状态的恢复／前向修复方案；任何原ID查询维持现有许可。未完成此方案不重放请求、不人工置成功、不清理历史。

当前未满足的生产门槛：真实 mixed DR 失败诊断与新范围恢复；Linux依赖wheelhouse／解码工具／沙箱；目标 schema／ACL 实查；真实渠道与价格合同。由后续独立受审发布处理，本轮不宣称迁移、恢复或生产能力完成。
