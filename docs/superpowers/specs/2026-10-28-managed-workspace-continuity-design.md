# 正式工作区与本机草稿连续性设计

日期：2026-10-28。状态：**设计草案，等待人类批准；未实现、未执行用户迁移、未新增测试。**

本草案按 brainstorming 阶段提出方案，供独立审读后由用户选择。不得因已有合成预检通过就执行本设计。当前分支与不新增 commit／push 的用户约束优先，本文不提交、不改主交付计划。批准后才能编写实现计划；真正用户迁移前还须单独审计具体输入和操作。

## 1. 目标与已知事实

用户要求保留登录状态、历史、配置、订阅／自定义双模式和未发送草稿；新用户应默认使用正式工作区。产品 UI 只显示必要操作、状态和错误，不添加开发或迁移协议说明。

已有 [实际浏览器迁移预检](../../validation/2026-10-28-workspace-migration-preflight.md) 证明限定事实：真实 v29 store／hook 在同 origin 改变 workspace scope 后看不到旧草稿，返回旧 scope 可恢复；相同 scope／session 改变 origin 后也看不到另一 origin 的草稿，回原 origin 刷新仍保留。预检是最小 UI，附件服务为 mock；没有验证 full App／ChatPage／生产附件归属，也没有迁移用户数据。

当前相关合同：

- `easel/portable.py:166–167` 默认工作区为 `~/Library/Application Support/Easel/workspace`，默认端口 17860；原已验用户工作台使用 `http://127.0.0.1:17868`。启动／停止目前以显式 workspace 或默认目录找到控制文件。
- `easel/workbench.py:113–118` 使用工作区私有 `scope.key`：workspace scope 是 HMAC(key, `workspace\0` + 绝对路径)，source scope 是 HMAC(key, `source\0` + 有效 source 的摘要)。**换路径必须产生新 workspace scope，不能伪造或沿用旧值。**
- `web/frontend/src/lib/composerDrafts.ts:51–60` 的键同时绑定 workspace scope、source scope、session；实际草稿在浏览器 localStorage，磁盘工作区备份没有包含它。
- `web/frontend/src/App.tsx:70–75` 的首页未发送 session 还依赖 sessionStorage 的 `easel_dashboard_session_v1`。它可能没有进入 `loadSessions()` 列表。新开标签页不能假定会继承这条索引，不能只搬 store 键就称草稿可达。
- `DraftSubmission` 保留原 clientTurnId、submittedRevision、confirming 状态及可能存在的 requestHash；迁移不是接受请求、结算、重发或确认任务终态的依据。

非目标：云端草稿同步、收费、第二产品接入、中央／钱包修改、自动修复未知历史任务、签名／公证、跨设备迁移及新原生窗口。它们不借本设计扩大范围。

## 2. 三条路线

| 路线 | 行为与收益 | 代价与边界 |
| --- | --- | --- |
| **A，推荐** | 保留原浏览器 origin 17868；私有 launcher profile 指向正式目录；离线本机复制并校验文件；凭一次性、受验证的 oldWorkspaceScope→newWorkspaceScope 许可接续本 origin 草稿 | 需要小型 profile／本机迁移记录和前端一次性接续适配。必须处理首页 session 可达性、旧页面并发、partial 与冲突；不能声称文件系统和浏览器事务原子 |
| B | 草稿显式导出成私有文件，再切默认 origin，用户显式导入 | 用户步骤更多；文件含草稿和附件引用，须 0600、限大小、校验来源及冲突，不能当普通分享文件。原 origin／source 绑定仍需验证，导入不授予新账号权限 |
| C | 新增本地后端持久草稿存储，以后由后端提供接续 | 涉及新存储权威、同步／并发、账号隔离、导入与数据生命周期，范围显著更大。暂不推荐，也不混入 A |

A 保留原浏览器容器与 origin，减少额外人工导出和私有文件流转，但不承诺无感迁移。原 tab 刷新优先；必要时提供最小“恢复草稿”入口。新用户没有历史 profile 时仍使用正式默认目录与 17860；已有 17868 用户迁入新的永久目录后仍用 **127.0.0.1、http、17868**，不自动改成 localhost、其他端口或其他浏览器 profile。

