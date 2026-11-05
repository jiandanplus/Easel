# M2 异步视频与音乐：独立只读审查

结论：两项独立发现的 P1 问题已在新冻结候选中闭合，**限定 M2 范围内没有剩余已知阻塞**。独立最终回归为 **404 passed、1 skipped、0 failed，88.51秒，exit0**，覆盖全部媒体模块、runtime最小权限及原两项对抗用例，共27模块。14份候选输入与额外依赖组成的26份closure，在运行前、运行后和报告生成时 SHA 全等。

新源码候选 aggregate 为 `abb2e0efe34956c834d4a1aa744934114a4c9d6939a73e033a716c2b15411da8`，来自 `/private/tmp/easel-m2-candidate-20261027/p1-p2-fixed-manifest.json`。这是**源码文件清单摘要**，不是生产wheel摘要。原 `9798b204883e7a2f1501ba2f95509e8ce4d394635b7bf5bf56a396df01ae02ae` 与其独立失败反例留存，不把旧快照通过结果冒充修后终态。

本review没有改实现或候选测试，没有SSH、付费请求、真实供应商POST、生产SQL、运行包切换或真实用户auth读取/修改。按root协调本轮只审M2；UIcopy由root另行验收，本报告不冒称自己的UI验证。

## 发现与修复复核

**P1：慢GET可能永久占住恢复循环。** 原 worker.run_once 在gather等待整个execute，只有HTTPX inactivity timeout；持续返回小块可长期不触发read timeout，导致下一轮expire、financial recovery与GC无法运行。原heartbeat也没有按record deadline封顶。独立纯反例采用30ms claim deadline、20ms/byte，120ms后任务仍卡在gather。最初fixture遗漏输入资源配置，先修正自身max_assets=0，随后得到针对上述行为的明确红测；没有改产品。

新 `media/worker.py:80` 用全claim绝对墙钟预算 `min(worker_wall_seconds, recorddeadline剩余)` 覆盖GET、解码与登记流程。`media/async_store.py:68` 的work/job heartbeat均封顶deadline；到期仅相同token的专用defer可收为manual，持有的费用和存储预留不释放。原 `core.py:211` 的terminal/settlement deadline围栏一直存在，本finding是恢复活性与资源占位，**未证明或声称旧代码能越期扣点**。

**P1：正常已知运行任务被错误移出执行并发。** 原submit协程返回后不再续media_jobs的60秒lease；worker只续自己的work lease。独立真实隔离PG中，套餐concurrency=1，模拟原submit lease过期后成功查询到in_progress，随后generic financial recovery却把原任务转为pending/unknown：work仍query，active_requests=0、pending=1，第二admit实际成功。原独立反例0.64秒明确失败。

新 `async_store.py:68,98` 在成功queued/running观察和heartbeat时，仅延长仍dispatched的job，遵守wallet→invocation→job/reservation→work锁序并受deadline上限。`core.py:441` 的generic recovery保留有效query、未发生GET失败的已知原任务；无ID、真实GET失败、过期/manual和原有pending政策保持保守，不将既有pending普遍抬回active。两个原独立文件未经修改，在最终404项中均通过。

## 端到端边界审查

- 三个显式适配器分别采用New API标准videos、锁定SunoAPI、现有DashScope音乐插件合同，不按模型名猜接口，不跨协议fallback。生成POST只在runtime提交路径；先固定job/最大输出预算/出站attempt/dispatch，HTTP关联持久化后，一旦解析到可信provider job ID便先持久化，再解析状态和入队。ID后状态异常或崩溃可补建GET work，没有ID则保持未知，不能重发生成。
- worker只有原ID GET、固定同源content GET或已持久来源GET。恢复、下载、重启、同键重放不增加生成POST。绑定重算检查当前配置与冻结job一致，scope/attempt/binding/provider scope与provider ID通过原关联和018外键链固定。关闭worker、缺工具、版本不符、未支持费率或未配置CDN策略均不开放相应readiness。
- queued/running只保有界work摘要；首次可信终态的manifest、usage、response hash不可变。后续签名URL更新必须来自原任务GET，保持source_id/kind/MIME、终态与计量一致。最终terminal evidence必须与首次observation的状态、ID、原hash及provider usage相符，不能用后来响应拼出账务证据。
- source拥有稳定storage_key与artifact_key。先原子占存储预算再写文件；完成文件或完整.part必须按原SHA/长度/归属验证才恢复。残缺.part保持失败和占额，不静默删除或覆盖。重复source stage/attach不再占预算、不重复建artifact；旧租约无法提交新租约的产物、终态或结算。
- CDN下载不转发供应商Authorization，严格origin与目录前缀、规范路径、禁止重定向。videos固定content接口同源带鉴权。内容长度与累计字节有界；原字节经受限ffprobe和ffmpeg全解码，进程清空凭据环境、禁网络/外部dref、限制CPU/地址空间/输出/文件与墙钟，受控AV属性才进入owned artifact。
- video_ms/audio_ms来自已解码并登记的实际时长；Suno audio_clip只统计实际完整交付的owned音轨，不能使用预估clips或nominal seconds。DashScope provider_usage仅使用显式usage.duration的精确毫秒转换。没有可信量、超出预留、下载或测量失败都不能伪造零用量或自动退款。
- 018的work绑定不可改、source身份与已登记metadata不可改、observation不可变；manifest/attempt/关联/产物/terminal/receipt跨表延迟约束和最小runtime DML权限覆盖正常结算与篡改拒绝。claim只短锁work，网络/解码期间无DB锁；写入与结算遵守既有单钱包锁序。失败或取消状态本身不证明供应商零费用，仍manual/pending并保留hold和pin。
- 已接受任务可在用户退出后按冻结scope交付与结算；新提交、查询和下载仍走当前session授权与归属。未绕过现有撤销、跨钱包和跨产品边界。产物可用、执行状态和财务状态独立表达。

