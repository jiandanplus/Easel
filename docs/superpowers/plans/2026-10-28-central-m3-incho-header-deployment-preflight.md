# 中央 M3＋Incho 020＋关联头候选：独立部署预检计划

本轮仅本地打包、离线安装及合成预检／AV全解码，不连接PG、不SSH、不部署、不调账。候选来源是完整中央运行8911的431文件快照，未从活动工作树取代码；不含正在修改的Pi root schema worker或新前端。旧M3 r2 wheel `936b7cb1…`、原全量和失败证据全部保留。

## 发布身份与当前门槛

- 来源tar SHA：`54ac2460d2a912eeaaa47f0348ba67fd192965202b22db96f7080b0ec5f9731d`；431输入manifest SHA：`5e8c9f416ac13478c332e089e3e557257fc4371090bce68c7256bd812f401cae`。
- 新wheel位于 `/private/tmp/easel-central-m3-incho-header-wheel-20261028/dist/easel_central_platform-0.1.0-py3-none-any.whl`，SHA `e7af8e66cc9a34deb92b47b5bc6f22f6d7406e0e510d3199b9a768efae3f8487`。版本仍0.1.0，必须用SHA和独立发布目录识别，禁止按版本名猜测。
- 历史来源测试分列：完整8911为1895 passed／14 skipped；之后只补旧worker的Incho SDK 7 passed／0 skipped。它们不是一个合并full，原7项恢复测试未覆盖。本轮没有重跑或借用后来root schema168 SDK结果。详见 [来源完整验证](../../validation/2026-10-28-central-m3-incho-header-final.md)。
- 生产已知仍为f50…、仅016。真实mixed DR失败、models已恢复、待诊断；Linux依赖wheelhouse、AV二进制及实际服务沙箱未验收。**本候选不能直接发布。**
- 根据主任务当前提供的资金状态：**pending=2，held=1,009,728 microcredits**，包括旧图片hold 1,000,000及新文本hold 9,728。本轮没有读取生产或复核账本，不把该值当作可长期复用的窗口。
- 原恢复窗口controller硬编码了旧钱包／pending基线，已因新文本hold变化而失效。不得只替换expected数字、继续旧计划、重用旧nonce／密文或把旧失败改为成功。必须重新授权fresh freeze：冻结准确源版本、数据库／角色／配置、两笔pending的原ID／attempt／关联／hold以及全部钱包和blob库存，独立生成新的plan／nonce／备份／fresh restore目标并严格复核。旧controller、失败日志、密文及目标仍保留。

## 构建与依赖边界

431快照含冻结pyproject和uv.lock，字节与旧M3包对应文件相同，运行依赖没有升级。构建Hatchling及其依赖版本与旧M3打包一致；来源完整记录在build-tools.json。wheel的96个Python／SQL与快照逐字节一致，001–020齐全。

另4个资源从已核旧b070… wheel提取：3个Console静态文件及Pillow12.3.0许可证。来源为 `/private/tmp/easel-media-m1-wheel-20261027-review-fixes/easel_central_platform-0.1.0-py3-none-any.whl`，SHA `b070d12f7c48f65709ebdfb295a76a2e1e45b79b612404893e5e541f00d1dfd9`；逐文件来源／SHA见supplemental-inputs.json。它们不被重标为431测试输入，也不是后来桌面前端。

Python必须3.12。26个当前平台运行依赖闭包满足METADATA并匹配冻结uv.lock；runtime-requirements-pinned.txt与旧M3包逐字相同。目标Linux架构仍须先准备并核验锁定wheelhouse，包含Pillow、psycopg-binary、cryptography、cffi、pydantic-core等平台二进制依赖。`py3-none-any`不意味着这些依赖或解码工具跨平台通用。目标平台marker闭包须单独核验，本轮没有下载依赖。

下列仅为后续获准部署时的命令，不是本轮生产执行记录。所有参数必须来自独立受审部署记录，凭据不放命令行：

```sh
rtk proxy "${M3_PYTHON:?}" -m pip install --no-index --find-links "${M3_WHEELHOUSE:?}" -r "${M3_BUNDLE:?}/runtime-requirements-pinned.txt"
rtk proxy "${M3_PYTHON:?}" -m pip install --no-index --no-deps "${M3_BUNDLE:?}/dist/easel_central_platform-0.1.0-py3-none-any.whl"
rtk proxy "${M3_PYTHON:?}" -m pip check
rtk proxy "${M3_PYTHON:?}" "${M3_BUNDLE:?}/preflight.py"
```

