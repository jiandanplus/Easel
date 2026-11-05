# Slow GET 限定诊断计划

当前状态为 OPEN。没有修改产品、原测试、deadline 或现有失败资源；本文件不是一次新运行的通过证明。

`THREE reviewed002` 的实际安全诊断在原 `test_media_async_worker.py:227` 联合断言得到 `request_count=0`、`cancelled_count=0`。容器8.37505s后退出1，无OOM，未超过原1800s期限。该诊断不足以判断请求为什么没有发出。

源码核对提供三个需要区分的路径：`MediaWorker.run_once` 先恢复关联，再执行 expire，再 claim；claim 的两次检查均要求 deadline 仍有效；获得claim后，execute还会按剩余deadline设timeout。任一路径都可能在GET前结束。不能只从0GET断言推出模拟执行速度、权限错误或生产代码缺陷。

若继续新增诊断候选，应仅在冻结夹具中记录有限的阶段计数与耗时：maintenance permit开始／获得、recover返回数量、expire返回数量、claim返回数量、execute开始／结束。包装器仍调用原方法一次，原返回值、异常和上下文必须原样传递；不能新增DB查询、打印工作行／DSN／异常参数，不能引入owner能力。GET方法与取消标志继续使用现有精确白名单诊断。诊断写入失败只能报告安全标志，随后保留原失败退出，不能改变成功／失败结论。

这类新增候选需先有纯函数负例和独立审阅，然后才可在新的专用环境执行一次。原函数AST、200ms deadline、20ms分片、1s timeout、25ms HTTPX配置、资源限制与原失败现场均保留。此处保留phase运行前的方案；当时尚未授权或运行，后续结果见下文。

源码依据：原测试文件SHA256 `5c430fdb13380c120d5369a1c4188416d3066be65e10855f137d5837421ceaa4`；当前worker SHA256 `9d6ab06a137b6b24c79fd1f0b62a4f4e4fe1335327cfc56e83598b7f0245dc04`；async_store SHA256 `8b627500f28341deb372de84cc9c0a3d210ac2fc1e55dffbbafec320809c962d`。产品包对应字节仍需新候选沿用冻结清单核对。


## 新阶段诊断候选与实际执行状态

新的phase候选已冻结，manifest `3fd6f0c66e411636da316da9dd2ebf07745be037c7a9e1efdd1783ed6e3ca83d`，runtime `539f081af511674c4c333148e55d5faa6e977494d5da45e11a5f28e88645e4f4`。Root已核全部163来源pin并读完整delta；独立复审12项新pure、7项pool回归和8种实际CLI隐私场景通过，报告SHA256 `80402af8c90bd06497c274bd9de519ec5bfc68c377f58c7096e5692b0d8d7c78`。记录只含7个固定标签、最多32个事件、有限整数相对微秒与实际计数；未知defer标签映射仅作用于诊断，不改变原参数。原decorated方法仍调用一次，返回值、原异常、permit与元数据保留；observer时钟有测量开销，不能将嵌套耗时相加或当成PG deadline起点。

经新独立授权，唯一新批次 `/private/tmp/easel-b2-family-slow-phase-run-20261031-reviewed001` 已完成公开构建并首次运行 `async-slow-get`。该批次executor先报告exit1/noOOM、原期限内失败，停止保留现场；随后Root完成严格phase回执核对，结果见下文。未改200ms／20ms／1s／25ms，不重试，不读取rawstderr或业务快照。此批诊断不能算原节点通过；实际后置资金／unknown／physical状态在没有原成功回执时仍为null。


Root现已独立核对14个固定安全哈希、同一已停止CID与原期限，并严格验证phase DTO。实际阶段为maintenance enter／acquired，recover_associations返回0，expire返回1，claim返回0；未观察到execute／defer，原GET0／cancel0。16份safe归档清单SHA256 `9dfd248f3c4aae0c00d354e0b1e0f397fa1e027398be82be152da25f2c8fa161`。没有再次读取stdout／stderr，也没有新增业务或私有DB读取；后置资金／许可／physical仍null。

进一步静态核对发现夹具setup使用 `MediaService(GuardedDatabase(self.db), identity)`，而产品构造器自身再包一层GuardedDatabase；原测试setup只传raw Database，故原结构是一层。每层会在同一个连接上执行permit检查，这一偏差应独立恢复。下一候选仅准备去掉setup传参的冗余外层，保留产品自身guard、原sql上下文guard、mutation／validate／control DB／pool／所有时限和断言。此处是single-guard执行前的静态方案，不保证消除200ms下的失败或证明旧因果；后续实际结果见下一节。


## 恢复原单层结构后的新样本

新single-guard候选经完整195来源pin与独立行为／回归／隐私复审后，首次在新专用环境执行。executor报告容器约7.61s后exit1/noOOM，未超过原期限；原227行仍GET0／cancel0，严格阶段诊断仍是expire返回1／claim返回0，未观察到execute/defer。现已停止并保留。Root已独立核22个固定安全pin、同一CID及原wall，24份安全归档清单SHA256为`c20460c2a465c91c2a2c8096ea0f452dc0717927a5d7df042d13acab2b81642f`，位于`outputs/_run/b2-development-20261030/slow-single-guard-reviewed001-root-20261031/`。本轮不再继续没有依据的时钟、连接池或guard实验。

这一结果将两件事分开：夹具构造已恢复原测试的单层产品guard，但原200ms下的GET取消路径仍未通过。不能将过期处理路径当成GET取消通过，也不能据此确认此前的根因或当前生产代码存在缺陷。完整family门仍OPEN。
