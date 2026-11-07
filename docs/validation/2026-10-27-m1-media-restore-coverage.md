# M1.1 / 016 / 媒体恢复覆盖

当前状态：**冻结 f50e M1.1 / 016 的真实双库、11 个配置及初始空媒体目录恢复已通过，独立后验也通过。** 该证据使用新的 a837…计划、cae1…nonce 和新密文，不是沿用旧 R2 成功。非空实际图片恢复仍须 M5 后另做。

## 真实恢复与独立后验结果

| 项目 | 已验证结果 |
|---|---|
| 唯一 nonce / plan | `cae1ad9dffe04d9986df17e8a81647c4` / `a837da51f337dbd602f4bd429316a70c62cee8d58595aca242aed8b4534274f1` |
| 冻结 DR / candidate | `68dccf3699a3c9b06d79e5cf87609dcf567b2d6c93c8b3c6186cc40c3474e1d3` / f50e wheel |
| 实际中央恢复库 | **75 表 / 734 行**，原始 schema、完整约束、全部数据、角色、owner / ACL / 设置、序列严格匹配；两项资金平衡均 true |
| 实际身份恢复库 | **79 表 / 446 行**，同样完整库存严格匹配 |
| 016 与配置 | 目标实际 16 条迁移记录匹配；11 配置的 bytes / SHA / UID / GID / mode 恢复及独立重读通过 |
| 媒体初始窗口 | `blob_count=0` / `blob_bytes=0` / `nonempty_media_blobs_restore_verified=false`，覆盖基础明确是空 registry / 空 owned 目录 |
| 新密文 | 1,393,483 字节，root:root 0600、单链接常规文件，SHA `732c79ea02737fe06ccf54e27e58f5adf1fa40dfae1b04444fad658ccff48e50`，独立重读精确匹配 |
| 原服务与源 | API PID 246906 / console PID 246907 的实际 cwd / argv / executable / MEDIA / 配置仍匹配；原 2 PG ID / image / running 精确相符；原四服务、Logto 和旧备份 timer active |
| 恢复目标 | 2 个 nonce 容器都已 exited / stopped，严格 owner / image / Cmd / volume guard 通过；network none、0 端口，容器及卷保留 |
| 原 unit 生命周期 | InvocationID `0bbd02e7b7b94c9aae2b7ebaa938b090`，04:35:04 UTC 启动；29 条阶段到 04:38:14 UTC complete；PID1 journal 对同 InvocationID 记录唯一 `Deactivated successfully.` |

程序自身从 04:35:07 到 04:38:13 UTC，约 **186 秒**。transient unit 没有 RemainAfterExit，终态后被回收，因此没有把 `systemctl show` 对 not-found unit 的默认 Result 当成退出证明；另按原 InvocationID 和 PID1 journal 独立确认成功终态，没有重派任务。

真实 `restore-safe.json` canonical SHA：**`847c3161ade52fb502e1fae48416e795c9441e8896b05c90640c78b71398b6df`**，与服务器 `/root/easel-media-restore-drill-20261027-cae1ad9dffe04d9986df17e8a81647c4/enable-proof.json` 字节完全相同。该文件由实际恢复与所有内置校验成功后生成，不是手工填入通过布尔。是否启用两个模型仍由 root 后续按批准流程执行，本监控子任务不改变模型状态。

本机 safe bundle 为 `outputs/_run/m1-media-restore-20261027/restore-cae1ad9dffe04d9986df17e8a81647c4-safe.json`，SHA **`6d807131fe83544df45ab8dcc3665ebc8644c02285179abe0749b24fcf91f75f`**。它包括程序安全回执、独立后验及原 unit journal 的分类元数据；程序回执重新 canonical 后确认等于上述服务器 SHA。没有取回完整 private plan、密文内容、明文归档、配置、角色值或真实 SQL。

独立后验 helper 固定 SHA `fd18a3d83f05e04d19325e7b9a3f26b9c72fce6d59802ef5e51115f809bd9c06`，按 root 明确审阅仅保存为原 nonce 0700 工作目录内 `verify-postcondition-server-only.py`、root0600，核 hash 后从该文件执行并 exit 0。它没有 SQL、解密、创建容器、服务 / 模型变更或源数据写入。

