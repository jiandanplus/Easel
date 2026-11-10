# M1 存储配额与回收：第二轮独立审查

日期：2026-10-27。分支 `codex/ui-redesign`。本轮补审迁移 015、存储登记、输出预算、物理 GC、已派发产物及最小数据库权限，不使用第一次 94 项审查替代新增实现的验证。

审查者未编辑产品实现、发布模型或操作生产数据库。复现和回归只使用本机专用 PostgreSQL 的随机 schema、合成协议响应与私有临时文件；未调用真实媒体供应商。

## 审查发现与修复

1. **未分配到 blob 的输出预算可以直接减少。** 初始 015 的 UPDATE 守卫只禁止增加；独立 SQL 复现将一个已接受任务的 `remaining_bytes / remaining_files` 降到零，未登记任何 blob、也未写 released_at，事务仍可提交。最终增加延迟对账：未释放预算的剩余量与全部已分配 artifact blob 历史相加必须等于冻结上限；只允许不可变终态或真正派发前释放归零未用预算。已删除 blob 的历史仍计入分配，不会重新制造任务预算。
2. **registry 的 owner 可能与 invocation Scope 不一致。** 独立 SQL 保留 `scope.user_id=u` 却写 `user_id=other-user`，初始版本接受并把 owner 配额记给错误主体。最终延迟约束核对完整 invocation Scope、owner、job / attempt、attach 目标和真实元数据，错误事务回滚。生产 API 正常路径没有暴露任意 registry INSERT；本项证明的是数据库对内部写入错误的保护缺口。
3. **中断写入的 `.part` 被错误视为已清理。** 独立实际文件复现了 stage 后、link 前中断：final key 缺失，旧 GC 直接 ACK deleted、释放配额，却遗留 `.part`。最终同时核对精确 final 与 `.part` 名称；完整、真实大小 / hash / UID / 0600 / inode 一致的部分文件才可自动清理。不完整或不可核验的部分文件保持 gc_pending 和配额占用，转私有运营核对与授权恢复，不能假称已经清理。
4. **派发后退出登录会丢失已知图片。** 合成供应商在返回有效 PNG 前撤销原 session，旧 artifact stage 重新要求原会话仍有效，导致产物和终态未保存、请求进入待核算。最终内部产物记录按已接受任务的完整冻结 Scope、invocation、model / capability / mode 和 attempt 校验，不依赖用户仍处于登录状态。独立复验确认原会话收到 401，而任务已实际 settled、actual=2、产物和不可变证据各一份；同一用户的新有效 session 可下载原 PNG。对外上传、新任务和下载仍立即执行撤销规则。

上述四个原始故障均已独立复现；修复后独立反向验证 **4 / 4 通过，2.65 秒**。复核没有要求向已撤销会话返回产物，以免削弱登录撤销边界。

## 事务、并发和文件边界

- owner 配额按 user / tenant / billing account 跨产品累计，并受服务器全局配额约束。未物理确认删除的 staged、attached、gc_pending 均计入占用；输出预算与实际 blob 的转移处于同一数据库事务和存储 advisory 锁下。
- 准入先取钱包、invocation 和素材锁，再保留最大输出空间和资金；配额拒绝不会留下金融预留或生成 POST。当前 M1 路径未发现存储锁向钱包锁的反向等待。
- GC 根据不可变终态证据或真正派发前 released + actual=0 判定解除 pin；单改执行状态标签不能获得回收或释放预算权限。输入准入的 FOR SHARE 与 GC 的内容行 FOR UPDATE 串行，等待后用新语句复查 pin。
- artifact 下载的 FOR SHARE 覆盖真实私有文件 snapshot；GC 不能在读取完成前软删除并进入物理清理。
- GC claim / token / lease 持久化后才进行精确路径删除；unlink 和 fsync 后 ACK deleted。旧 token 不确认新领取；重复执行和 unlink 后 ACK 前中断可按相同 key 恢复。篡改、符号链接、目录外路径、权限或 hash 不符保持失败。
- 015 为已有 014 job 回填输出预算，保留已知终态和未知请求的不同状态；没有修改 014 已有迁移。runtime 无权提高 policy，更新列保留白名单。

## 回归与基础设施记录

