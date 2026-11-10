# 媒体恢复计划安全输出 wrapper：独立审查

日期：2026-10-27；独立 reviewer：`delivery_media_review`。

**结论：冻结 wrapper 未发现剩余已知代码阻塞；独立纯合成测试 12 passed、0 skipped，0.02s。** 本次不执行 SSH、154 服务器操作、Docker 或生产维护命令；不修改原 DR、M4 或桌面发布源码。

## 冻结输入

- `services/platform/scripts/capture_media_restore_plan.py`：SHA256 `af1906bf0a0d012f3033407e4a41ac136b3b37c5746dd2891ebf29a6ba43bd2b`。
- `services/platform/tests/test_media_restore_plan_capture.py`：SHA256 `452b373ff7dd3baf83917f351c5e6d0d73357ec65c80536861e3015c0743e91e`。
- 原 `verify_media_restore_drill.py`：SHA256 `68dccf3699a3c9b06d79e5cf87609dcf567b2d6c93c8b3c6186cc40c3474e1d3`，未改变。

三个文件在执行前后 hash 均相同。此 wrapper 解决原 plan 输出含固定数据库/用户元数据的问题，保持已审核 DR 本体不变。

## 读审的边界

1. 生产入口限定 root 与固定主机名，并检查原 DR、祖先目录的 owner/mode/类型和固定 DR SHA。
2. 子命令固定为 `/usr/bin/python3 -B <固定DR> --nonce <32位小写hex>`，没有 apply 参数、shell 或任意脚本/目标输入。坏 nonce 在启动子进程之前拒绝。
3. 子进程 stdout/stderr 仅捕获到当前服务器进程内存，不透传原文。失败、超限、重复 JSON key、nonce/DR SHA/完整 plan hash 不符均拒绝，CLI 只输出固定失败消息。
4. 完整 plan canonical bytes 仅在已有 0700 私有目录保存，0600、O_EXCL、NOFOLLOW、fsync 文件与父目录；同 nonce 的已有文件/链接不能覆盖。
5. 对外结果采用显式字段白名单。source 只给 kind/id/image_id，target 只给 kind/name/volume；省略 database/user/role、原配置字典 keys、迁移详情、原 argv/environ。配置和迁移只保留总数量与 aggregate SHA；进程信息只给 PID、command SHA 和布尔核验。

文件写入/fsync 失败时可能保留 0600 局部文件，命令仍拒绝且不覆盖同 nonce；不能将失败当成完整计划保存成功。

白名单依赖 SHA 固定 DR 当前的受控字段合同，不接受模型、浏览器或任意外部 JSON 作为计划来源。测试包含嵌套合成 private sentinel，确认额外私有字段及嵌套资源/进程详情不会被转发。

## 独立执行

```text
12 passed in 0.02s
0 skipped
exit_code=0
```

测试仅注入 synthetic 子进程结果，不运行实际 DR 子进程。覆盖精确 argv、捕获策略、0700/0600/独占文件及计划 hash、嵌套输出白名单、子进程或输入校验失败不创建文件/不泄漏诊断、无效 nonce 不启动子程序。

日志：`/private/tmp/easel-ops-independent-review-20261027/plan-capture-wrapper-tests.log`。机器证据见本目录同名 safe JSON。

## 审查时的生产阻挡状态

以下为主任务在本次审查时报告的状态，非 reviewer 现场查询：stop＋apply 的整条 SSH 命令被自动审批拒绝，命令未执行；拒绝原因是尚无针对当前生产维护窗口与数据库迁移权限副作用的具体人类批准。主任务已经向用户请求具体批准，仍待回复。

原服务保持 active R2，新的 media.json 尚不存在，生产 mutation 为 0；只读 cutover plan 已生成。不能通过换命令、换 agent 或将此 wrapper 解释为批准来绕过拒绝。这个 wrapper 是后续已批准流程的安全计划输出准备，不授予维护或迁移权限。
