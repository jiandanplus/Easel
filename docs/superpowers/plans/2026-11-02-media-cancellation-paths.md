# Media Cancellation Paths Implementation Plan

> **For agentic workers:** Use subagent-driven-development for the isolated test-helper task and requesting-code-review before Root-only resource execution. Each task below has its own verification boundary. No commit, push, merge or production action.

**Goal:** 按用户2026-11-02明确选择，分别验证排队过期和已经进入GET后的截止取消、点数／存储预留／租约安全。旧200ms测试和失败记录保留，不回写通过。

**Architecture:** 使用原real-PG setup_async、真实最小运行角色、原MediaWorker/MediaAsyncStore/维护许可及同一不可变数据库deadline。HTTP使用明确的MockTransport；一个可独立测试的parked AsyncByteStream证明已进入body、捕获真实CancelledError、finally和aclose，拒绝重复消费。没有假时钟、预先claim、改deadline或替换产品timeout实现。

**Tech Stack:** Python3.12 asyncio、HTTPX0.28.1、pytest/pytest-asyncio、原PostgreSQL20迁移夹具和最小权限helper。Context7已解析HTTPX0.28.1，Python3.12官方asyncio任务文档确认timeout通过取消实现、CancelledError需传播；不能仅由finally推导取消。

## Global Constraints

- 原分支codex/ui-redesign，不覆盖既有未提交工作；前端不改、不新增说明文字。
- 用户选择的是功能正确性；不再把入队后200ms内必入GET作为本项新验收的产品硬指标。原37节点历史账本保持原值，原slow失败独立保留；新两例独立命名、逐项映射后才能更新修订后的发布验收，不把它们伪报为旧函数通过。
- 不延长任何已有任务的deadline、lease或正式安全预算。新测试C2在INSERT时一次创建20秒deadline，外部30秒仅为测试故障收束，不能令产品任务超过原20秒deadline继续GET。原200ms/20ms/1s/25ms用例字节不改。
- 无供应商访问、POST、解码、模型、真实点数、生产配置或远程操作。只使用全新合成数据库和HTTPX MockTransport。
- 新helper/测试先代码及纯测试，再独审；不得凭unit通过宣称real-PG或Linux通过。Root唯一资源操作者，后续Linux候选需单独固定完整代码/权限/oracle/资源/nonce/期限。
- 所有成功路径都保留maintenance/mutation/DB guard。app只持最小业务连接；owner仅用于隔离seed和独立只读oracle。未知费用不结算、不释放、无重试生成。

## Task 1: 可观察的合成流及两条实质测试

**Files:**
- Create `services/platform/tests/b2_cancellation_cases.py`：只导入stdlib及HTTPX；产品及原setup_async在async case函数中按需导入，不在module import时访问PG/OS资源。
- Create `services/platform/tests/test_media_cancellation_paths.py`：引入既有legacy_media/legacy_db夹具，两个pytest-asyncio wrapper。
- Create `outputs/_run/slow-get-contract-review-20261102/codeonly/test_stream.py`：无产品导入、无网络/PG的unittest.IsolatedAsyncioTestCase，验证真实HTTPX合成流协议。
- Create同目录README/manifest及IMPLEMENTATION记录；不得修改旧测试、产品或其他harness。

**Interfaces:**
- `ParkedStream(deadline: datetime)`继承`httpx.AsyncByteStream`；字段`entered: asyncio.Event`、`entered_at: datetime|None`、`saw_cancel: bool`、`finalized: bool`、`closed: bool`、`consumed: bool`。第一次`__aiter__`要求真实UTC尚早于deadline，记录entered_at/entered，yield一次`b'{'`后等待永不set的Event；捕获CancelledError置位并原样raise；finally置位；aclose置closed。第二次消费拒绝。没有完成JSON或正常耗尽路径。
- `async expired_before_dispatch(db, legacy_media, monkeypatch) -> dict[str,bool|int]`。
- `async inflight_deadline_cancellation(db, legacy_media, monkeypatch) -> dict[str,bool|int]`。
- 两个case返回仅固定bool/count DTO，真实身份、DSN、行数据、deadline值不进入公开结果；函数中的assert负责实际因果和完整后置断言，不能返回硬编码pass绕过检查。

