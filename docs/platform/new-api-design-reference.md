# New API 设计参考与中央平台对应关系

日期：2026-10-20。用途：落实用户提出的“借鉴 New API 设计”，修订中央平台方案。本页的中央平台仍处于设计阶段；后续获授权实施的 New API 升级与插件接入见[现场验收记录](../validation/2026-10-20-new-api-upgrade-plugins.md)，不表示中央会员、共享额度平台已经实现。

实现依据是官方 `v0.13.2` 标签源码，与此前运维手册的版本基线一致；当前官方文档用于补充概念。当前文档的新功能不视为该部署版本已有能力。下面的“中央平台采用方式”是本项目的设计决定，不是 New API 已实现能力声明，也不是线上性能测试结果。

2026-10-20补充：用户指出当前官方插件市场后，已新增[插件能力与复用评估](/Users/jiandan/Code/GitCode/Easel/docs/platform/new-api-plugin-evaluation.md)。本页保留v0.13.2设计参考，不再据此预设所有供应商适配独立重写；新版任务插件及宿主复用作为优先评估项，以修订后的主设计为准。

## 核心对象与流程

| New API 的实际设计 | 源码或官方文档 | 中央平台采用方式 |
| --- | --- | --- |
| Channel 保存供应商类型、地址、状态、模型映射、优先级、权重和凭据相关配置 | [Channel](https://github.com/QuantumNous/new-api/blob/v0.13.2/model/channel.go#L22-L54) | 拆成 adapter 协议实现、channel 连接实例、credential_version；同一供应商可有多地区或多个实际账号的连接 |
| Ability 通过 group、model、channel_id 关联可用渠道 | [Ability](https://github.com/QuantumNous/new-api/blob/v0.13.2/model/ability.go#L16-L23) | 建 channel_model 能力关系；另增加协议、操作、工具类型、计量、限制和验证状态，group 只对应路由池 |
| 选择优先级，再在同级按权重分配；存在缓存与DB回退路径 | [缓存路由](https://github.com/QuantumNous/new-api/blob/v0.13.2/model/channel_cache.go#L96-L186)、[数据库路由](https://github.com/QuantumNous/new-api/blob/v0.13.2/model/ability.go#L61-L137) | 首期采用可解释的 priority/weight；缓存和DB使用相同算法，零权重含义显式定义，记录候选排除与选中原因 |
| Adaptor 拆分URL、鉴权、请求转换、发送和响应处理；TaskAdaptor另有异步任务与计量阶段 | [协议接口](https://github.com/QuantumNous/new-api/blob/v0.13.2/relay/channel/adapter.go#L15-L78) | 采用较小的文本、图片、语音、异步任务接口；直连和可选New API渠道使用相同平台注册机制 |
| 请求有重试循环、重选渠道、错误分类及使用渠道链 | [重试流程](https://github.com/QuantumNous/new-api/blob/v0.13.2/controller/relay.go#L180-L240)、[条件](https://github.com/QuantumNous/new-api/blob/v0.13.2/controller/relay.go#L318-L347) | 逻辑请求与实际attempt分开；已向客户端输出或供应商提交结果未知时不盲目切换，重试受总预算和可证明的副作用规则约束 |
| 按错误规则自动禁用，主动测试成功可恢复自动禁用；人工禁用不会被同一路径恢复 | [禁用与恢复](https://github.com/QuantumNous/new-api/blob/v0.13.2/service/channel.go#L18-L77)、[主动测试](https://github.com/QuantumNous/new-api/blob/v0.13.2/controller/channel-test.go#L909-L935) | 区分人工管理状态和健康状态；定义窗口、冷却、半开探测与恢复阈值，429有独立退避，探测有预算与审计 |
| Token 具有用户归属、有效期、模型/IP限制、额度和分组 | [Token](https://github.com/QuantumNous/new-api/blob/v0.13.2/model/token.go#L14-L30) | 分开产品会话、短期任务token、未来租户服务Key和上游凭据；消费限额是中央钱包的准入约束，不给每个Key复制钱包 |
| BillingSession 管理预扣、差额结算与退款，并支持不同资金来源 | [BillingSession](https://github.com/QuantumNous/new-api/blob/v0.13.2/service/billing_session.go#L23-L78) | 借鉴生命周期，使用PostgreSQL持久化reservation、receipt、不可变平衡分录和幂等对账；缺usage保持pending |
| 共享HTTP client、连接池、流空闲处理、有限缓冲和断开检测 | [HTTP client](https://github.com/QuantumNous/new-api/blob/v0.13.2/service/http_client.go#L18-L70)、[流式处理](https://github.com/QuantumNous/new-api/blob/v0.13.2/relay/helper/stream_scanner.go#L185-L292) | 借鉴连接复用和清理，单独限定在途并发、事件大小和总缓冲；不能将空闲池大小当在途并发上限 |
| 管理菜单区分console、personal与admin | [菜单结构](https://github.com/QuantumNous/new-api/blob/v0.13.2/web/src/hooks/common/useSidebar.js#L28-L56) | 建平台运营后台和租户控制台；入口、服务端权限及字段投影分开，组件可以复用 |
| 渠道管理、全局日志与本人日志在服务端使用不同鉴权 | [管理与日志路由](https://github.com/QuantumNous/new-api/blob/v0.13.2/router/api-router.go#L210-L301) | 平台operator权限独立于产品/租户会话；请求日志可关联账本，但日志写入不直接决定余额 |
| 模型元数据同步先展示缺失项和差异，再显式覆盖 | [模型同步流程](https://github.com/QuantumNous/new-api/blob/v0.13.2/web/src/hooks/models/useModelsData.jsx#L168-L243) | 采用草稿、差异预览、校验、发布和回滚的配置工作流；上游目录变化不自动开放付费能力 |
| 日志按请求ID等筛选，详情展示实际模型、转换及计费相关信息 | [日志查询](https://github.com/QuantumNous/new-api/blob/v0.13.2/web/src/hooks/usage-logs/useUsageLogsData.jsx#L729-L760)、[详情](https://github.com/QuantumNous/new-api/blob/v0.13.2/web/src/hooks/usage-logs/useUsageLogsData.jsx#L368-L720) | 用request/job/attempt ID串联产品、租户、渠道、价格、原始usage、扣费、pending和审计；默认不保存提示词与凭据 |

当前官方文档的[渠道字段](https://github.com/QuantumNous/new-api-docs-v1/blob/main/content/docs/en/guide/feature-guide/admin/channel.mdx)也明确区分优先级、权重和模型映射；源码基线仍以本表版本链接为准。

## 本项目需要分别建模的概念

**租户与路由池。** tenant 是成员、权限和资源所有权边界，product 是调用产品，billing_account 是共享付款主体。routing_pool 只负责流量和渠道策略，不授予会员资格，不允许客户端换group绕过Scope。

**登录会话与API令牌。** Logto身份、产品设备会话、短期任务token、租户服务Key、operator会话和上游凭据具有不同的audience、权限和生命周期。第一阶段实现设备/任务凭证与管理会话；长期自助API Key后置，不能让产品bearer访问全局管理接口。

**用户计费与采购成本。** 用户费率用rate_version，在逻辑request准入冻结；供应商采购规则用provider_cost_version，在供应商操作/attempt冻结并记录原始单位。用户只在中央钱包结算一次，采购pending不自动等于用户pending，平台隐藏重试损失不能直接累加给用户。运营调整使用带原因、唯一操作键和相抵分录的事件，不通过直接改余额或修改日志实现。

**请求日志与账本。** 请求日志用于排障和经营分析；财务状态由事务账本及可信receipt决定。HTTP 200不等于业务成功，SSE可能以错误终态结束；断流或未知提交不自动当成免费失败。

## 不照搬的版本细节

- v0.13.2缓存选路在全部权重为0时可均分，DB路径还使用`Weight + 10`。中央平台必须统一快照和回退算法，不能直接把两段实现各复制一份。
- 该版本的错误禁用与探测恢复不是本设计完整的熔断状态机。我们需要独立定义何时打开、半开、恢复，以及多实例并发探测的归属。
- 空闲连接池上限不限制活跃推理数量；扫描缓冲与SSE解析方式也不直接作为4核4GB默认值。
- 该版本[钱包退款实现](https://github.com/QuantumNous/new-api/blob/v0.13.2/service/funding_source.go#L47-L63)说明额度增量不是可任意重试的幂等操作；进程内状态不足以证明跨崩溃的财务幂等。中央账本使用持久化业务唯一键和事务。
- 该版本[Responses补估](https://github.com/QuantumNous/new-api/blob/v0.13.2/relay/channel/openai/relay_responses.go#L133-L147)不能替代所有工具/推理输出的可信最终用量，中央服务继续保留pending与对账。
- [管理用户额度](https://github.com/QuantumNous/new-api/blob/v0.13.2/controller/user.go#L920-L960)可以作为操作页面的参考，但不复制直接覆盖余额的业务语义。

这些边界用于说明本项目的实现取舍，不是对New API其他版本、所有部署或所有资金来源作统一结论。

## 第一阶段的运营闭环

首期同时交付必要管理能力：P0有operator权限基础与审计；P1能查询用户/租户/产品并撤销授权设备；P2能授予test会员、发布费率、查看预留和pending并按证据对账；P3能配置渠道、轮换凭据、发布模型映射和路由、运行有预算的探测并定位失败请求。

租户控制台首期展示当前租户和产品、有效测试会员、钱包可用/预留额度、设备与本人请求记录。跨产品经营数据需要独立账户中心权限，不能由任意产品会话读取全部明细。

平台运营页面采用列表、筛选、详情、受限操作与结果回执的工作流。请求ID应能定位模型、渠道、原始usage、预留/结算/待核算，以及相关管理变更。复杂批量操作、营销、正式收款和高级报表后置。

## 参考范围

New API提供值得借鉴的模型接入和运营结构，本轮参考其对象、流程和协议边界，未复制实现代码或界面资产。原仓库的[许可证](https://github.com/QuantumNous/new-api/blob/v0.13.2/LICENSE)与后续选定复用组件的来源记录应随实际代码复用单独处理；本次设计参考不等同于决定fork或直接移植整套系统。

修订结果见[中央平台供应商直连与性能设计](/Users/jiandan/Code/GitCode/Easel/docs/superpowers/specs/2026-10-20-central-platform-direct-provider-design.md)。
