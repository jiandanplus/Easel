# 排队过期与在途取消功能验证

2026-11-02，`codex/ui-redesign`。按用户明确选择，将原200ms混合验收拆成两条功能路径；原函数、失败记录及原37节点历史账本保留。

| 新用例 | 实际行为 | Root实际结果 |
|---|---|---|
| C1 async-expired-before-dispatch | 一次INSERT创建已过期work；完整原worker不派发HTTP；work进入manual；晚到heartbeat拒绝 | 通过，运行7.526秒 |
| C2 async-inflight-deadline-cancel | 一次INSERT固定20秒deadline；先进入唯一GET body，执行原heartbeat，再由原worker期限取消；观察真实CancelledError、finally、aclose，最终manual且晚到heartbeat拒绝 | 通过，运行27.211秒 |

运行时长包含PostgreSQL初始化与准备；不是GET时长或取消延迟指标。C2外部30秒是协作故障超时，不能冒充产品期限或硬进程生命周期上限。运行未延长任何已有deadline，也没有重跑原200ms请求。网络由HTTPX MockTransport提供合成挂起流，真实asyncio取消和真实SQL/worker已运行；不代表真实供应商连接／断网行为。

两例分别在新Linux/amd64隔离容器、真实PostgreSQL001–020迁移、真实最小运行角色／连接池下首次运行。Root独立检查实际app身份、同一job/attempt/permit、binding、不可变work/deadline、实际claim/heartbeat租约、完整资金保留／关联／账本、未释放8MiB/32文件预算、零交付和零物理产物。真实辅助maintenance capability在scan/claim/heartbeat保持active，之后只有其精确完成审计；原未知费用jobcap与预留继续保留，不结算、不退款、不重派模型。

最终源码manifest `f914cdb3cd605aff1b5d37395146c62e79c293381aa78eeb4bd123fd352da6f9`，64公开文件closure `2c24a6eb7f00a91603b742797a0150f27489abb111bea4740f4657663cafa9d2`。Root当前70项纯测试通过（0.501秒），producer dictionary AST→实际host consumer AST包含两例及schema/type/hash/once/wall/copy反例。源审与物化审通过，真实角色／执行／回执／fresh完整配置、原同boot期限、最终发布时钟样本及写后围栏均通过。

实际安全归档37文件，manifest `c6d11df0ad381709f2dec7462f7c7d172457a02678ea4322e623042bae26a33b`；独立实际安全材料审查已通过，未发现该限定范围的阻断性矛盾。内部私有SQL、app流标记及checkpoint不对外导出。Root执行已审查的完整断言，公开审计校验的是安全材料的身份／schema／hash／期限与配置一致性；不能称审计者重建了私有SQL。

第一轮发现并修正辅助维护cap完成审计的oracle遗漏；R3运行端exit0后发现host仍识别旧family-six回执格式，有限R4仅修正format及scope标签。两轮源、handle、失败/未通过完整验收记录及关联镜像均保留；未对旧handle再次accept，也未伪造兼容成功回执。原成功构建容器已在归档后定向清理；最终成功资源在独审后已定向清理：3个容器及1个专用镜像；本次清理新鲜库存34→31容器，209卷全部保留，其他容器ID/image/state一致。两轮旧未通过完整验收的运行容器及关联镜像保留。

本次没有修改产品／前端、原17868登录／历史／自定义接口，没有调用供应商、支付接口或真实点数，也没有新桌面打包／激活／签名／公证、commit/push/merge。两条新功能验收与原严格200ms历史结果分列。AV19、其余媒体完整覆盖、B2/B3/T13/T14、生产备份恢复／DR及正式发行门仍需单独完成。

[最终源审](../../outputs/_run/b2-causal-linux-preparation-20261102/INDEPENDENT-REVIEW-r4.safe.md)；[实际安全归档](../../outputs/_run/b2-causal-linux-preparation-20261102/actual-r4-safe/manifest.safe.json)；[开发计划](../superpowers/plans/2026-11-02-media-cancellation-paths.md)。

发布映射只调整此family子集：保留原37历史账17/37；按已批准一拆二功能合同，修订family门为38项，17原通过＋2新功能通过＝19通过，原AV19仍未关闭。该计数不是项目总进度。见[映射](../../outputs/_run/b2-causal-linux-preparation-20261102/FUNCTIONAL-ACCEPTANCE-MAPPING.safe.json)、[实际独审](../../outputs/_run/b2-causal-linux-preparation-20261102/ACTUAL-REVIEW-r4.safe.md)和[定向清理](../../outputs/_run/b2-causal-linux-preparation-20261102/cleanup-r4-result.safe.json)。