root 另已只读 lstat 既有默认 `~/Library/Application Support/Easel/workspace`：它已存在、为非 symlink 目录，UID501／GID20、mode0755；未读取内容。此 metadata 不属于合成夹具证明。A 对这次 legacy 迁移选择**全新** `~/Library/Application Support/Easel/workspaces/legacy-<opaque migration id>`，子目录必须 0700；不强塞到既有默认 workspace，不扫描、合并、覆盖或 chmod 它，也不要求用户删除它。私有 profile 让旧用户的普通双击 start／stop 指向这份新永久工作区；无 profile 新用户维持原默认路径逻辑。

## 3. A 的信任边界与 source scope

复制原 `scope.key` 是保持 source 范围的必要条件，**不是充分证明**。新旧工作区必须分别通过同一份真实 source 组装逻辑求值，并严格比较 source scope：

1. `web/app.py:896–935` 先取有效 harness config。
2. 中央／legacy 订阅取其本机状态的 URL 和 auth identity；只去掉 access_token、refresh_token、expires_at、refresh_expires_at。其他身份字段仍参与绑定。
3. Pi 自定义或 bundled Codex 取真实 provider 配置；direct API 取 `EASEL_DIRECT_API_*`；外部 Codex 取 native_config_binding；OpenClaw 取现有配置内容摘要。
4. 该 source 由现有 `WorkbenchState.contract` HMAC。build_id 单独计算，不把“包升级”伪造成身份连续。

实现时将现有有效 source 组装与 HMAC 计算抽成 HTTP 与本机 CLI 共用的纯函数，保留上述语义，不在 CLI 复制一套简化的账号判断，不用预检中的合成 source 替代它。CLI 另用 existing-only 读取器验证并读取原 scope key、所选账号／渠道状态及必要的既有锁；不调用会 mkdir／创建 key 的 `WorkbenchState._key()` 或 HTTP 初始化包装，也不调用会 mkdir／chmod／O_CREAT 的 `SessionStore.locked()`。所需 key／所选身份缺失、格式坏或权限不符就拒绝；原合同允许缺省的可选配置仍按同一纯函数的空值语义求值，不能靠写默认文件满足它。正常首次 HTTP 初始化保持原行为。离线检查不刷新 token、不访问模型／中央或查询钱包；无法只读求值就拒绝发许可。真正发许可前仅在已停写并受控的来源快照上完成最终比对。

仅当 old source scope = new source scope = 当前 source scope，且迁移记录、profile 和文件快照绑定均成立时，才许可重定位 **workspace 维度**。source scope 不改，session ID 不改，不跨账号／渠道／provider key／原生账号边界。配置或身份改变立即使未完成许可失效；已恢复草稿仍按正常 source guard 隔离。

复制登录文件不等于登录仍有效。过期／撤销的登录继续由原认证守卫拒绝并要求正常登录；不清 error 伪造 authenticated，不延长凭据，不承诺迁移后免登录。不得把 source／ticket authority 解释成新账号共享许可。

## 4. 启动 profile 与本机入口

新增一个包外用户私有 launcher profile，默认位置为 `~/Library/Application Support/Easel/launcher/profile.json`；这是拟议位置，本设计阶段不读取或创建。新增专用 launcher 控制目录为 0700，避免为了私有文件改变既有 Easel 父目录。profile 包含 schema version、opaque profile_id、新永久 workspace 绝对路径、精确 origin 的 host／port、所选 App 的经验证资源身份和本机迁移记录绑定。文件必须为当前 euid／egid、0600、普通单链接文件，不跟随 symlink。它不保存 provider secret，也不进入 App／ZIP／GET 响应。

所有父 path 在真实操作前验证 owner、类型、symlink 和非授权写权限；已有父目录只验证，不自动 chmod。专用 launcher／workspaces 父目录若不存在才以 0700 新建；若已有而不能满足边界则拒绝操作，不借迁移修权限或碰既有默认 workspace。App 的内容／build inventory 身份与可移动安装位置分开：合法拖到 Applications 不产生新 workspace 或 source。普通升级通过显式、核验后的 profile 资源 rebind 保留 profile_id／workspace／origin，使未完成的 build-bound permit 失效；不重导 consumed 批次，不新增自动更新器。