## 审批与准备阶段记录

授权状态已更新：root 最初的停止 API / console 并执行 apply-disabled 迁移请求被自动审批完整拒绝，理由是对具体维护窗口、结构迁移和权限副作用的授权不足，那次命令没有执行。之后用户通过 `call_3e06c3000db64793b08a843291a76bc3` 明确批准限定维护 / 迁移 / 恢复后启用及原余额真实验收，阻挡解除。root 已启动原精确计划的 stop + apply-disabled，并独占后续 candidate / cutover；本子任务没有代理该命令。准备阶段结束时，真实 DR 仍须等 root 确认新 unit active 和给出具体后验调度，不能把授权当执行通过。

后续 root 已确认新两 unit 切换及 production smoke 的 7 项检查全部通过，并上传精确 SHA 的冻结 DR / wrapper。获得限定只读授权时，本子任务仅生成下述计划；随后 root 核准具体 plan SHA 并独占启动上述唯一真实恢复，本子任务只监控和进行独立只读后验。

## 新服务后的真实只读计划

新 nonce 为 `cae1ad9dffe04d9986df17e8a81647c4`。完整 plan 仅保存在 154 root 0600 文件 `/root/easel-m1-media-restore-review-20261027/plan-cae1ad9dffe04d9986df17e8a81647c4-private.json`，完整 canonical plan SHA 为 **`a837da51f337dbd602f4bd429316a70c62cee8d58595aca242aed8b4534274f1`**。

实际通过的只读前置闸：冻结 f50e candidate / 68dccf DR / 4d3c v6 核心，11 个配置、16 条精确迁移 checksum、016 固定 SHA、真实 configured namespace 与 central.env 双 SHA、两个原 source container ID / image，以及 UID / GID 997 的空 registry / 空目录。实际 API PID 246906、console PID 246907 的 cwd / executable / argv 摘要 / MEDIA 环境检查均通过；完整 argv / environ 没有输出。

计划限定新的 nonce 工作目录、两套目标容器和卷，顺序执行 512 MiB / 0.75 CPU / 128 PIDs / network none / 0 端口的恢复，目标密文目录为 `/var/backups/easel-encrypted-m1-drill`。只读捕获阶段没有执行这些动作；随后 root 依该具体计划单次调度，真实结果已在本文开头记录。

白名单副本：`outputs/_run/m1-media-restore-20261027/plan-cae1ad9dffe04d9986df17e8a81647c4-safe.json`，该副本 SHA `60cabc336264fb2f05c330ef9d37a8929bd9c1f1f294ff6070a65f717eeb6b4a`。没有下载完整计划、数据库名、用户名、角色、配置或真实 SQL。

执行入口使用新 release venv Python 启动 wrapper。冻结 wrapper 的子命令固定 `/usr/bin/python3`，这一点已向 root 澄清；只读 plan 路径只使用标准库，因此本次成功。后续实际 DR apply 必须直接使用具备 cryptography 依赖的新 release venv Python，且仍需 root 单独调度。

独立复核已完成：最终 tool / tests 与 prepare / cutover 合并执行 **56 passed、0 skipped、31.79 秒**，输入在执行前后哈希不变，无剩余已知阻断。复核使用另一个全新 nonce 进行相同的真实本机 PG / 非空文件 / 11 配置回环，回执已留存 `outputs/_run/m1-media-restore-20261027/synthetic-nonempty-independent-safe.json`。仍不代表 154 服务器的新 M1.1 数据已经恢复。

