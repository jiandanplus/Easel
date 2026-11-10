# 已结算图片的非空备份与恢复

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans。按任务验证，服务器执行仍须 root 审核固定脚本 / 新 nonce / 具体计划 SHA。

**Goal:** 在两张 M5 实际图片完成后，为已结算媒体、typed HTTP 证据、共享点数账本、原始文件和 11 个配置提供新的非空恢复证明。

**Architecture:** 新增独立 settled-media 工具，保留初始空窗口工具 `68dccf…`、wrapper `af1906…` 和旧 timer。沿用固定 v6 核心，源库只读导出快照，registry 和原 owned 文件在同一快照窗口绑定。两模型 disabled、无执行中 / 待核算 / GC 占用、预算全部释放构成显式静止窗口；不停止文本 / 登录 API。恢复从新密文重读认证，仅对新隔离目标进行原七 CHECK 精确修复，其他 schema / 约束 / 角色 / owner / ACL / 数据 / 财务全部严比。

**Tech Stack:** 冻结 f50e size-v2 / 001–016、现有 Python 标准库 / 加密 helper、指定 PostgreSQL 16 镜像，fixtures 使用独立本机 PG 和 MockTransport。

## 固定约束

- 代码权威为 `/private/tmp/easel-media-m1.1-size-staging-20261027` 的 f50e wheel，不能使用共享 repo 后加的 017 作为源迁移集。
- 本地开发不操作 154、模型、服务、用户会话、真实供应商或付款；不得先执行新 DR、修改价格、发点、延长 TTL 或权益。
- 旧空窗口证明不能外推成实际非空媒体恢复。新工具、新私有目录、新 nonce、新密文，旧对象和 timer 全部保留。
- 捕获上限仍 32 MiB；超限、未知文件、`.part`、硬链接、符号链接、哈希 / 长度 / inode 变化、staged / GC 状态均闭锁，不跳过内容。
- 媒体 TTL 原样保存；规划 / 捕获 / 结束及目标窗口检查时，两个原 blob 的 expires_at 必须仍距当前至少 30 分钟，使 15 分钟有界演练不进入正常 GC 候选区。
- 实际目标仍顺序执行 512 MiB / 0.75 CPU / 128 PIDs / network none / 0 端口，成功失败停止保留，不启动恢复应用。
- 完整 plan / registry / 角色 / SQL / 配置 / 明文归档只在 154 私有内存与目录；safe 白名单只出计数、哈希、布尔及必要操作标识。

## Task 1：合成的完整已结算记录

- [x] 新隔离 PG、固定 source 76 Python / SQL 和 16 迁移检查；`-I` worker 避免 module cache 混入 017。
- [x] 用真实平台 API、模拟 live internal grant 和 MockTransport，完成 flare / sunburst 各一笔 1 点任务，不访问真实供应商。
- [x] 两笔 settled / completed / available，amount=actual=1,000,000；钱包 issued10m / spent2m / available8m / held0，预算已释放且 remaining0。
- [x] 两张实际 1024×1024 PNG、各 >1MB，registry / 文件 / owner / SHA / 权限一致；typed New API references / terminal evidence / receipts 各2、provider_response_id NULL；禁止跨 owner 读取。
- [x] fixture 的连接元数据拒绝非本机、非专用库和 PG query options；源仅 127.0.0.1 随机端口，guard 限制资源和 nonce / image / volume。测试及失败对象停止保留。

## Task 2：新静止窗口与快照文件绑定

- [x] 先红后绿实现 settled_window：两 model disabled / 各1 job / 2 invocations / 2 settled jobs / 2 artifacts / 2 attached blobs / 2 released budgets / 无 assets / 无 active jobs / 无 pinned / 无 GC claims。
- [x] 强校验每笔 n1 / size1024 / noassets / live / test_onlyfalse / 1point、invocation held0 / op1 / spent1point、真实 typed HTTP 终态与回执、PNG 属性和 TTL。
- [x] 实际非 public schema 反例先失败；窗口 SQL 显式 SET LOCAL search_path，保持原 media_storage_pinned 函数和严格 pinned 断言不变。
- [x] 完整同 snapshot registry + 文件 capture / fresh live recheck / SHA + inode / window hash 对比通过，任何 drift 拒绝。
- [x] 本机 durable ciphertext → 新空目标 PG / 文件 / 11配置严格恢复，目标 settled / typed 查询与原 registry 再对比；缺任一实际证明不能生成 nonempty success。26 passed / 1 个额外旧handle检查显式skip / 29.89 秒，75表529约束82行、两PNG2101644字节。
- [x] 生成独立 safe-plan wrapper 和13项红绿测试，复用 af190 白名单规则、继承调用venv、拒绝旧emptyplan，不外发 DB / user / role / config / SQL。
- [x] 输出166行DR / 42行wrapper最小diff和固定49df… / 356667… SHA；独立完整46pass1显式旧handle skip33.73秒，9输入前后hash相同，无已知限定阻断。独立safe SHA `e4450eeb0513280077c75b60d6cb562b90866d29a8f9b5669a9207b13b48759f`。本阶段未上传或执行154。

## Task 3：M5 真实图片后的服务器准入

- [ ] root 提供 M5 两个真实任务均已 settled 的核验结果；不得把预期数量当事实，若仍 pending / 未交付则停在新计划之前。
- [ ] root 单独批准两 model 临时 disabled / idle 窗口及新 nonce 的只读计划；不改用户登录 / 会话 / 钱包 / 价格 / TTL / 原服务。
- [ ] 真实 gate 检查 2 jobs / invocations / artifacts / blobs、typed 证据、预算与 held0、无 GC lease / partial / unknown file、原 PNG 可安全读取、源 configured schema / release / 16迁移 / 11配置 / 实际进程一致。
- [ ] root 审核具体 tool / wrapper / nonce / plan SHA 与资源 / owned targets absent，之后单次有界新 DR；成功或失败只观察原 handle，不重派。
- [ ] 新真实非空 proof、独立源 / 目标 / 密文 / 权限后验及原余额精确保留证据通过后，由 root 按单独批准流程恢复模型启用。
- [ ] 覆盖日常媒体状态（包括 GC / `.part` quarantine）、更大容量和定时备份升级另审；本工具只覆盖明确静止的两张 settled 图片，不声称通用日常备份或 PITR。