普通 App 启动与 Stop Easel 必须使用同一 profile 解析规则。无 profile 才用现有正式默认值。显式本机 `--profile` 与 `--workspace`／`--port` 互斥；显式旧式参数仍用于隔离诊断，但不能被普通双击入口悄悄选中。profile 损坏／权限不符／资源身份失效／端口冲突均报错，不回退另一工作区或 origin，不 kill 端口占用者。

本机迁移只由显式 CLI 发起，拟定为 `workspace-continuity prepare / commit / status / resume` 四个操作；这不是当前可运行命令。CLI 显式接收本机路径并在私有记录里绑定，没有 HTTP 任意路径迁移端点，没有模型工具写入口。

保留现有 token 认证的 instance identity／stop 合同和停止旧实例的兼容路径；profile 只统一选择同一工作区，不为迁移重写既有 CLI 控制协议。只有新 continuity GET 不回显路径或 token，控制 token 不进入新日志。不能把未经认证的 PID／端口当所有权证明。

## 5. 文件迁移的准备、提交与边界

本机流程分开记录文件与浏览器进度，不用一个“成功”掩盖另一部分尚未完成。停止已认证自有实例后，以现有 owned workspace lock 排他保持复制／切换窗口；profile／permit 的读改写在同一私有控制锁下串行，并核预期旧值。只读 prepare 不创建来源锁；无法安全取得必要的既有来源锁就拒绝。锁不证明同 UID 的任意外部编辑已被禁止，文件漂移检查和旧页面停写前提仍保留；普通 profile `os.replace` 不称为 compare-and-swap。

1. **只读 prepare。** 验证来源、目标、App、profile 与 backup 父目录均是明确的本机输入，无祖先／后代重叠，不与包资源重叠。本轮目标固定为新 opaque migration id 对应的独立永久子目录，任何同名既有目标（包括空目录）都拒绝；非空目标尤其不能合并或覆盖。不得扫描既有默认 workspace 内容来选择或清理目标。
2. **用户页面停写前提。** 操作者先让旧页面保存草稿，停止编辑，关闭其他 Easel tab，保留原 tab 供刷新。旧 v29 页面没有新互斥协议，本系统不能自动证明或阻止所有旧 tab 的写入；这一前提必须在真实迁移前审计里明确确认。无法确认就不执行实际切换，不能用“自动无感”作替代。
3. **源停闲。** 只读核对原任务／回执，确认没有仍运行的任务或正在发送的请求，再用认证的自有停止入口停源实例并确认其控制锁释放。未 accepted／confirming／结果未知的历史只保留原 ID 查询，不重派、不撤销、不把缺少记录解释为可重试。不能证明源停写、扫描中有变化或存在活跃 writer 时拒绝复制。
4. **完整私有 backup。** 为本轮建立新 0700 backup，0600 私有清单记录所有条目的内容与原始 metadata。内容含原账号、配置、历史和用户文件，不进入公开源或包。已存在备份不覆盖；完整备份核验完成后才继续。未知 symlink、特殊文件或无法安全表示的内容使准备失败，源与已写证据保留，不解引用外部目标。
5. **同快照复制。** 在目标同文件系统的新 0700 sibling stage 复制；扫描前后清单一致，每文件读取前后核对类型、身份、size／mtime／ctime，并核 SHA。校验 bytes、mode、link 类型／目标、mtime，保留源 metadata 记录。目标所有权为当前用户；必要的 group 归属差异必须由 prepare 明列且符合原私有文件 guard，不能静默降权或宣称 GID 完全相同。凭据原本不满足私有权限就失败，不顺便修权限。
6. **控制状态不进入可运行目标。** backup 仍完整记录原 `.easel-portable` 状态；目标不复用 instance.json、launcher.lock、旧 token 或 PID。已认证归属的 managed `.venv` 只记录原链接，不遍历或复制；目标由新包重建链接及 ownership 记录。其他 symlink 一律拒绝。坏 seed／未知 `.venv` ownership 不伪造修复。源目录保持原状。
7. **提交目标。** 复制／比对完成，原 source 与 stage 的实际 source scope 相同后，写前记录 phase 与清单 digest、fsync 文件／目录，再以同文件系统的排他 rename（目标存在即失败）原子提交 stage 到经再次验证的不存在目标；不以普通可覆盖 rename 代替，平台不支持排他提交则拒绝。只把这一次目录提交称为原子。失败保留 stage／backup，不删除来源或任何既有目标。只有同一私有 journal 证明为本轮已提交目标时，显式 resume 才可核验该目标；它不重新复制、覆盖或接管未知目录。
8. **提交 profile。** rename 后先以目标的真实永久绝对路径重新计算 workspace scope，绝不把 sibling stage 的 scope 当成最终值；再以同一 source 组装函数核目标 source scope。若它与来源不同，停在 `target_committed`，不改 profile 或发许可，保留目标供审计。验证通过才另一次原子替换私有 profile 指向新目标并保留 origin 17868，保留原 profile 副本；再启动新包重建自有控制状态。目标和 profile 是两个可恢复步骤，journal 明确区分 `target_committed` 与 `profile_committed`，不声称二者一起原子。确认 bind／identity 后才引导原 tab 刷新。

