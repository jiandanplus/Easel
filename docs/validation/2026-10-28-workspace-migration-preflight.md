# 工作区迁移前提：真实草稿 store／hook 的 scope 与 origin 浏览器验证

状态：本机合成浏览器预检已完成。root 用实际 CUA 验证了两条边界：同 origin 改变 workspace scope 后不显示旧草稿，返回旧 scope 可恢复；保持 scope／session 相同而改变 origin 后也不显示另一 origin 的草稿，返回原 origin 刷新仍保留。**没有实际迁移用户数据，也没有把这个结果记为完整 App、ChatPage、生产附件归属或 native 验收。**

安全回执为 [workspace-migration-preflight.safe.json](2026-10-28-workspace-migration-preflight.safe.json)。小白名单证据已持久归档到 [workspace-migration-preflight-20261028](../../outputs/_run/workspace-migration-preflight-20261028/README.md)。主交付计划由 root 单独维护，本次未修改它。

## 实际浏览器结果

使用两个仅监听 loopback 的新 origin：`http://127.0.0.1:57966` 和 `http://127.0.0.1:57967`，固定合成 session 为 `synthetic-migration-session-20261028`。所有文字、附件、账户标签与 scope key 都是本夹具新建的合成数据。

| 操作 | 实际结果 | 原始证据 |
| --- | --- | --- |
| Origin 57966／A 点击预置，保存合成文字与 1 个附件 | 已保存；文字精确；附件显示 `available` | [保存后截图](../../outputs/_run/workspace-migration-preflight-20261028/origin1-A-saved.png) |
| 同 origin 切换到 B | workspace scope 改变、session 不变；文字为空、无附件 | [B 截图](../../outputs/_run/workspace-migration-preflight-20261028/origin1-B-empty.png)、[AX](../../outputs/_run/workspace-migration-preflight-20261028/origin1-B-empty.ax.txt) |
| 同 origin 返回 A | 原文字与附件恢复；再次请求合成附件验证并显示 `available` | [返回 A 截图](../../outputs/_run/workspace-migration-preflight-20261028/origin1-A-returned.png)、[AX](../../outputs/_run/workspace-migration-preflight-20261028/origin1-A-returned.ax.txt) |
| 在 origin 57967 打开同一 A，未预置 | workspace scope、source scope 与 session 都和原 A 相同；文字为空、无附件 | [第二 origin 截图](../../outputs/_run/workspace-migration-preflight-20261028/origin2-A-empty.png)、[AX](../../outputs/_run/workspace-migration-preflight-20261028/origin2-A-empty.ax.txt) |
| 回到 origin 57966／A 并刷新 | 原合成文字、1 个附件仍在；已保存，附件再次显示 `available` | [最终截图](../../outputs/_run/workspace-migration-preflight-20261028/origin1-A-final.png)、[AX](../../outputs/_run/workspace-migration-preflight-20261028/origin1-A-final.ax.txt) |

[root-browser-state.json](../../outputs/_run/workspace-migration-preflight-20261028/root-browser-state.json) 记录 15 条浏览器观察、11 次 contract GET、3 次合成附件 validation POST、**0 次 task POST**。另外 2 次 task GET 是准备阶段有意验证拒绝边界，均返回 403；没有把它们算成任务提交。15 条观察包含加载和验证中的中间态，不等于 15 个独立案例。3 条稳定 A 观察分别对应首次保存、返回 A、最终刷新，均为文字精确、已保存、1 个附件与 `available`。

这次结果把“只复制磁盘工作区尚未覆盖浏览器草稿接续”的静态判断变成了限定场景的实际浏览器证据。它证明旧合成草稿仍可在原 scope／origin 下恢复，不是原草稿被清除，也不是自动跨 scope／origin 迁移成功。正式迁移前仍需给出文件态与浏览器草稿／附件接续的可审查方案；本次没有实现或选择产品迁移方案。

## 使用了哪些实际产品逻辑

夹具只读导入真实 `useComposerDraft`、`composerDrafts` 与 API 客户端，渲染最小 composer UI。文字与附件保存在浏览器真实 localStorage，未 mock Storage；只读取固定合成 scope／session 的精确键，UI没有枚举真实浏览器存储。页面导航使用实际 hook 的卸载保存及重新加载行为。

A／B scope 由 v29 App 内 `easel/workbench.py` 的真实 `WorkbenchState.contract`／`reference_id` 生成，使用两个新绝对路径、相同固定合成 key 和 source。没有复制或重写 scope 算法。A／B 的 workspace scope 分别为：

- A：`3a66713765f6f3ee9db90e873851579972e572afa65ffe60920c9c3e5ec56185`
- B：`4fedd67ca89da1d967695beaa1fd4f46b93d422de0790fdae3bc7e487dcac0d5`
- 两者共同 source scope：`e407b71aaeca3eb7e2988eeb40a7ed0d0b73f21a63f0318c4e7d51f45082ef99`

这些值仅属于本夹具合成数据，可以公开。实际包内函数把绝对 workspace 路径纳入 workspace scope；真实前端 store 再把 workspace/source scope 与 session 纳入草稿键。