候选必须安装于全新独立venv。安装后预检精确检查100个payload SHA、Python／SQL集合和Python3.12，拒绝混入额外协议代码；保留旧venv和旧包。

## 016→017→018→019→020：owner流程

在诊断当前真实DR失败并完成上面的fresh freeze／fresh restore门槛之后，才能准备获准的迁移窗口。暂停新派发并协调所有资金写入者；停旧worker不丢弃任务。原unknown及其hold／permit不释放、不重POST。备份必须包括完整DDL／函数／触发器／约束／owner／ACL／行／sequence，以及实际媒体字节、registry、明确缺失staged key和配置权限；旧016固定范围、32MiB演练材料不能外推020。

一次性owner进程私下注入 `CENTRAL_MIGRATION_DATABASE_URL`，明确schema和独立API／Console角色。先用只读预检验证001–016集合／checksum、schema owner身份及角色危险权限：

```sh
rtk proxy "${M3_PYTHON:?}" "${M3_BUNDLE:?}/preflight.py" --database pre016 --schema "${CENTRAL_DB_SCHEMA:?}" --api-role "${M3_API_ROLE:?}" --console-role "${M3_CONSOLE_ROLE:?}"
rtk proxy "${M3_PYTHON:?}" -m platform_service migrate
rtk proxy "${M3_PYTHON:?}" -m platform_service grant-runtime --role "${M3_API_ROLE:?}"
rtk proxy "${M3_PYTHON:?}" -m platform_service grant-console --role "${M3_CONSOLE_ROLE:?}"
rtk proxy "${M3_PYTHON:?}" "${M3_BUNDLE:?}/preflight.py" --database post020 --schema "${CENTRAL_DB_SCHEMA:?}" --api-role "${M3_API_ROLE:?}" --console-role "${M3_CONSOLE_ROLE:?}"
```

API行政CLI使用CENTRAL_MIGRATION_DATABASE_URL；Console grant使用CENTRAL_CONSOLE_MIGRATION_DATABASE_URL并要求owner current_user=session_user。两套owner／runtime DSN、显式schema必须对应同一受审目标，既有Settings有效但不得输出；所有owner变量必须从长驻API／Console环境清除。migrate核验历史checksum，取得schema维度事务锁，在一个事务内应用新增迁移。失败不能通过手写schema_migrations或改旧SQL绕过。

| 迁移 | SHA-256 | 内容 |
| --- | --- | --- |
| 017_voice_stages.sql | c1292c5aa13f04e1ef72083766e6404e900fc37c1dbe43b88a2e9eedc76122e8 | 声音阶段、观察与登记proof、不可变和终态关联守卫 |
| 018_media_async_work.sql | 6c7f2ce777064d8a747c6de4fac9b33b0e77792d3a82bbc1a5ab44bf4b1b8496 | 异步原ID工作队列、观察、来源及完整性守卫 |
| 019_media_voice_runtime.sql | f52ba0c5d4c83f3f39b802de93c1d00667d46458f3d6b20b1d09c7735291ef66 | 声音adapter闭集、不可变严格数值protocol facts及真实response关联 |
| 020_media_incho_runtime.sql | 3b6577460d1f5bd7a12db243fc74adf82dcd211a5ec33ce0e8ce7ec0adc5e0f5 | **只替换media_work_adapter_check**，显式加入newapi_incho_v1；不创建模型、价格、权益或启用状态 |

post020预检要求完整001–020集合／checksum，并精确检查validated的queue CHECK定义为以下七adapter闭集：`newapi_videos_v1`、`newapi_suno_v1`、`newapi_dashscope_music_v1`、`newapi_incho_v1`、`newapi_minimax_voice_v1`、`newapi_dashscope_voice_v1`、`newapi_fish_voice_v1`。不是检查一个Incho子串；缺Incho、额外adapter、OR TRUE均拒绝。该定义匹配使用pg_get_constraintdef的规范形式；目标PG若格式不符应停下审查，不能放宽为包含字符串。

本轮数据库预检模式**没有连接PG执行**，仅做代码编译及纯函数的合成正负验证。后续先在新恢复副本实际运行，并完整对照DDL、deferred属性及约束行为，再按独立授权用于生产。

## 最小ACL与完整备份清单