source 完整 backup 清单与 target 复制清单分开：后者明确排除上述瞬态／managed 控制条目。新包正常 seed 更新只允许沿用现有“原先未被用户修改才更新”规则，任何发生的 package-owned seed／新建控制记录 delta 都逐项另记；不能把它们伪称原用户文件完全 byteexact。许可绑定的是提交时受保护的 snapshot manifest，不是要求运行中所有日志／instance 文件永不改变；profile／manifest 记录被改或当前真实 source 不匹配才使许可失效。附件当前是否可用仍由第 8 节的真实重验决定。

默认文件门：最多 100,000 条目、总普通文件内容 4 GiB、单文件 1 GiB、相对路径最多 4096 字节／64 层；不是常规大媒体备份能力声明。超限拒绝，不截断。开始时要求可用空间至少为“两份新增内容估算（backup＋stage）＋512 MiB”，逐文件检查剩余预算，低于 512 MiB 就停止本操作，不清理旧文件。复制最长 30 分钟；超时、取消或 metadata 漂移保留证据并停止。此门适用于小型本机工作区，不能借它取消既有独立打包门。

## 6. 一次性许可和有限 HTTP 合同

profile 和一份私有 continuity record 足以保存本机状态；不新增守护进程或服务。record 0600、父目录 0700，当前 euid／egid、单链接、无 symlink，写前 journal 每阶段 fsync；manifest 分别在新 backup／stage 的私有清单内。record 最大 256 KiB，不存草稿正文。

CLI 只有在文件目标／profile 提交后，才可发一张 browser permit。私有字段固定为：

- version=1、256-bit 随机 nonce（64 ASCII hex）、profile_id、operator euid／egid；
- 精确 origin、old／new workspace scope、保持不变的 source scope；
- 原来源 snapshot manifest digest、目标 manifest digest、profile digest、目标 build_id；
- issued UTC、expires UTC、同一 OS boot 标识与 monotonic 起点／900 秒截止；
- browser_batch_digest（首次显式认领后固定）、已用状态与 journal phase；
- 对上述 canonical 字段的 HMAC binding，使用目标私有 scope key 验证，不暴露 key。

单 profile 只能有一个未完成 permit。有效期严格为签发后 900 秒，UTC 与同 boot monotonic 任一到期、倒退／boot 变化或 source／profile／manifest 改变均拒绝新的迁移写入；不能通过编辑 now／expiry 或 HTTP 请求延长。过期 partial 只读展示保留内容，必须由显式本机 resume 重新核对并签发新 nonce，不能靠前端重取 GET 自动续期。已经 consumed 的批次不进入 resume，也不再次导入；完成后的同 source 正常草稿编辑不受旧 permit 的 900 秒有效期限制，继续受当前账号与普通草稿门禁约束。历史签发 binding 仍须验证，但不能把旧 build_id／profile digest 当作完成后每次编辑的当前值要求：经核验的 App 移动或资源 rebind 在 profile_id／workspace／origin／source 不变时保留 consumed 映射与其工作副本。许可准入与完成后的映射权限分开，不能因合法升级让草稿失联。

