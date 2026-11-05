# 媒体存储配额与回收：M1 后续收口

范围是中央媒体数据面的服务器私有文件，不改变共享点数账本、付款或供应商准入。媒体仍默认关闭，不产生真实模型请求。所有操作继续验证当前中央 Scope；存储配额按 user + tenant + billing account 跨产品合并，其他用户分别计算，同时共享服务器全局上限。

## 唯一数据库登记与上限

新增 015 迁移，创建 `media_storage_policy` 和 `media_blobs`。policy 默认单 owner 256 MiB / 128 个文件，全局 2 GiB / 4096 个文件，TTL 24 小时；runtime role 只能读取 policy，不能提高配额。测试在独占 schema 通过 owner 配置 TTL 1 秒，避免修改不可变内容记录。未来变更上限必须走受限运营变更。

付费输出在同一准入事务、金融预留之前先保留模型最大产物字节和请求图片数量的 `media_storage_budgets`；并发上传与新任务不能抢掉已接受任务的输出空间。实际产物 stage 把剩余预算原子转为真实占用，不双计；真实终态证据持久化或派发前取消后释放未用预算，未知任务保留。准入先锁 billing account，再 invocation，再 storage advisory，避免 storage→wallet 反向等待。

blob registry 的 `storage_key` 唯一，绑定不可变 SHA256、字节数、MIME、真实解码属性、owner、invocation、可选 job / attempt；阶段为 `staged → attached → gc_pending → deleted`，也允许未完成写入的 `staged → gc_pending`。所有非 deleted 行都占用配额，软过期或已经申请删除不会提前释放。INSERT 触发器在同一数据库事务中取全局存储 advisory 锁、读取 policy 并检查 owner / 全局 bytes 和文件数；多个进程或同一用户多 invocation 不能并发突破上限。当前最多 4096 个活动 blob，查询有 owner / state 索引，适合现有小规模服务器；未来扩展可换为同语义计数器。

真实图片解码后，先 stage 配额并持久化随机文件键，再写私有文件，最后登记 asset / artifact 并原子 attach。这样进程在写前、写后或元数据提交未知时退出，都保留可恢复的 registry，不生成未计量孤立文件。已有直接内部 register_asset / register_artifact 调用仍通过同一 registry / 配额校验。已登记同 ID 同元数据幂等，不二次计费或二次占用存储；物理键不能跨任务、scope 或内容复用。

## 过期与任务 pin

到期后查询和下载返回过期，不能继续作为新任务输入。已接受的 queued、running、cancel_requested 或 unknown 任务仍 pin 它实际引用的素材、产物和输出 staging；供应商超时与待核算不允许按固定 TTL 删除仍可能需要恢复的数据。真实终态 completed / failed / cancelled 已持久化后，才允许按到期政策回收。财务 pending 不冒充执行失败，终态证据与完整账本保留。

`prepare_gc` 对到期候选 `FOR UPDATE SKIP LOCKED`，再次检查当前任务 pin；资产表、产物表先写 `purged_at`，registry 转为 `gc_pending`，同一事务提交。SQL 触发器再次验证到期、父任务与不可变内容。准入读取 asset 使用 FOR SHARE；若准入先持有锁，GC 等待后重新看已提交 job pin，不能越过其读取。GC 不先取钱包锁或存储全局锁，避免与准入的 wallet/invocation/asset 路径形成环。

artifact 下载改为服务端内部 `read_artifact(scope,id,reader)`：保持内容行 FOR SHARE，直到私有文件安全 snapshot 完成；GC 的 FOR UPDATE 在其后执行。输入文件在任务准入后已有 job pin，执行过程读取不与过期回收竞态。

## 幂等物理回收与崩溃恢复

`claim_gc` 只领取 gc_pending 私有记录，持久化随机 gc_token 和短租期。文件删除只使用 registry 的固定 key，复核 owner-only 根目录、NOFOLLOW 普通文件、0600、大小和 SHA256；内容或路径被替换则保留待处理，绝不猜删或扫描其他文件。final 与精确 `.part` 名称都不存在才视为幂等缺失。若断电留有完整 `.part`，仍必须满足预期字节数、SHA256、UID、0600、普通文件和 inode 检查后才能清理。不完整或不可核验的 `.part` 保留为 GC 失败并继续占用配额，不凭固定文件名无条件 unlink，也不 ACK。

此类部分文件的恢复由私有受限运营完成：先暂停该 blob 的 GC 重试并保留 registry、文件和日志快照，核对完整 owner / invocation / job、不可变 storage_key、实际 inode / UID / mode / 字节数，确认没有活跃或未知任务 pin；如无法证明完整散列，不把它改名为正常产物。经针对该确切临时文件的删除授权后，只移除已核对的私有部分文件，保留原 registry 和审计，再由 worker 在租期后确认 final / part 均缺失并完成 ACK。禁止直接改 `deleted` 或降低存储计数，禁止扫描猜删其他文件。

unlink 并 fsync 后，`finish_gc(key,gc_token)` 才标记 deleted、释放配额。标记后崩溃但未 unlink，后续 worker 重新领取；unlink 后、ack 前崩溃，后续 worker 看到 ENOENT 后确认；旧租期 worker 不能确认新 token 的记录。失败保留 gc_pending 并在租期后重试，不影响账本和任务原始证据。

worker 的 `run_once` 串联有限数量 GC 与原财务恢复；两者都不调用供应商。默认 closed 不启动媒体 worker。不会为了回收去删除本机旧工作台、桌面 outputs、预览库、原有 New API 或没有本版本 registry 的文件。

## 验收

先失败测试再实现：跨 invocation / 跨产品 owner 配额、多个 concurrent 请求仅准入一个、全局额度与其他 owner 隔离、配额拒绝发生在文件写入和付费 POST 前、到期 queued/unknown pin、准入/下载 FOR SHARE 与 GC 竞态、GC 两个崩溃窗口幂等、错误 hash / symlink 不被删除、已删除才释放存储配额。复跑既有 M0/M1、文本金融和 runtime permissions，再重新构建服务 wheel。真实供应商媒体验收仍需新输入，不以 fixture 冒充。