- [x] 先写parked stream纯反例：不能用finally-only作取消，重复消费拒绝、过期前置失败、未取消正常等待无closed、明确task.cancel或asyncio.timeout产生saw_cancel/finalized/closed，aclose必须实际执行；捕获实际RED后再实现helper。

```python
async def consume_with_client_stream(client):
    async with client.stream('GET', 'http://fixture.invalid/status') as response:
        async for _ in response.aiter_bytes():
            pass

def handler(request):
    assert request.method == 'GET'
    return httpx.Response(200, stream=probe,
                          headers={'content-type': 'application/json'})

async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
    task = asyncio.create_task(consume_with_client_stream(client))
    await probe.entered.wait()
    assert not probe.saw_cancel and not probe.closed
    task.cancel()
    with self.assertRaises(asyncio.CancelledError):
        await task
    self.assertTrue(probe.saw_cancel and probe.finalized and probe.closed)
```

`consume_with_client_stream(client)`必须是测试中完整定义的协程：在`async with client.stream('GET','http://fixture.invalid/status') as response`内迭代`response.aiter_bytes()`；handler只返回当前probe并核对GET，MockTransport不访问域名。

- [x] 实现C1：原setup_async创建真实job/attempt和association；在原job许可下INSERT media_work，以`clock_timestamp()-interval '1 second'`一次创建已过期deadline并RETURNING整行。原DDL允许初始过期deadline，不UPDATE历史deadline。不enqueue第二行。构造真实MediaProvider/MediaSettings/MediaWorker和MockTransport，handler记录method且任何HTTP均使断言失败。完整run_worker返回0；实际work变manual、lease_token/lease_until清空、last_safe_code精确media_recovery_window_expired、deadline和全部immutable binding字段与初始行一致；存储预算仍未释放，ledger reserved精确5；原claim heartbeat拒绝且deadline不变；无media_sources/observations/artifacts/terminal_evidence/receipts。使用同一个30秒故障上界，非产品期限扩展。

- [x] 实现C2：原setup_async后在原job许可下INSERT media_work，一次固定`clock_timestamp()+interval '20 seconds'`并RETURNING整行。默认合法worker_wall_seconds120不覆盖，不预先claim。使用ParkedStream(initial_deadline)和唯一GET handler。单一`asyncio.timeout(30)`包住INSERT后的完整test过程；创建run_worker Task，另建entered.wait Task，通过FIRST_COMPLETED确认body先进入且worker尚未完成；若worker先结束，立即因果前置失败，不猜为取消通过。读取实际claim行，只读比较immutable binding/deadline，要求非空原lease且lease_until<=deadline。

```python
done, _ = await asyncio.wait({entered_task, worker_task},
                             return_when=asyncio.FIRST_COMPLETED)
assert entered_task in done and probe.entered.is_set()
assert not worker_task.done()
await queue.heartbeat(claim)
# read actual work again through the original scoped runtime connection
assert after_heartbeat['deadline'] == initial['deadline']
assert after_heartbeat['lease_until'] <= initial['deadline']
assert await worker_task == 1
assert not worker_task.cancelled()
assert probe.saw_cancel and probe.finalized and probe.closed
```

实际heartbeat使用原queue facade、原job permit和原数据库，不换owner或伪造成功。成功路径禁止test主动cancel worker；只有error/finally中才cancel尚存的task并gather，所有失败保持失败；entered_task也需收束。任务最终必须因原20秒deadline而manual，不是外部30秒故障超时；验证真实PlatformError/lease丢失的heartbeat拒绝、reserved5和未释放budget、immutable binding/deadline、零delivery/settlement表、唯一GET/零POST以及permit/physical收束。若迟到、未入body、未执行heartbeat、只有finally或aclose、worker被外部cancel，均不通过。