HTTP 只在现有 local-origin／认证守卫内增加固定的 continuity 读取／认领／确认合同；请求不能携带文件路径、目标目录、scope key、账号凭据或任意迁移指令。GET 返回字段限于 version、nonce、old_workspace_scope、new_workspace_scope、source_scope、opaque binding、expires_at 与只读状态；不返回 profile、路径、manifest 内容、正文或秘密。nonce 是记录标识，不单独授予权限：服务器仍以当前私有 record、绑定与认证 scope 为权威。

用户点“恢复草稿”才认领；不因新开页面／空列表自动消费 permit。认领把该浏览器批次 digest 绑定到原 nonce；确认只接收 nonce、binding、批次 digest、行数与结果摘要，不接收原始草稿或修改中央状态。成功后 permit 标为 consumed，不能重发；相同已确认摘要可幂等读回，冲突摘要拒绝。HTTP 不执行文件复制或覆盖；模型工具不能访问这些控制操作。

## 7. 浏览器接续：不覆盖、可达、可恢复

浏览器与文件系统没有共同事务。先有私有文件提交记录，再有本 origin 的写前 browser journal；只有后者完成并经用户确认，才能称选定浏览器中的草稿已接续。

### 7.1 精确发现和首页 session

原 tab 原 origin 刷新是首选，保留同一浏览器容器的 localStorage，以及可继续存在的 `easel_dashboard_session_v1` sessionStorage。该索引若有合法原 session ID 且 source 与 permit 相同，保留原 ID／source，不调用 createSession 来替代。

新 tab 没有该索引时，只有“保持同 origin”仍不够。许可期间，前端有界枚举本应用草稿前缀，只解析与许可 old workspace scope／原 source **完全匹配**的 version 1 envelope。包括没有出现在 loadSessions 列表的非空首页草稿。每项保留真实 session ID；不从最新会话猜测归属，不把草稿任意移到当前 session。

“恢复草稿”入口列出这些候选：有已验证历史标题则沿用，否则显示“未发送草稿”；选择后以原 session ID／source 建立有界的恢复入口或首页入口，不伪造历史消息／接受回执。孤立首页草稿不批量调用 `saveSessions()` 加入历史列表，以免现有 100 条上限裁剪旧会话；选定原 ID 的恢复索引／首页入口独立保存，用户之后真正新提交时才走普通会话策略。sessionStorage 只记录该已选原 ID，不是重新分配 ID。无法验证 session 格式、source、envelope 或附件引用的项保持只读候选并报失败。

### 7.2 最小的一次性存储适配

localStorage 没有跨 tab 的 compare-and-swap，不能声称“先检查再 setItem”能原子创建目标。因此迁移**不写入或覆盖 canonical 目标 draft 键**，而是：

1. 写前 browser journal 记录许可绑定、源精确键／原始 payload digest、目标 scope／session、候选 digest 和进度。解析失败、写入失败、quota 超限立即停；不清旧键。
2. 把仅 workspace 维度重定位后的 envelope 写入新的专用候选键，保留不可变候选；另外创建独立的工作副本键供用户恢复后编辑。source、session、revision、正文、附件引用、updatedAt 与完整 confirming submission 原样保留。已有 available 标记不算新目标下验证完成。
3. 导入 ID 由 origin、old／new workspace scope、source、session、原 payload digest 确定，**不以新 nonce 再生成一份**。同一原 scope/session 的批次记录固定第一次 payload digest。新 nonce 只能继续同一冻结批次；源 payload 改变视为冲突，不重复导入，不丢掉 submission。partial 再进入时，在既有迁移互斥下核同一 import identity 和候选摘要；已存在的工作副本保留当前字节，不能从不可变原候选重新 seed 或覆盖用户后续编辑。
4. 前端草稿存储增加一个有界的一次性 backing-key 适配：只在当前 target scope／source／session 和有效确认记录匹配、canonical 键不存在时，把该 session 的正常 store 操作固定到这份工作副本。scopeKey 仍是新的真实 workspace scope，绝不返回旧 workspace scope。不是新后端草稿架构或同步服务。
5. backing key 在 store 实例创建时固定。旧缓存、unmount persist、后续 flush 都不能因 canonical 出现而改投 canonical 键；每次读取／写入／发送前再次检查当前 source／映射和 canonical 冲突。发现 canonical 键，即使其内容为空，也冻结候选编辑与发送、保留 canonical／候选／工作副本并显示冲突，不覆盖，不自动挑一份。
6. canonical 若恰好在检查后被旧 tab 写入，迁移写的仍是另一个工作副本键，不能覆盖 canonical；下一次观察到冲突便冻结。对工作副本自己的写入失败，保留内存未保存态及既有离页保护。不能把独立键的保留说成跨 tab 原子事务。