- 原测试 PostgreSQL 使用 768 MiB tmpfs，但容器内存上限 512 MiB。首次全量产生 **8 failed、827 passed、1 skipped、420 errors**；只读检查确认 `OOMKilled=true / exit 137`。8 个失败逐项都是连接中断或拒绝，失败日志保留，未将该运行当作代码通过。
- 新建仅供本次回归的磁盘命名卷 PostgreSQL，内存 / swap 配置均 768 MiB、CPU 1、PIDs 128、shared_buffers 32 MiB、max_connections 30、max_wal_size 64 MiB、checkpoint_timeout 1 分钟；仅随机 loopback 端口、无 restart。原 5 个应用的容器 ID / start time 未改变；未删除旧测试对象。
- 新环境首次全量 **1259 passed、7 failed、1 skipped**。7 项均为 operator 配置入口拒绝测试 DSN 的 keyword conninfo 格式；产品设置刻意只接受 PostgreSQL URL。只将同一私有 DSN 原子转换为等价 URI，未改变目标或凭据、未放宽配置守卫或断言；三个 operator 模块随后 **18 / 18 通过，2.45 秒**。
- 最终使用等价 URI 连接格式重跑完整 `services/platform/tests`：**1268 passed、1 skipped，263.80 秒，退出码 0**。唯一 skip 是 `test_encrypted_restore_drill.py:100` 原有的显式合成 Docker 环境门槛；没有为跳过媒体、账本或权限失败而新增 skip。命令为 `env -u EASEL_CENTRAL_TEST_DSN EASEL_CENTRAL_TEST_DSN_FILE=<私有测试连接文件> services/platform/.venv/bin/python -m pytest -q -ra services/platform/tests`。
- 完整回归结束后，七个冻结关键实现 SHA 与开跑前一致；wheel 内 73 个 Python / SQL 仍逐字节匹配工作区。新测试 PostgreSQL 持续 running，`OOMKilled=false`。安全回执见 `2026-10-27-media-storage-review-safe.json`；失败运行的日志仍保留，没有用定向 176 项代替全套回归。

## 分发包

最终复审修复包为 `easel_central_platform-0.1.0-py3-none-any.whl`，SHA256：

```text
b070d12f7c48f65709ebdfb295a76a2e1e45b79b612404893e5e541f00d1dfd9
```

独立完成 ZIP CRC 校验：80 个文件，73 个 Python / SQL 文件逐字节匹配冻结工作区；Pillow 固定 12.3.0 且包含许可证；未包含 .env、.venv、__pycache__ 或路径越界。该包替代较早 `686f2599…` 包，后者不能代表本轮四项复审修复。

## 冻结 SHA256

| 文件（中央服务相对路径） | SHA256 |
| --- | --- |
| `src/platform_service/migrations/015_media_storage.sql` | `1b91064ff21b1a91abcf6aeea46e91b22253073239db51221563d4ea224de1cb` |
| `src/platform_service/media/storage.py` | `e747922e90d0fb08169a693706ac74d78bf6a160b902248aac280ee8c0b783b1` |
| `src/platform_service/media/core.py` | `85ff8faa2cabe1f9b8a7bfc7a1540f3b69c25ec8d98d3e90a2ff295401d91d4e` |
| `src/platform_service/media_runtime.py` | `44b1f278e3ef925b625d95a7c67976713ff4d61f2318a21bb9bbb586302c0c3b` |
| `src/platform_service/media/artifacts.py` | `2e7dfa3e236227b617323ce538ea3444adbd8d7a986e22157aa44eb709cb4ad4` |
| `src/platform_service/media/adapters/openai_images.py` | `b156bc90808a48cebfb315c8dccf176919582b3316c59d9be4516813a5da0d0e` |
| `src/platform_service/permissions.py` | `def79102c0621b7df88f67b69af97449d59d5e794008f1f775f9ab1da0543885` |

## 当前证据不覆盖的范围

本轮四项已确认缺口均已闭合。在生产默认关闭、单个私有持久卷、M0 / M1 图片代码和隔离协议 / PostgreSQL / 文件验证的范围内，未发现剩余已知阻塞。该限定结论不扩大成任意内部数据库角色受损、所有文件异常均自动恢复或真实供应商已经开放的保证。

本次不把 isolated PostgreSQL / 协议 fixture / 文件解码称作真实供应商验收。媒体生产仍默认关闭；实际模型、私有绑定、真实验证记录、费率和真实产物 / 用量 / 扣点仍需逐类完成。M2 视频和音乐、M3 音频和声音、桌面 / Pi host 工具、共享对象存储、多节点、高可用与跨卷灾备不在本次完成范围。
