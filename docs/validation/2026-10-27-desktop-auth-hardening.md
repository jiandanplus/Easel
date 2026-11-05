# 桌面认证护栏候选

本次只修改本机桌面源码，没有修改服务器、独立 f50 服务发布候选、原 v22 运行包或真实用户 auth／状态。实际原事件尚未完全归因：服务器只读证据显示会话未撤销、最新 refresh 未被消费，不能把此前 loopback 的响应丢失机制认定为实际原因。

## 行为与权限边界

新增 `easel/platform/auth_refresh.py`，由中央和媒体客户端共用，保留原跨进程账号文件锁。刷新 POST 前先原子写入并同步持久化围栏；成功响应通过原 scope、账号及字段校验后，新 auth 与围栏清理一次原子替换。若传输、正文、账号匹配或保存结果未知，保留围栏与原账号信息，返回 `refresh_uncertain`，`authenticated=false`。重启也不会再次提交原 refresh。

服务端的 access 缺失、消费或过期均可能返回 `session_invalid`，同一 code 也用于真实会话失效。因此 access GET 的 401 不能直接证明 refresh 失效。中央和媒体只读 GET 在原 URL／scope 匹配、无未知围栏时可强制续期一次，只重试同一个 GET，沿用原绝对截止时间。刷新自身的明确认证 401 仍严格清理账号和围栏，未放宽服务器撤销或权限检查。第二次 GET 401 不会触发第二次续期，也不会据此删除已保存的新 refresh。

媒体 GET 修复会同步更新宿主 snapshot 中的 product access，后续 mint 使用新 access；生成 POST 仍只使用其短期 task token。另一客户端已在锁内轮换时复用当前新 access。scope／账号变化和迟到旧 401 不能删除新登录，不能为新账号继续旧任务。

订单、mint、生成等非 GET 请求不因 401 自动重发。task-token 401 不触发 product refresh 或删除 product auth，原未知操作及其键继续保留。

明确点击登录可以清理与当前账号匹配的未知围栏并重新认证；若围栏属于另一个已改变的账号则拒绝丢弃新账号。明确退出本机也可以清除未知状态，不重放旧 refresh。未知结果下的本机退出不代表服务器未知令牌对已被撤销。

诊断只输出 `code`、`status`、固定路径、`scope_hash`、时间五个字段；不记录 token、refresh 哈希、正文、异常原文或带 query 的 URL。围栏中的凭据哈希仅存原有受限账号文件，不进入诊断输出。

## 验证

- 首轮新增 16 项：14 项因能力缺失而失败，2 项原有刷新 401 严格清理对照通过。
- snapshot→mint 专项回归实际撤掉同步修复时，mint 明确使用旧 product access 而失败；恢复后验证修复 GET、单次新 access mint、单次 task-token 生成。
- 另以真实旧围栏／新账号组合观察到 `begin` 错删新账号红测，再加绑定校验修复。
- 新认证护栏共 21 项，包括 marker 写失败零 POST、完整响应保存失败的持久围栏、重启零重放、scope 变化、退出／重新登录出口、task-token 401 和原截止时间。
- 完整中央原生客户端：`rtk proxy env PYTHONPATH=. .venv/bin/python -m pytest tests/test_central*.py -q --tb=short -p no:cacheprovider`，326 项通过、4 项跳过，6.22 秒。四项仅为需要显式 legacy Codex binary／Node 环境的原有可选测试。
- 使用真实 loopback HTTP 的响应停顿／截断四组合现在均在一次 refresh 后被持久围栏阻断，不再自动重放。改动前 d922 基线测试归档为 `assets/2026-10-27-media-refresh-response-loss-baseline-tests.py.txt`，原定位报告保留当时证据。

7 个源码／测试文件 SHA、产品补丁 SHA 和 root 的 Pi 两文件 SHA 单列在同名 `-safe.json`。产品补丁在 `assets/2026-10-27-desktop-auth-hardening.patch`。这组 326 不叠加 root 单独验证的 Pi 测试，也不代表新安装包已签名、已公证、已激活或已完成真实模型验收。

root 另行修复 Pi 媒体工具 content 只含 text 的问题，使已清理的结构化操作号／状态可被模型读取；该改动在 v23 候选中单列。服务器真实 Flare 的后续存储故障由 root 和独立 reviewer 另行处理，不归为本次认证修复。