配合同 origin 的迁移互斥与 storage 通知减少新页面并发；缺少互斥支持时拒绝开始本轮迁移。**旧 v29 tab 不参与新锁，无法被自动阻止**，所以第 5 节的旧页面停写／关闭前提仍不可省略。许可／互斥也不防御同 origin 任意恶意脚本，不能被描述成新的账号授权。

最多接续 256 个 envelope，总原始内容 16 MiB，单项最多 3 MiB；正文保持原 1 MiB 上限，附件最多原 store 的 128 项，每次验证请求不超过现有后端 32 项。严格校验现有 version 1 字段，不截断候选来绕过上限；未知版本／格式、目标已有草稿、部分批次和不同摘要都显式失败并保留两份。浏览器 journal 最大 512 KiB，只存键、digest、映射与状态，不另存正文。

### 7.3 用户确认与后续变化

“恢复草稿”确认只启用已验证的本机映射、创建可达入口并请求附件重验，不创建任务、不调模型。旧 storage 键、不可变候选、源目录和 backup 不自动删除。完成映射可作为本 scope 下该 session 的 backing 记录保留；没有自动清理或跨下一次迁移串接规则。

发现目标冲突只提供最小“迁移失败”“恢复草稿”操作／状态，候选只读；解决冲突需要另一次明确选择与审计，不能后台覆盖。没有目标草稿的空白 composer 也不得在迁移决策前挂载并自动 persist 一个新 canonical 键；首次启动先完成接续入口判断，再挂载该 session 的 store。

## 8. 未知 submission、附件和发送权限

原 confirming submission 的 clientTurnId、requestHash（若有）、submittedRevision、text、attachments 全部保留，不重新计算成新请求，不生成新 turn ID，不伪造 accepted 或 clean_end。缺 requestHash 时不补造，不因迁移完成就清 confirming。

进入恢复的原 session 后，先走现有原 ID 的只读 turn query；只有 session／turn／source／原 requestHash 和接受或拒绝凭据均匹配，才能使用对应状态。现有 API 已检查 session／turn、回执与 hash 格式，后端已检查当前 source 归属；它们不等于前端已比较本地期望 hash。已另行授权的 A2 最小修复须先通过独立审读：accepted 回应对应本地 pending 时，本地 requestHash 必须存在且精确相同，才能 acknowledge、呈现确认结果或续接；缺失／不符保留 confirming、正文、附件和原 IDs，维持“请求状态待确认”。本迁移不把更强条件伪称为旧代码天然已有。未知继续未知；只查原轮，不自动调用派发／重试／withdraw，不用 resume 名义创建新请求，不修改 wallet／central。真正的新提交必须是用户之后的独立操作，仍满足当前模型 lease／来源／任务许可等全部原门禁。

所有附件在新 workspace／原 source、原 session 下逐项调用现有生产验证端点；先清运行时 validated 状态，不能仅凭迁移前的 available=true 发送。missing／forbidden／validation error 阻止相应带附件提交并保留草稿，不改 path／id 让其通过。文件内容与输出引用由已核对副本和当前后端共同证明，不能用本次 mock 的 3 次请求替代。

## 9. Crash、partial 和回退

