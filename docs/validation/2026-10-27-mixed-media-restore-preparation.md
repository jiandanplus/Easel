# Mixed 媒体恢复候选：本机验证

**当前：新增独立候选的完整本机合成验证通过；没有上传或执行154的新DR。** 实际服务器仍有两笔已交付图片和一笔缺文件的旧 Flare 待核算，本次实现保留该异常及原1点预留，没有用免单或删行满足旧 settled 工具。

实现前阅读了 `2026-10-27-media-storage-readonly-cause.md` 中的冻结 f50 因果审计及 `2026-10-27-newapi-image-workbench-delivery.md` 的两笔真实交付/扣点证据。root 随后明确最终 exception 审计只有 agent 消息、没有磁盘报告，并确认其完整合同与本候选相符：精确2settled+1pending，typedref3，2attached+1staged missing，预算2released+1held，原1m预留与actualNULL；原reason为upstream_uncertain、execution unknown、delivery processing。这些是既有审计/受信消息来源，不是本子任务重新执行的服务器或供应商验证；不能将本机合成数据当作服务器当前行值。

当前6个新增Python文件冻结如下，所有9输入（另含原v6/backup/crypto）的完整路径与SHA在 `/private/tmp/easel-mixed-media-restore-review-20261027/freeze-manifest.json`，manifest SHA为 `04bb87fbeb62bf3a476caa595de802b99cbcea3fc8ca76bccb805e0ad64c39f6`：

| 文件 | SHA256 |
| --- | --- |
| verify_mixed_media_restore_drill.py | `2e6c5d8e5a6dac0801f613e320ca1bc995862c365362d1b33ebffce029988596` |
| capture_mixed_media_restore_plan.py | `c86497bf47278d46c6500a72aba213f74fc4ac46b4f56e779d4973ae4ef592a8` |
| test_mixed_media_restore_drill.py | `8d496be85a842f69eddc19927b92255b6c91308cc30fd0c07e82530f4e28e4de` |
| test_mixed_media_restore_plan_capture.py | `271245a6907ffc1dfc8247189196aa3b02f9c68ad05b8b30d6cb27c7c9f2cee0` |
| mixed_media_restore_fixture.py | `5ecfaf37f34b88651c812ff3ae79c2f58b3af26b8514246c30a482a90b037f29` |
| test_mixed_media_restore_fixture.py | `4cb77309e99158dc5d5b8446845e936b88a965cfbbc36876129c1ea56fcf61e8` |

同目录 `mixed-drill.diff` 为376行、SHA `8c46985452b35cac7a4b2261684dc61cdd8d672891c00fe5c78cbddd20933f7d`；`mixed-capture.diff` 为46行、SHA `5acf376360aea9fa01fee8b0dc4cd4fc8f98438c99de3421c85ba1aa95c10dd2`。`full-tests-tool-stdout.log` 是实际 session55048 两个 stdout chunk 的合并记录，不冒充另一次测试。safe回执SHA为 `499b902eeef49449cb0ce02888b9ecef7306f071bf6c8eedf964d58fe1fff500`。候选冻结后没有再改Python源/测试。

实际完整命令：

```sh
rtk proxy env EASEL_MIXED_RESTORE_DOCKER=1 EASEL_MIXED_RESTORE_EVIDENCE=outputs/_run/mixed-media-restore-20261027/synthetic-mixed-nonempty-safe-final.json services/platform/.venv/bin/python -m pytest --noconftest services/platform/tests/test_mixed_media_restore_drill.py services/platform/tests/test_mixed_media_restore_plan_capture.py services/platform/tests/test_mixed_media_restore_fixture.py -q --tb=short
```

结果 **76 passed / 0 failed / 0 skipped / 30.39秒**。其中真 PG 回环不依赖旧 handle，独立创建专用源和 fresh 恢复目标，并在 finally 按 guard 停止保留；没有连服务器或调用真实供应商。

首轮 mixed 准入/文件/最新 drop-in 测试为48失败、1旧源冻结检查通过，之后实现为49通过。完整PG先使用旧两笔settled夹具，因缺原pending被严格拒绝，原对象停止保留。新夹具只对`.part`的创建注入 errno.EROFS，完整 f50 provider解析、PNG解码、typed HTTP200、stage事务、pending处理都是真的；后两笔用同一假供应商正常交付。fake transport POST总数3、真实provider POST0。没有通过SQL伪造pending、terminal、receipt、预算或点数。

独立新 PG 中保存原三笔指定 job IDs：两笔 settled/completed/available，各actual1m；旧pending/unknown/processing一笔actualNULL、invocation held1m/spent0、staged pin和未释放budget。总计75表、529约束、95行；2张1024² PNG，各>1MB，合计2,101,644字节；typed reference3、terminal2、receipt2。合成钱包issued10m/spent2m/held1m/available7m，无付款。

同一中央 exported snapshot 捕获完整原始库存、三blob registry、两原图和唯一 missing-staged 摘要；新cipher落盘后重读并认证，实际cipher2,704,203字节。恢复到新的network-none PostgreSQL，原rawschema、全部约束、roles/owner/ACL、DBmetadata、所有行、sequence和财务库存全部匹配。目标mixed查询完整pending对象与源逐字相等；两文件byteexact，原缺失key仍不存在，没有占位文件。11个合成配置的实际本机owner/mode恢复，坏cipher拒绝。生产997权限由未来server source/config闸验证，本机UID不是生产997，不能冒称生产配置已经恢复。

纯负例涵盖未知job或模型、额外/缺少pending、actual被改0、丢hold、pin/预算释放、错误attempt/binding/reference/status/namespace、GC、任一attached丢失、staged突然存在、未知文件、`.part`、符号链接、错误allowlist以及状态漂移。新proof缺任一target pending/actualNULL/hold/absence/typed/两文件证明均拒绝。实际namespace闸用只读合成mountinfo先红后绿；safe wrapper不导出mount正文、pending对象、storage key、DB/user/role、配置或SQL。

旧49df/68dccf/af190/v6核心哈希保护通过，没有修改原timer或任何生产源。候选文件与最小diff/freeze清单交root独立审查；固定范围见 `docs/superpowers/plans/2026-10-27-mixed-media-backup-restore.md`。32MiB有界、逐库独立snapshot；不宣称日常通用媒体备份、跨库原子、PITR、恢复应用登录或已处理旧pending费用。
