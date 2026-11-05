# New API 插件能力与中央平台复用评估

日期：2026-10-20。本页保留插件选型时的资料与源码评估。后续用户已授权并完成现有 New API 升级、全部 13 个官方插件和 5 个 Easel 私有插件注册；线上版本、哈希、运行时及 126 个无供应商调用的在线 hook 样本见[升级与插件验收记录](../validation/2026-10-20-new-api-upgrade-plugins.md)。完整中央平台与独立 runner 复用 PoC 仍未实施，真实供应商调用尚未验收。

**新版New API已有可复用的供应商任务插件，中央平台不应预设所有适配器都重新开发。** 优先验证官方插件及其宿主的复用方式，保留中央身份、产品/租户权限和共享账本；缺口再用独立适配器补充。此前v0.13.2核心接口核对仍是该版本的历史基线，不能代表当前市场全部能力。

## 已确认的插件机制

[官方市场](https://www.newapi.ai/zh/plugins)目前列有Alibaba、Doubao、Sora、Hailuo、Google/Vertex Veo、Kling、Jimeng、Vidu、xAI、SunoAPI、Incho、TypeSafe等。发布物是单文件同步JavaScript ECMAScript Module，通常为`plugin.js`，不是Lua。仓库的`v1.d.ts`是类型合同，不代表宿主直接加载TypeScript源码。[官方仓库](https://github.com/QuantumNous/new-api-plugins)、[开发指南](https://docs.newapi.ai/zh/docs/plugins/development)

当前市场类别是task。task不只表示异步视频：插件可以处理即时完成、图片、音乐和其他任务，并声明Images、Video、Responses等宿主协议。这不等于存在可以替代全部LLM协议和工具流的通用relay插件体系。

| 插件负责 | 宿主负责 |
| --- | --- |
| 模型/协议/能力声明、请求描述、提交结果解析 | 身份与调用权限、渠道选择、实际HTTP、文件引用与访问控制 |
| 查询描述、状态与结果解析、持久化state投影 | 任务持久化、worker、轮询、重试与恢复 |
| 产物列表和内容请求描述 | 下载代理、Range、凭据与无凭据CDN访问边界 |
| 秒数/张数/clips/token等usage facts | 价格、预留、结算和退款策略 |

插件不提供浏览器或Node.js的全局fetch、文件系统或环境变量访问，也不是任意Node.js脚本。支持相应能力的宿主可在指定driver hooks提供受限`utils.fetch`，只允许受主机白名单、次数、时间和响应大小限制的GET/HEAD，不用于启动收费任务；兼容runner也要处理这项宿主工具合同。复用源码不能仅安装一个JavaScript引擎就认为任务和账务已经完成。[API参考](https://docs.newapi.ai/zh/docs/plugins/api-reference)、[受控读取合同](https://github.com/QuantumNous/new-api-plugins/blob/main/docs/plugin-api/v1.md#reading-upstream-data-with-utilsfetch)

## 与 Easel 的能力对应

下面是代码存在与合同核对，不是渠道配置或真实调用通过记录。具体插件版本是本次观察的版本，实施前须锁定源码hash和宿主版本。

| 能力 | 当前源码证据 | Easel仍需处理的边界 |
| --- | --- | --- |
| 百炼图片/视频 | [Alibaba 1.4.2](https://github.com/QuantumNous/new-api-plugins/blob/main/plugins/tasks/alibaba/1.4.2/plugin.js)含同步/异步图片、视频、Images/Video包装与`/ali/api/v1/...`原生路由 | 现有DashScope地址不能只替换域名；编辑、蒙版按具体模型开放，mask仅特定模型支持 |
| 豆包图片/视频 | [Doubao 1.3.0](https://github.com/QuantumNous/new-api-plugins/blob/main/plugins/tasks/doubao/1.3.0/plugin.js)含Seedream、Seedance、`/doubao/api/v3/...`路由、产物和usage | 参考图编辑不等于任意蒙版，当前实现拒绝mask且n仅接受1；客户端参数仍需适配 |
| Sora视频 | [Sora 1.1.0](https://github.com/QuantumNous/new-api-plugins/blob/main/plugins/tasks/sora/1.1.0/plugin.js)有submit/poll/remix/artifact及带鉴权的content请求 | Easel需要消费content/artifact，不能要求完成JSON总含直链 |
| 海螺与MiniMax-H3视频 | [Hailuo 1.3.0](https://github.com/QuantumNous/new-api-plugins/blob/main/plugins/tasks/hailuo/1.3.0/plugin.js)有视频任务、文件签名URL和用量 | 视频文件检索不等于MiniMax声音样本上传、克隆登记和TTS |
| 音乐 | [SunoAPI 1.1.0](https://github.com/QuantumNous/new-api-plugins/blob/main/plugins/tasks/sunoapi/1.1.0/plugin.js)有音乐/歌词、批量轮询、产物与clips计量；[Incho 1.0.2](https://github.com/QuantumNous/new-api-plugins/blob/main/plugins/tasks/incho/1.0.2/plugin.js)有歌曲任务和产物 | SunoAPI源码标注Unofficial并连接Suno-API项目，不是Suno官方API；不自动兼容Easel原有`/generate`与`/feed`客户端 |
| 图片变体 | 已读Alibaba/Doubao插件未见variations，宿主Image协议声明generation/edits | 不能据此将`/images/variations`标为已覆盖 |
| TTS/ASR/声音克隆 | 本次市场未见专用声音登记/克隆插件；New API核心另有标准音频能力 | 不推导DashScope enrollment、MiniMax upload/clone、Fish reference、Gemini Interactions已兼容 |
| Codex文本工具 | 某些任务插件声明Responses的stream/sync/background，用于包装媒体任务 | 不能推导namespace/function/custom工具往返兼容；Doubao当前插件会过滤function等工具。原生文本网关仍单独验收 |

官方[宿主协议及SSE合同](https://docs.newapi.ai/zh/docs/plugins/api-reference)明确：任务插件的提交SSE不会直接透传上游事件，而是由插件解释、宿主累积和呈现。`supports: stream`、模型名称或市场条目不能替代Easel工具及最终usage的真实验收。

## 版本与宿主边界

- 手册基线v0.13.2仍使用Go内置TaskAdaptor，没有当前jsplugin管理与运行链，不能直接安装当前市场插件。[旧工厂](https://github.com/QuantumNous/new-api/blob/v0.13.2/relay/relay_adaptor.go#L134-L165)
- [v1.0.0-rc.27发布说明](https://github.com/QuantumNous/new-api/releases/tag/v1.0.0-rc.27)确认引入沙箱JavaScript任务插件体系，并在该次发布标为实验性架构变化；这是体系引入证据，不是当前所有插件最低版本保证，也不是本项目选定的部署版本。
- 官方文档仍将Task Plugin API v1标为尚未正式发布的演进合同。相同`apiVersion: 1`也会增加字段；市场`minApiVersion: 1`不是New API宿主语义版本。[版本说明](https://docs.newapi.ai/zh/docs/plugins)
- 安装兼容性按宿主版本/commit、plugin key/version/hash、manifest字段、requiredCapabilities、协议与模式共同判断。不同插件的最早兼容release本轮未全部确认，不能统一写成rc.27。
- 选型时尚未读取原服务器；后续现场已确认从 v0.13.2 升级至固定摘要的 v1.0.0-rc.45，并读回 18 个注册插件。具体部署与限制以上述升级验收记录为准。

## 三种复用途径

| 路径 | 优点 | 必须付出的集成工作 |
| --- | --- | --- |
| 完整New API宿主作为模型/任务后端 | 可直接复用渠道、插件管理、任务、轮询和产物处理，作为兼容基准 | 中央授权、任务归属和可信计量桥；隔离内部quota/退款与用户钱包，测量新增进程和调用成本 |
| 自有Go数据面或私有runner实现兼容宿主，复用插件JS | 插件可直接构造供应商请求，不要求经过完整New API HTTP服务 | 对接宿主context、签名工具、文件引用、路由/呈现、state、轮询、产物、版本和账本合同；先证明成本可接受 |
| 自定义Python或其他语言适配器 | 适合官方插件缺口、特有业务和复用不划算的能力 | 自行维护供应商字段、认证、查询、下载、计量和变更；不再作为所有供应商的默认首选 |

[moejs](https://github.com/Calcium-Ion/moejs)是独立的纯Go JS引擎，当前README要求Go≥1.25并标注alpha；引擎可嵌入不等于完整New API插件宿主可即插即用。New API的[engine](https://github.com/QuantumNous/new-api/blob/main/pkg/jsplugin/engine.go)、[registry](https://github.com/QuantumNous/new-api/blob/main/pkg/jsplugin/registry.go)及[任务适配层](https://github.com/QuantumNous/new-api/blob/main/relay/channel/task/jsplugin/adaptor.go)仍连接其日志、渠道、relay、任务和计费设施，尚未在本轮证据中证明是独立稳定的第三方宿主SDK。

Python控制面可以继续承担身份和共享账本；数据面是否采用Go插件宿主、完整New API后端或局部自定义适配，应通过D0选择，不能先假定Python原样执行官方插件。引擎的微秒级hook基准也不能换算成4核4GB上整个平台的吞吐。

## 中央账本必须保留的语义

插件只报告用量事实。产品/租户授权、用户费率、钱包准入、reservation、幂等分录与pending由中央服务决定。第三方插件提供的usage需要绑定具体attempt、插件版本、宿主身份和响应证据，不能由产品客户端提交后直接入账。

当前官方[轮询合同](https://github.com/QuantumNous/new-api-plugins/blob/main/docs/plugin-api/v1.md#polling-contract)包含404/410失败退款和轮询失败清理。此类任务状态不能作为供应商未消费或中央用户hold可安全释放的充分证据。完整宿主作为后端时，其内部quota/退款只作该后端状态，不同步覆盖中央钱包；若宿主无法提供足够状态与receipt而必须深度改造，应在D0记录为复用成本或验收失败。

发布对象增加plugin key/version/hash、宿主版本、required capabilities、usage schema和渠道绑定。旧任务的producer版本、state与data schema必须保留可恢复性；官方宿主后台轮询可能使用更新后的generation，不能假设自动固定旧插件。更新需要兼容验证，必要时排空在途任务再切换；回滚配置不删除历史计量和账务。

## D0 数据面复用验证门槛

D0是设计阶段新增的后续PoC任务，尚未执行；在P3数据面实现定稿前完成，不需要先升级现有服务器。

1. 锁定一个支持所需字段的候选宿主commit及插件hash，选Alibaba同步图片和Sora异步视频为代表。保留一个原生Responses工具流作为独立文本对照，不用媒体Responses包装代替。
2. 用假凭证、假上游、临时数据库和专用端口验证插件加载、manifest/capability拒绝、请求描述、submit/query、state、content/artifact及usage facts。只证明模拟协议，不声称真实供应商可用。
3. 验证任务归属、重复/乱序、未知提交、客户端断开、轮询404/5xx、无最终usage、版本切换与worker重启；检查中央用户账本不会被后端自动退款或重复事件改变。
4. 比较完整宿主与兼容runner所需的桥接改动、组件版本/许可、资源预算和维护成本。对照供应商直连路径测量增加的网络及处理耗时，不用“多一跳”或引擎微基准替代实测。
5. 输出版本矩阵、完整能力/失败项、中央结算桥是否成立、4核4GB候选资源预算和推荐路径。能复用的插件优先复用；不兼容或成本高的部分明确回退到自定义adapter。

D0不会因市场条目存在自动通过。开始PoC代码、插件安装或服务升级仍属于设计确认之后的开发与部署范围。