候选固定为 `/srv/easel/releases/20261027-images-m1-1-size-v2`，wheel SHA `f50e52511e941d3bea8e724eddbd68330e20eaa6339c5e2437b3880ca9705abf`，016 SHA `a5a1dcfdb90299ebd063b18377a900ad68e1338bb2137cba9a7010f9694d6cba`。新工具 `services/platform/scripts/verify_media_restore_drill.py` 扩展 source unit / drop-in 恢复及实际进程闸后的审核 SHA 为 `68dccf3699a3c9b06d79e5cf87609dcf567b2d6c93c8b3c6186cc40c3474e1d3`；复用 v6 核心 SHA `4d3c4210469d6630dd4cbc71d8d5327e62aa972eedb7084685bb0df85a2c88ea`，核心原文件、旧 timer 和旧归档没有修改。

## 本机实测

最终命令：

```sh
rtk proxy env EASEL_RESTORE_DRILL_DOCKER=1 EASEL_MEDIA_DRILL_EVIDENCE=outputs/_run/m1-media-restore-20261027/synthetic-nonempty-with-dropins-safe.json .venv/bin/python -m pytest --noconftest services/platform/tests/test_media_restore_drill.py -q --tb=short
```

最终结果 **39 passed，31.16 秒，exit 0**。此前 35 项非空回环记录单独保留。新增 source unit / drop-in、原环境可无 MEDIA、完整配置私有恢复、实际 MainPID 进程确认的红测通过后，重跑完整回环。最初先观察到缺少功能时的 28 个失败；配置唯一性、冻结源码与迁移检查、proof 构造、失败清理、空白重复变量和 GID 校验也分别先红后绿。编译和 diff 空白检查通过。

实际 PG16 夹具使用随机非 public namespace，逐一应用冻结候选 001–016 并记录 checksum，包含 **75 张表、529 条完整约束、29 行**。通过真实 FK / deferred trigger 创建合成身份、媒体调用、asset 和 attached blob；捕获对应 **1 个 34 字节**受管文件。临时 RSA 私钥仅在本机进程内生成，不读取服务器私钥；加密归档落盘后重新读取认证，恢复到新空 PG 和新文件目录。原始 schema hash、完整约束、所有数据、角色与授权、数据库 owner / ACL / 设置、序列及财务平衡严格匹配；media registry 与文件摘要一致。

媒体文件和 **11 个固定配置文件**逐字恢复，含原 6 个私有配置、media.json、2 个 main unit、2 个 `90-m1-images.conf` drop-in。原本机 **UID 501 / GID 20** 与文件 0600（合成 main unit 0644）/ 私有目录 0700 保留。这个结果没有伪装成服务器 UID 997 的真实执行。哈希、长度、缺失、多余、穿越、符号链接、硬链接、错误 owner / GID / mode、读取时变化、`.part`、staged / gc_pending 和坏密文均拒绝，不跳过内容；既有目标路径拒绝覆盖。

最终合成回执：`outputs/_run/m1-media-restore-20261027/synthetic-nonempty-with-dropins-safe.json`。没有真实供应商、付款或生产数据库请求。

## 首轮真实执行门槛

只读预检观察到新 wheel 与目标目录已存在，wheel 哈希精确相符；原 API / console 仍使用旧 release，四个原服务及旧备份 timer 均 active。实际主机 4 CPU，MemAvailable 2,925,060 KiB，Docker 所在文件系统可用 42,916,212,736 字节。这是预检时快照，真正调度前必须再验。记录见 `outputs/_run/m1-media-restore-20261027/pre-cutover-stage-safe.json`。

真实执行中已经满足的前置合同如下：