以下 5 个直接输入重新核对后，与 v29 `source-manifest.json` 精确相同：

| 输入 | SHA256 | 使用方式 |
| --- | --- | --- |
| `web/frontend/src/lib/useComposerDraft.ts` | `0cf64994f5eabdd4a5085234e22924471b0a216ad4a06611ba615e775ed33a55` | 实际导入 |
| `web/frontend/src/lib/composerDrafts.ts` | `cfbc599739ead07661a337d44ec828851e93b74ff81924a047c720aaa291839f` | 实际导入 |
| `web/frontend/src/lib/api.ts` | `728e17eed4d8d514d4849725a405f3057f5fb886343dc53901d0f980757f60b0` | 实际导入，发送合成附件验证请求 |
| `web/frontend/src/App.tsx` | `65371dcef20b660360ade324015c3f8c13284713d61d8f9d4723076790b31615` | 只核 SHA，未导入完整 App |
| `easel/workbench.py` | `c7e43e0a54d53e571c55d867446f79e21957a9432149e1055ac0bfd90acb26ac` | 从实际 v29 bundle 导入；与仓库对应文件一致 |

当前 ChatPage SHA 为 `5294c2d04e62140d89a6586e96cd43cc4cf9b66792c67984bb0fd86d939b1015`，不同于 v29 的 `c1a3fcfc499407b3daa5e3df5d915476c56fb647622b9c3088309700f0c1d227`，本夹具没有导入它，不能将最新 reconnect 源码算作 v29 整包运行。

附件端点为明确标注的合成 owner／session／path mock。实际 API 客户端请求、真实 store 的 availability 写入与 UI显示已经发生；**不据此证明 ChatPage 的生产验证 effect、真实附件后端归属规则或实际用户附件迁移通过。** 发送按钮始终禁用，没有任务提交 handler，没有调用 prepareSubmission。

## 隔离、HTTP 边界与清理

准备阶段最终完成两个 origin 共 16 项 GET 检查：首页、模块动态转译、scope A／B 返回 200；未知 fixture 返回 404；task GET、越界 `/@fs/etc/passwd`、敏感 `/.env` 返回 403。CSP 的 `connect-src` 仅为 `self`。Vite 使用 `configFile=false`、`envDir=false`、空 envPrefix、`publicDir=false`、严格 fs allow，仅允许新夹具目录、公开 frontend/src 和现有 node_modules；没有外部 fallback。没有运行 Vite build 或创建 App／ZIP／新 source manifest。

child 环境仅包含 `LANG`、`NODE_ENV`、`PATH`、`PYTHONDONTWRITEBYTECODE`、`TMPDIR`，HOME 完全省略，没有 provider／auth／`VITE_*` 变量。未访问原 17868、原 workspace、默认 Application Support 数据目录、PG、SSH、模型或真实账号。

root 已执行 stop.py。[cleanup.safe.json](../../outputs/_run/workspace-migration-preflight-20261028/cleanup.safe.json) 记录 supervisor 24677 的两个原始 Popen 子进程均已 wait：Node 24679 退出 143，backend 24678 退出 0，`stopped=true`。没有按猜测 PID 或进程名停止其他服务。新 origin 的合成 localStorage **保留，未宣称已经清除**。

准备阶段三次失败及各自清理证据原样保留：默认沙箱拒绝 bind；Vite 将 port 0 回退到已占用的 5173（未访问或终止原 5173）；不存在的 `/.env` 曾得到首页 fallback 200（未泄露 env 内容）。修正均只发生在新夹具目录，最终使用 OS 分配的新端口与显式敏感路径 403。原始准备回执中的“DOM待 root”是当时状态，本报告和 root-browser-state 记录其后的实际 CUA 结果。

## 持久证据与范围

白名单复制 **40 个小文件、371,181 字节**，逐项核对源复制前后及目标 SHA；加上 copy manifest 后为 **41 个文件、378,176 字节**。未复制约 5 MB Vite cache、workspace 目录、任何实际 `scope.key` 文件或新 keybox，也没有复制 App／Pi payload。原 tmp 与准备失败内容继续保留。

- [copy-manifest.safe.json](../../outputs/_run/workspace-migration-preflight-20261028/copy-manifest.safe.json) SHA256：`b75620857faacafc4406fc6285e74dda6a69bf8aaa2fe5da03a0ce13a872e083`
- [最终安全回执](2026-10-28-workspace-migration-preflight.safe.json) SHA256：`467cfa79159a8f98540027cd9d4fa0f0f0857b9388d11bf896c3c449d48590e4`

原 13 项审计输入、v29 上述 5 项相关 SHA 在归档时再次一致；没有改任何产品源码、现有包或旧证据。未重跑 514／2468 等已绿套件，没有新增 HTTP／浏览器／真实 App 操作。未重算整个 1333 源闭包或 11,932 payload 库存，不新增全闭包认证。独立干净设备、签名／公证、原生剪贴板、中文 IME、VoiceOver及正式用户迁移仍未由本次证明。