## 独立实际验证

私有runner：`/private/tmp/easel-m2-independent-review-20261027/run_fixed_review.py`。先等待作者完整回归退出，再独占原50958隔离PG执行随机schema测试，不打印DSN；未停容器或删除数据卷。

实际命令集合：全部 `services/platform/tests/test_media_*.py`、`test_runtime_permissions.py`，加私有 `test_worker_deadline_independent.py` 与 `test_job_lease_independent.py`，使用 `--noconftest -q --tb=short -rs -p no:cacheprovider`、显式原字节fixture别名和本机私有DSN文件。结果 **404/1skip，88.51秒**。唯一skip为 `test_media_restore_drill.py:247`，要求显式Docker恢复环境；它不是跳过M2协议、PG、lease、AV、runtime role或两项修复反例。

此前单独执行的新deadline＋18协议＋27实际AV为46passed/2.34秒，已包含在最终404覆盖中，不相加。fixture使用本机实际ffmpeg/ffprobe 8.0.1产生并完整解码短视频/音频，协议网络为MockTransport，无真实媒体供应商调用。

作者另行完成完整中央回归 **1710 passed、7 skipped、0 failed，350.69秒**；14份aggregate abb2及212份全输入 `223a4c82831216673c4096066223ae64d2911e2e0a4dd3b1de7469cb65a0e5f6` pre/post匹配，回执 `/private/tmp/easel-m2-candidate-20261027/p1-p2-fixed-receipt.json`。这项是作者本轮证据，不冒称我执行，也不与404加总。修前9798的1702/7仅为历史，不用于修后通过结论。

## 候选14份清单

| 文件 | SHA256 |
|---|---|
| scripts/validation/verify_central_live_text.py | `08ddc4647242b64bd7a59875f28e7b67e9482872913a355be692fb500c7ab2e3` |
| src/platform_service/app.py | `3b8dde45af17583ef67b8a167d53dfdf75ad6761056225add33ddfc0e5b36991` |
| src/platform_service/media/async_store.py | `a4088e3592bd54bc57b2be1c4765304a258fc481f46604c61a543593d8f5c2ec` |
| src/platform_service/media/core.py | `466619895003be59c9d419700f7520ad132f973c504d3b2515cef13d71965bb3` |
| src/platform_service/media/worker.py | `6d87bb454ed70a1270127206074e49598bd363506afbdd01f07d7f90848b9952` |
| src/platform_service/media_config.py | `aa527d402030da4d6312731aaba3c5ef8a3c8b56731752b0951c0569f88fe3b1` |
| src/platform_service/media_runtime.py | `99cd0b5f9bdbd35f0100098f14444cb0e6bdc894d5c08abf6ec9bea652615d62` |
| src/platform_service/migrations/018_media_async_work.sql | `6c7f2ce777064d8a747c6de4fac9b33b0e77792d3a82bbc1a5ab44bf4b1b8496` |
| src/platform_service/permissions.py | `c17a179d8c20321a8d41b749fec78dde6efd519c92448a3adb460c1017c4517c` |
| tests/test_live_validation_runner.py | `05151a78a0291332445d96cf652345c35e7d166e5f9fe606da7a769f0a4a105d` |
| tests/test_media_async_runtime.py | `2d09562df819ce8db4ebac87f776c24e1fa020d53519af805263782f531ae012` |
| tests/test_media_async_store.py | `419fc5f6db604148fe97f6aa250a7079e28d74cebb64ef8c7442691e95239c5d` |
| tests/test_media_async_worker.py | `2da65da8e156e3bc98263b2d7e1da939514e6bf082d7df79bac46d29140bbdf1` |
| tests/test_media_storage.py | `94753490d32dd3a336edabc47b20b5b7e9345608dc285f05f357a1dfe45ccc80` |

## 后续范围与证据限制

本机合成验收不代表真实视频/音乐供应商、正式费率或Linux生产AV工具已经开通；没有执行M2生产发布、安装包激活或新mixed-version DR。既有f50、运行包与用户会话不在本轮写入范围。

下载策略目前只接受明确目录前缀，不能显式允许根目录平铺 `/track-id.mp3`。这是一项已向root说明的配置范围限制；没有真实渠道证据便不据此声称实际供应商不兼容。上线前需要核对具体原任务GET、CDN origin和路径，再确定显式策略，不能把合成 `/audio/` fixture当全部真实平台已接通。

供应商失败/取消、未知POST、过期窗口、不可验证partial以及费率超预留仍可能进入人工核算；这是保留未知费用的边界，不应为完成DR或继续测试而删记录、释放hold或伪造付款/用量。

最终安全摘要SHA256：`b0cfecb0823baed4a5e2b3ca909cae39d215b8099d9d81fd5bfb3c6b915f1197`。