| 中断点 | 下次处理 |
| --- | --- |
| backup／stage 未完成 | 核 journal 与现有条目，保留来源／partial；仅显式 resume 可继续，不以目标目录存在当成功 |
| target 已提交、profile 未提交 | 核 target manifest，再显式提交 profile；不重复制或合并目标 |
| profile 已提交、新实例启动失败 | 停止自己的失败实例，保留新目标与证据；不自动启动原源或改其他端口 |
| 文件完成、浏览器未开始 | 明确 `browser_pending`；可正常只读查看，但不称草稿接续完成；许可由显式本机操作产生 |
| browser partial／失去确认响应 | 依原 import ID／digest／journal 幂等核对已写候选；有冲突就停，不能新 nonce 重复导入 |
| browser permit 过期或身份变化 | 候选保留只读；新 nonce 须本机重新核对，不能放宽源边界 |
| 新 workspace 已产生文件／会话／草稿后要求回退 | 禁止自动 restore、覆盖或删新目标。先停闲并为新状态做新私有完整备份，形成单独可审计的人工切换；本方案不自动逆向合并 |

旧目录即使保留也不自动恢复运行，否则会产生两个可写真源。只有显式操作在确认没有新数据损失的方案下切 profile；无法证明就拒绝，不以“可回退”承诺无损自动回滚。

## 10. 验收分层（均为待执行条件）

1. **Source unit／合同。** 覆盖真实 source 组装与 HMAC、新旧 config／token 轮换／身份变化、私有 owner／path／symlink／非空目标、复制中漂移、quota／clock／过期、journal 幂等、nonce 重发、不同批次、crash 各阶段与新数据后回退拒绝。store 测 canonical 冲突、旧缓存 cleanup、不同 nonce、confirming 原 hash、附件 32 项分批和首页孤立 session 的可达性。以新行为 RED→GREEN 记录，不重跑无关绿套件冒充本设计完成。
2. **实际浏览器。** 同 origin 路径换 scope、不同 origin 同 scope；原 tab 刷新／新 tab 丢 sessionStorage 两条；匿名首页 session 未入 loadSessions；canonical 已有／中途出现、两个新 tab、旧 v29 tab 不合作、quota／中断／刷新、返回旧 scope 可读。核正文／session／source／submission 精确，0 task/model POST；分别说明新锁支持范围及无法自动阻止旧 tab 的事实。现有预检只提供本设计的动机，不预填这些新行为通过。
3. **真正 bundled、无网 dummy SDK。** 在现有包或后续经批准新包中，以两份合成 session 完成正式目录 handoff、原 origin 保留、两种业务模式配置／身份守卫；确认原 session 历史、一个未发送首页草稿和附件可达。用离线受控 dummy SDK 明确新用户操作后才执行文件工具，验证文件与 download 字节／hash，再验证同 profile stop／restart 与旧源不受影响。该层必须实际包入口、bundle Python／Pi、严格无外网，不以 source devserver／mock 附件或静态检查替代，不覆盖 HOME。
4. **真实用户迁移前单独审计，当前未批准。** 列出本轮源／目标／App／profile／origin、现有目录与浏览器容器、原 tab／其他旧 tab 停写、活动请求、未知 turn 只读清单、backup与可用空间、私有权限／symlink／大文件、实际 source scope 一致性及预期包 delta。提交确切动作与回退限制供确认。不得读取或写真实 workspace／Application Support／账号原文／旧 browser storage 来完成本设计阶段。

native 剪贴板、中文 IME、VoiceOver、独立净机和签名发行仍各自待验。本设计没有把它们改成静态／合成可通过的目标。

## 11. 设计自审与批准门

本稿已自审并明确：scope key 复制不等于身份相同；workspace scope 仍随路径变化；匿名首页 session 有恢复入口；同 origin 不等于同 tab／同浏览器容器；无跨 tab CAS；候选 backing 不转投或覆盖 canonical；新 nonce 不重复导入；confirming 保留原 ID／hash；FS／profile／browser 分阶段；新数据后不自动覆盖回退；source／permit 不是新账号授权；mock 验证不是生产归属证明。

未留待填上限或假通过结果。尚需独立审读与**人类选择 A／B／C 并批准设计**；批准前禁止实现、实现计划执行、真实迁移、构建或新测试。brainstorming 的明确门槛是：“Do NOT … take any implementation action until you have presented a design and the user has approved it.” 本稿交 root 完成审读并向用户提出设计选择，不以 agent 间同意替代人类批准。
