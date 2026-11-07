# 订阅图片与私有存储交付验证（M1）

当前分支 `codex/ui-redesign`；没有提交、推送、合并或生产写入。媒体配置缺省关闭，当前真实 New API 文本渠道不能证明图片、视频、音乐、语音或音色可用。正式支付商户配置由用户暂缓。

## 实现

- `media_config.py`：owner-only 0600 私有配置；test 仅 literal loopback HTTP，live HTTPS；供应商 Key 仅服务端，明确模型/能力/版本绑定与 CDN origin 允许列表。
- `media/adapters/openai_images.py`：固定 generations / edits；variations 仅显式验证 legacy 模式，无自动降级或重试。HTTP/response 关联、JSON/图片字节有界、签名下载 URL 不转发供应商 Authorization。
- `media_runtime.py` / `media_web.py`：产品当前会话→短期任务 token→拥有素材→持久 job→至多一次 POST→完整解码产物→不可变终态证据→中央账本结算；精确重放、独立执行/交付/金融状态和已持久证据的财务恢复。
- `media/artifacts.py`：真实 Pillow verify 后重新 load；CPU/墙钟/像素/字节限制，Linux 地址空间限制；0700/0600 原子文件、SHA256 和 inode 检查，拒绝路径穿越、符号链接或内容变化。
- `015_media_storage.sql` / `media/storage.py`：owner 跨产品与全局 quota、先 staging 后写文件、准入事务预留最大输出存储预算、unknown pin、软失效→持久 GC claim→精确 unlink→ACK 才释放配额。当前下载 SQL FOR SHARE 覆盖实际文件 snapshot。
- `media/rates.py`：精确分数扣点与精确 Decimal tuple 时长缩放；不会因默认 Decimal 精度错过 ceil/floor 边界或把巨大指数抛为未捕获异常。

## 上一冻结快照证据（已被后续复审替代，不作为发布依据）

1. 配置 / 图片文件 / adapter runtime / HTTP / 合同 65 项独立单测通过（1.88 秒快照）。
2. M1 实际隔离 PostgreSQL 图片数据面 9 项通过（2.87 秒快照），包括生成、编辑、真实图片 decode + SHA、原输入回读失效后的冻结请求重放、HTTP 产品/任务凭据隔离、身份撤销、未知请求不重发、终态证据后结算故障恢复、可信 provider token 计量，以及能力/费率来源不匹配时关闭。
3. 独立 agent 存储测试 18 项通过（7.23 秒），包括 owner/global 并发限额、输出预算竞争、无额外供应商 POST、unknown 输入/产物/stage pin、未用预算释放、GC 重复执行与错误 lease、篡改/symlink/目录外文件拒删，以及独立 SQL `FOR UPDATE NOWAIT` 证明下载回调期间原行仍持 `FOR SHARE`。
4. 最终媒体全部模块 + 既有文本 execution API / runtime / gateway integration / recovery / runtime permissions 定向回归：176 passed in 27.25s，退出码 0。所有数据库 fixture 使用专用合成 PostgreSQL 16 和随机 schema，不跳过、不替换 SQLite、不指向原预览或生产库。
5. 后续自审追加可信终态故障注入：仅内部状态被误写 failed、没有不可变供应商终态证据时，旧 GC 会错误回收 2 个 blob，已观察红测试。现 GC pin 与预算释放要求不可变 terminal evidence 或真实派发前 released+actual=0，存储 19 项通过（7.96 秒快照）；仅状态标签无法获得删除和释放权限。旧 variation 增加 PNG 方图 / 小于 4 MiB / 旧协议参数限制 3 项红测试，以及内部嵌套配置变更使旧 binding 失效的红测试，修复后均绿。
6. 新增模块 compileall 退出 0；已检查本批新文件尾部空白。服务 wheel 构建退出 0，80 个文件，包含 migration015、storage 模块和 Pillow 12.3.0 许可证；无 .env、.venv 或测试私有 DSN 文件。