020不增加表或权限。016之后共七张新表及API最小授权如下；Console对所有media_*私有表／列仍无权：

| 表 | API runtime |
| --- | --- |
| media_voice_steps、media_voice_step_observations、media_voice_registration_proofs | SELECT、INSERT；不可UPDATE／DELETE／TRUNCATE／REFERENCES／DDL |
| media_observations、media_protocol_facts | SELECT、INSERT；不可变 |
| media_work | SELECT、INSERT；仅phase、due_at、lease_token、lease_until、polls、failures、last_safe_code、updated_at可UPDATE |
| media_sources | SELECT、INSERT；仅location、metadata、artifact_id、updated_at可UPDATE |

完整acl-contract.json从新wheel导出，与旧M3包逐字一致，含所有旧表和列／sequence。API仅原两条sequence USAGE，Console仅audit sequence USAGE。API／Console不得是DB／schema／表owner，也不得经可达父角色SET ROLE到owner、超级用户或危险系统角色；检查current_user、session_user、所有可达角色及PUBLIC／独立列授权。不能以GRANT ALL、TRIGGER、DELETE或DDL修复启动失败。

post020只读预检检查API正向最小授权、API／Console安全边界、voice授权、26个新增触发器启用／存在、7个新增函数存在以及无未验证约束。后续恢复验收必须包含上述七表、media_voices／media_terminal_evidence／media_artifacts上的新守卫、全部函数／索引、020新CHECK和完整ACL，不得只导出数据行。

## AV、文件权限及实际沙箱

媒体root须绝对规范路径、API UID所有、0700；blob0600。media config为API UID所有普通文件、0600，不能符号链接。Console不得读取媒体私有目录或供应商配置；保持现有精确API媒体写权限，不扩大ReadWritePaths。metadata预检不创建文件，os.access不能证明systemd挂载可写；后续须在真实服务UID／沙箱下用受控新文件验证独占创建、fsync与清理。

ffmpeg／ffprobe必须为显式固定绝对路径，解析后的普通可执行文件由root或API UID所有、group／other不可写，两个版本首行都匹配同一配置版本。工具不随wheel分发。目标Linux需登记二进制SHA、动态库、架构和许可证，在实际服务沙箱验证完整合成媒体解码；保留CPU／FD／文件大小／Linux512MiB地址空间限制和受限临时目录，不放宽URL／CDN或协议白名单。缺工具／版本／费率条件即closed。

```sh
rtk proxy "${M3_PYTHON:?}" "${M3_BUNDLE:?}/preflight.py" --filesystem --media-root "${M3_MEDIA_ROOT:?}" --media-config "${M3_MEDIA_CONFIG:?}" --av --ffmpeg "${M3_FFMPEG:?}" --ffprobe "${M3_FFPROBE:?}" --av-version "${M3_AV_VERSION:?}"
```

## 付费unknown、切换和回滚

- 关联头补丁只让X-Oneapi-Request-Id成为最后fallback，不修复真实HTTP400根因，不授权重发原文本。原两笔pending、held1,009,728和attempt／wire hash／账本原样保留，后续fresh freeze须重新读取准确状态而非假定数字不变。
- Incho异步恢复只GET原任务ID；声音新增POST链仍限原5分钟，纯原ID GET／已完成登记finish仍限原24小时。unknown保留hold和一次性permit，重启／切换不能创造新预算、重新POST或伪造成功／usage／零价。
- 初次发布也不自动开放新增能力。缺真实渠道、价格和明示meter继续closed；旧DashScope enrollment缺安全素材producer继续closed。后续真实请求必须有独立授权。
- 迁移事务未提交时失败应整体回滚并验证仍016；不改旧包。已提交017–020后没有down migration，不能DROP新表／删迁移账。旧f50在020数据库上的兼容性未验收，不能承诺只切回二进制即可安全回滚。
- 若提交后没有新数据，回退仍须使用已验证fresh窗口的DB／blob／配置恢复到新目标后独立复核；优先受审forward fix。若已有新任务或资金写入，不能恢复旧备份丢弃付款／hold／receipt；先停止新派发、保留最新证据，制定包含新状态的恢复或前向修复计划。

当前交付只是可审本地候选。Linux依赖wheelhouse、真实服务沙箱、生产schema／ACL、freshfreeze控制器与真实DR成功证据均未完成；原失败记录和旧所有包保持。