- [x] 所有实际SQL使用原Runtime permit作用域；任何deadline不可变负例只能在独立事务中尝试且要求原SQLSTATE42501或23514，并验证回滚后完全不变。不要吞任意异常作为通过。
- [x] 运行纯测试并封存source/test hash：`rtk proxy services/platform/.venv/bin/python -I -B -m unittest discover -s outputs/_run/slow-get-contract-review-20261102/codeonly -q`。记录真实RED/GREEN，不运行pytest collection、真实PG或任何harness main。AST/compile核对旧测试SHA5c430f...不变。

## Task 2: 独立代码审查与Linux集成

**Files:** 后续新独立causal Linux overlay，不修改physical3包。复用实际最小角色、真实pool、root/app身份和root只读资金oracle；本任务只有在Task1源码完整后才能固定候选hash。

- [x] Root阅读完整新helper/测试和pure结果；独审检查真实取消因果、外部超时失败行为、任务收束、无期限UPDATE、real-PG/MockTransport证据边界，以及原失败字节保留。
- [x] 新Linux overlay固定只启用`async-expired-before-dispatch`和`async-inflight-deadline-cancel`，保留原wheel/SQL/role helper。root oracle独立读取完整合成资金、work binding/lease/deadline和budget，验证manual/pending、actual=null、未释放/未结算/无物理产物、同一job/attempt/permit身份与epoch/audit。不把app返回DTO当数据库事实。
- [x] Root完整核对新candidate closure、资源与默认值、剩余空间、guardian-beforeSTART、原1800s同boot、一次性create/start/logs、终态/fresh/hash/publication，再各新case顺序单次运行；未知结果查询原handle，不重跑旧失败。运行前资源profile和实际nonce在新准入记录固定。
- [x] 逐例完整安全归档／独审后才计新功能验收通过。旧200ms函数的历史FAIL和17/37账本不改写；发布验收新增明确映射，AV19、原其他媒体／T13/T14/B2/B3及所有广泛交付门保持原范围。

## Task 3: 收口与交付账同步

- [x] 更新本计划checkbox与验收报告，准确区分pure、real-PG、Linux/root/app及MockTransport。
- [x] 新增测试不改动产品前端或当前17868数据，不需要为纯测试重新打桌面包；若真实用例暴露产品缺陷，先保留具体RED，按最小产品修复另跑相关回归，之后才重新打包。
- [x] 已成功且完成归档/独审的临时测试容器按用户清理授权定向移除，数据卷／私有失败证据保留；不prune/force，不操作其他业务或远程服务。

## Task2 实际首轮后修订：维护扫描审计闭合（2026-11-02）

- [x] 保存C1首轮真实exit1、原source/stage/handle及固定安全诊断；helper已到preclose但Root未确认，C2未启动。不能记为功能通过。
- [x] 从固定wheel原use/finish/close/SQL确认原维护扫描成功会生成permit_completed，现有零新增audit断言与此矛盾。确切首个Root失败frame未保存，因此不宣称已穷尽实际失败原因。
- [x] 新codeonly-r3保留原消耗版本，新增scan_ready，只观察真实transient维护cap，不改产品/Task1/期限/业务guard；Root绑定实际auxcap的token/kind/subject/epoch/worker/generation，要求只完成该cap的精确审计，原jobcap/资金/存储预留保留。
- [x] meaningful RED/GREEN及独审后，Root固定新完整candidate/nonce/lifecycle，再各新case一次验证；原失败现场不重试或删除。

## 最终限定收口

R4新源码70项Root纯测试0.501秒、源审/物化审通过；两例真实最小角色/PG/MockTransport实际完整通过，37安全文件独审PASS，3成功容器和1镜像定向清理，209卷及旧失败现场保留。原R2 oracle遗漏和R3 consumer格式失败均保留；R4只修正格式与scope并增加实际producer→consumer回归，未复验旧handle。详见 docs/validation/2026-11-02-media-cancellation-functional.md 与 FUNCTIONAL-ACCEPTANCE-MAPPING.safe.json。当前计划的拆分功能验证完成，不代表剩余交付总目标或完整B2/T13/T14完成。
