# 原轮查询的 request hash 确认修复

状态：作者限定候选已完成 RED／GREEN、类型检查与一次完整前端回归；小白名单证据已归档。**独立审源及15项聚焦执行通过，限定结论 APPROVE_A2_REQUEST_HASH_GUARD_ONLY；未构建或激活新包。**

[安全回执](2026-10-28-workbench-query-hash.safe.json)记录候选、计数及证据边界；[作者报告](../../outputs/_run/workbench-query-hash-20261028/implementation-review.md)与[本次净生产补丁](../../outputs/_run/workbench-query-hash-20261028/App-only-task.patch)保留完整说明。

## 问题与改动

原 `queryOriginalTurn` 已经通过实际 API 的 version／session／turn／receipt 格式验证，后端也有当前 source／session 归属守卫。缺口是 accepted 分支只比较本地 clientTurnId，没有核对本地 pending 的内容指纹。正确身份字段、相同 turn 但不同 request_hash 的合成响应，能让真实 store 清空提交 revision 的文字／附件；若已输入下一版，则正文／附件受 revision guard 保留，但原 confirming 及 requestHash 关联仍被删除。

这是纯合成协议反例，不证明生产已经发生，也不否定已有身份守卫。它属于原已批准 A2 的“可校验该请求确认”要求，与尚未批准的工作区迁移设计独立。

生产改动只有 `App.tsx` 的 **4 行新增**：当 accepted 对应本地 pending turn 时，requestHash 缺失或与响应不相等，就保持 blocked 的“请求状态待确认”并立即返回，不 acknowledge、不追加 terminal、不自动 resume。原正文、附件、revision、confirming、turn／hash 不变。没有本地 submission 的历史查询及查询另一原 turn 仍兼容。API parser、后端、onstream accepted 回调和其他 controller 均未修改。

| 候选文件 | SHA256 |
| --- | --- |
| `web/frontend/src/App.tsx` | `475b2d4263d10cb278c99cfbb62d74abdf396dc4160a8dd0106add9d29191450` |
| `tests/frontend/queryOriginalTurnRequestHash.test.mjs` | `a515993310096395d4fc3009dee48e20613d5d2bf3756f8d53c27540f9bbe593` |

## 实际验证

新测试从真实 App AST 原文抽取 callback，连接真实 `createComposerDraftStore` 和完整实际 `api.fetchChatTurn`，实际经过 `checkedChatTurn`／`acceptedReceipt`。只有 fetch 返回合成内存响应、Storage 使用新 Map，React／UI／resume 依赖注入；没有手写镜像 callback，没有执行真实 resume 实现或网络请求。

| 阶段 | 实际结果 | 证据 |
| --- | --- | --- |
| 修复前新回归 | 15 total，10 passed／5 failed／0 skipped，退出 1 | [RED 日志](../../outputs/_run/workbench-query-hash-20261028/red.log)、[命令回执](../../outputs/_run/workbench-query-hash-20261028/red.run.safe.json) |
| 修复后同一回归 | 15 passed／0 failed／0 skipped，退出 0 | [GREEN 日志](../../outputs/_run/workbench-query-hash-20261028/green.log)、[命令回执](../../outputs/_run/workbench-query-hash-20261028/green.run.safe.json) |
| 类型检查 | TypeScript 退出 0，两配置 noEmit | [typecheck 回执](../../outputs/_run/workbench-query-hash-20261028/typecheck.run.safe.json) |
| 唯一一次完整前端 | 55 个文件，529 passed／0 failed／0 skipped，13.750 秒，退出 0 | [完整日志](../../outputs/_run/workbench-query-hash-20261028/full-frontend.log)、[完整实际 argv](../../outputs/_run/workbench-query-hash-20261028/full-frontend.run.safe.json) |

15 项覆盖匹配 accepted／completed、匹配 r+1、错误 hash 的 accepted／completed 与 r／r+1、本地缺 hash、实际 API 拒绝 null／无有效 hash及错误格式、rejected 两向、迟到 source scope、另一原 turn、不带本地 submission 的历史查询。RED 的 5 条失败正是 4 条错误 hash 与 1 条本地缺 hash，其余对照当时通过。

这些计数有重叠，不能相加。529 是本次新候选的完整前端结果，包含新 15 项；旧 514 保持旧结果身份。没有在归档阶段新跑测试／类型检查。随后独立审读另执行同一15项，15 passed／0 failed／0 skipped、438.196ms、退出0；App、test、API、store四个实际输入前后SHA相同。独立报告核实guard位于acknowledge／终态追加／resume之前，原source迟到与rejection守卫保持，无本次限定阻断。见[独立报告](../../outputs/_run/workbench-query-hash-20261028/independent/review.md)、[实际运行回执](../../outputs/_run/workbench-query-hash-20261028/independent/run.safe.json)及[独立复制清单](../../outputs/_run/workbench-query-hash-20261028/independent/copy-manifest.json)。这是独立聚焦执行，不是重新跑529或中央全量。

## 中央全量与当前源码的身份边界

旧 [中央完整验证](2026-10-28-central-current-f1-f2-av-full.md) 的 2,475 项结果为 2,468 passed／7 skipped。它绑定当时的 **1,333 路径冻结输入**，其中 App SHA 是 `65371dcef20b660360ade324015c3f8c13284713d61d8f9d4723076790b31615`；本次 App 已变为上表的 `475b…`。**2468 不是新 App 候选的完整冻结快照结果。** 中央代码未由这次 bugfix 修改，未重跑 PG 或中央全量，不把旧完整结果重新标为新快照通过。

作者阶段另记录原 1,333 公共路径、完整前端与当时设计 spec 的并集：before 1,334／after 1,335，只旧 App 改变并新增测试；v29 build inventory 与 11,932 Pi payload 文件 SHA 当时前后一致。原始大闭包仍在 tmp，用[归档清单](../../outputs/_run/workbench-query-hash-20261028/copy-manifest.safe.json)中的**绝对路径＋SHA**引用，未重复复制或声称是新备份。

作者冻结之后，root 已继续编辑 managed-workspace continuity 设计文档。安全回执保留其作者阶段与归档时 SHA，明确二者不同；不把作者阶段“其他闭包路径未改”延伸为当前全树未变。该设计仍是设计阶段，本次没有实现 profile／permit／backing。

## 持久证据与未验证范围

`outputs/_run/workbench-query-hash-20261028` 白名单复制 **12 个文件、94,792 字节**；加 copy manifest 后 **13 个文件、99,679 字节**。源复制前后与目标逐项 SHA 相同。没有复制约 4 MB before／after 快照、大 git diff、cache、App、ZIP 或 Pi payload。原 tmp 闭包与最初独立 RED 证据保留，引用不等于持久复制。

- copy manifest SHA256：`3518ce8c44af2b6ea5a8d49fd1a56c9a523e1b2e7cfef56e4ec536cc4d0386b2`
- 本文安全回执 SHA256：`caaca6986c43c0e0bc99facceec91b2e333b6aa475c2b462be6f5f862a6ea96d`

未做新浏览器、真实回执／供应商或新包运行验收；原 17868 未访问或改变。没有 HTTP／PG／SSH／模型／auth／真实 storage、HOME 覆盖、Vite build、App／ZIP 构建、commit／push。归档只新增本验证文档、安全回执和小证据，不改产品、主计划或设计 spec。作者安全回执中独立审读pending是归档当时状态，原字节保留；后续独立结果单独追加，不把本修复等同迁移设计批准或整项交付完成。
