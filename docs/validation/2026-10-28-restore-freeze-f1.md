# 恢复窗口 v2：F1 私有捕获合同

F1-r2 已通过独立审查，结论为 APPROVE_F1_CONTRACT_ONLY。它仅构造 captured/unapproved 的私有 F：approved=false、actions为空、paused_media_guard_executed=false，不授权暂停模型、修改资金或执行恢复。旧真实mixed DR仍为failed。

模块位于 `services/platform/scripts/media_restore_freeze.py`。它复用固定v6完整表库存和canonical比较，保留11个库存顶层字段、完整媒体metadata、真实enabled=true观察态及原pending关系。没有通过disabled投影调用暂停guard。私有存取采用root所有、受限权限、O_EXCL／NOFOLLOW、完整写入和文件／目录fsync；safe仅输出白名单字段。

初候选69项测试通过，但独立21项中六项发现应拒却接受的媒体缺失／关联错误，已REQUEST_CHANGES。原源码、tar和失败证据保留。R2新增实际enabled观察态完整25计数／八组full f50字段和跨表关联，保持原对象，拒绝缺失attempt、budget、valid_invocations等六项原反例。

验证分别记录，不合并计数：作者当前320 passed／0 skipped；原未改21个独立probe全部通过；新增28个独立边界通过；旧11个pure闭包另有历史通过记录，本轮独立复查未重跑它。三文件SHA前后相同，旧五脚本与123个require保持不变。

| 文件 | SHA256 |
| --- | --- |
| media_restore_freeze.py | 3fff42e415989cf8c6d8004b34ac0ff2990a6472247ce581ece33dee99541f6e |
| test_media_restore_freeze.py | 37e7ddec70c6688effa65dfddae01b6336e8c9ada80ef311d36704f39b2c49e9 |
| 2026-10-28-restore-window-v2.md | afd71f0cf84f132cfbdc787d2a36dfcc9c2e73672760373eb8258ab84cc135e5 |

独立报告 `/private/tmp/easel-restore-freeze-f1-r2-independent-20261028/review.md` SHA为 `e4c8f47780db17c446a74b133fa08fef365d5b5e871b202ec73a6aabc9442ec7`，safe SHA为 `f77852b146f2973f5e26e41ecaf137a888c5468e158a20178f589c7b0ca82fb7`。

实际SQL producer、当前时钟准入、W／P／D、真实paused P、PG／Linux及真实双库恢复尚未实现或验收。F2正在只读设计真实来源producer，必须使用同一只读snapshot读取原始lot／全部unresolved关联，不能调用会expire写入的CreditLedger.summary。没有SSH、真实用户capture、认证／owner环境读取、真实nonce、模型或资金动作，也没有部署到生产。此模块不在v29桌面公共源或中央已交付wheel中。