- API 和 console 两个 unit 均在新 release active，76 个 Python / SQL 文件与冻结 wheel 逐字相同。实际 MainPID 的 cwd、cmdline 和 Python executable 必须相符，不能只依赖 daemon-reload 后的 unit 属性；实际 API 的 MEDIA 环境必须同固定路径。进程环境及原 argv 不输出。
- 数据库 schema_migrations 恰为冻结的 001–016 记录；真实 schema 名只从 SHA 绑定的 root 私有 central.env 读取，不扫描猜测、不对外输出。
- 两个图片模型 disabled，全部 media invocations / jobs / blobs / budgets 为 0，owned 目录为空。计划、中央只读 snapshot 捕获和最终状态复验，文本和登录继续运行。
- `/etc/easel-central/media.json` 是 UID / GID 997、0600，明确指向 `/var/lib/easel-central/media`；目录同 UID / GID、0700。原 central.env 可以没有 MEDIA 变量；API 的精确 drop-in 提供该变量，console 不添加。drop-in 字节从仅留服务器的原 main unit 唯一 argv 推导，和部署 helper 同合同；主配置与 2 个 root:root 0600 drop-in 全部纳入新加密归档及权限库存并绑定计划摘要。
- root 审核具体新 tool SHA、nonce、只读 plan SHA 与独立目标后，仅用受控新 transient unit 单次执行；两个目标 PG 顺序恢复，512 MiB / 0.75 CPU / 128 PIDs / network none / 无端口，成功失败均停止并保留。
- 只从 durable ciphertext 重读并认证的内容恢复。11 个配置文件在独立 root 私有文件树逐字及逐 UID / GID / mode 恢复，不安装或 enable 恢复的 unit。目标 016 记录实际查询通过、双库严格库存匹配、私有 media.json / 目录恢复和源状态复验全部通过后，程序才写 `enable-proof.json`，不能手填成功布尔。

首轮实际 `blob_count=0` 时必须写 `nonempty_media_blobs_restore_verified=false`、`coverage_basis=empty_registry_and_empty_owned_directory`。这是空目录覆盖，**不是实际用户图片恢复证明**。两张 M5 真实图片后须另建新 nonce 的真实非空演练，受管 `.part` / 过渡状态的完整 quarantine 方案也不在首轮工具内。

## 计划输出的服务器内捕获

`capture_media_restore_plan.py` 是独立捕获 wrapper，SHA `af1906bf0a0d012f3033407e4a41ac136b3b37c5746dd2891ebf29a6ba43bd2b`。它不改变已独立验证的 DR `68dccf…`：完整只读计划 stdout 仅在 154 子进程内存中解析，完整计划以 root 0700 目录下 0600 / O_EXCL / fsync 文件保存，SHA 仍是原完整 canonical plan 的 SHA。后续 apply 继续由原 DR 现场重建该完整计划并严格验同 hash。

对外输出显式白名单：新 nonce / 自有目标路径和名称、原 source container ID / image ID、冻结代码 SHA、资源约束、进程 PID / 命令摘要 / 校验布尔，以及配置 / 迁移数量和整体 SHA。移除数据库名、用户名、角色、配置字典路径 keys、原配置 / argv / environ、migration 明细和所有未知键。不会把完整计划通过 SSH stdout 或 journal 传到本机。

新增 12 个捕获测试先红后绿，含嵌套未知键的合成私有哨兵、错误计划 hash / nonce / script、重复 JSON key、超长输出、子进程失败、非法 nonce、已有文件拒绝且不再次启动子进程。与 DR 纯验证合跑 **50 passed / 1 个显式 Docker skip，0.11 秒**。这是本机准备阶段记录；后续 wrapper 已按明确授权在 154 仅生成一次上述私有只读计划，不属于绕过生产审批。

独立 reviewer 对最终 wrapper `af1906…` / tests `452b373…` 再次完整读取并执行 **12 passed / 0 skipped / 0.02 秒**，wrapper、tests 和 DR 三个 hash 前后稳定，无剩余已知阻塞。确认仅 nonce 的 argv 无 apply、stderr 不转发、完整 plan 私有落盘、对外白名单符合本轮元数据边界；该次本机复核没有进行 154 操作。之后用户批准、计划捕获及真实 DR 结果已在本文开头分别记录，恢复工具和 wrapper 继续保持同一冻结输入。

## 明确保留的缺口

本轮没有更换旧备份 timer，其 32 MiB 旧范围仍未包含媒体目录。覆盖 M1 的常规备份必须在新配置和真实恢复证据通过后另审，不能把本轮手工演练当持续保护。新工具同样 32 MiB 上限，超限失败，不能覆盖以后整个 2 GiB 媒体配额。双库各自快照，不具跨库原子性、异地恢复或 PITR；没有启动恢复应用、验证恢复登录或对恢复库发真实模型请求。