TDD 边界如实区分：计量来源矩阵、test 公网来源限制、精确 Decimal 边界、低磁盘、超请求 n、签名 URL、精确 replay、上传时限等均有已观察的失败→修复→通过；存储最初两项确认 stage API 缺失的失败，后续存储场景与实现并行增加，首次执行已通过，不宣称全部 18 项都有独立红绿证据。

上一冻结快照 wheel（已被后续复审替代）：`/private/tmp/easel-media-m1-wheel-20261027-final/easel_central_platform-0.1.0-py3-none-any.whl`，SHA256 `686f2599f4ac25cb37d2868ff84137f53ec39631f72542994662718dbbee30d6`。已逐字节核对打包的关键模块与最终工作区一致。

第一冻结快照独立媒体审查记录另见 `2026-10-27-media-m0-m1-independent-review.md`，其文件 SHA 不能代表后续 quota/GC 快照；后续以独立审查的第二快照为准。

## 第二轮复审修复与新冻结快照

独立 review 额外复现了四项：运行中 output budget 无 blob 可归零；registry 的 user 列可与完整 Scope 不同；断电 `.part` 留存时 final 缺失可能导致 false ACK；派发后登录撤销使输出 staging 丢失已知完成产物。均已先纳入持久失败测试，再修复。

- 015 增加 deferred 预算账实：未释放时 `remaining + 所有关联输出 blob 历史（含 deleted） = max`；真实终态 / 派发前释放后只允许 unused 预算归零，实际已分配不能重复恢复。registry 的完整 invocation Scope、owner、job / attempt 和附着元数据相互校验。迁移为旧 014 的 unknown / terminal 作业回填预算，两个真实迁移回归验证再次执行为空，旧待核算任务更新不会缺预算。
- GC 对 final 和确切 `.part` 先一起检查；仅完整长度、SHA256、UID、0600、普通文件和 inode 可核验才删除。不完整／不可核验 `.part` 保持 GC 失败、文件保留与额度占用，不能凭 final ENOENT 确认回收。受限私有恢复步骤见生命周期设计。
- 已派发产物 stage 仅按冻结 job / attempt / 完整 owner Scope 校验；用户退出立即阻止新的上传、提交和下载，但不会丢弃已接受任务的供应商产物、证据或最终扣点。
- 验收 runner 的必需迁移更新为 015，prepare-only 验证仍是零供应商调用。

新专用 PostgreSQL 上定向 5 模块 `72 passed in 33.86s`，本机不依赖 PostgreSQL 的 5 模块 `69 passed in 1.85s`，prepare-only runner `1 passed in 1.51s`。这些集合互有重叠，不相加充当总数。原内存盘测试实例在广泛回归中 OOM，混合快照只保留作诊断；已换专用磁盘卷 PostgreSQL，完整中央回归由独立 reviewer 在新冻结哈希上运行，结果另行记录。

新冻结 wheel：`/private/tmp/easel-media-m1-wheel-20261027-review-fixes/easel_central_platform-0.1.0-py3-none-any.whl`，80 个文件，SHA256 `b070d12f7c48f65709ebdfb295a76a2e1e45b79b612404893e5e541f00d1dfd9`，已逐字节核对 015 / storage / artifacts 与冻结工作区一致，仍等待独立全中央回归。

新冻结核心 SHA256：

- `015_media_storage.sql`: `1b91064ff21b1a91abcf6aeea46e91b22253073239db51221563d4ea224de1cb`
- `media/storage.py`: `e747922e90d0fb08169a693706ac74d78bf6a160b902248aac280ee8c0b783b1`
- `media/artifacts.py`: `2e7dfa3e236227b617323ce538ea3444adbd8d7a986e22157aa44eb709cb4ad4`

## 剩余证据与下一阶段

当前仅协议 fixture 请求、真实解码和真实 PostgreSQL 的隔离链路；没有真实供应商媒体任务或收费请求。生产媒体开放仍需实际模型、私有服务端配置、不可变真实验证记录、费率和完整产物验收。异步视频 / 音乐、语音识别、语音合成与音色 adapter 留在下一阶段，未配置能力继续关闭；M0 九类合同与 owned voice 基础不会被本批图片限定删除。多节点共享对象存储与跨卷灾备尚未验收。
