# AV 受限失败诊断设计

## 目的与授权范围

用户已要求按上一回复继续完成剩余开发；上一回复明确下一步为补充受限阶段和状态诊断，再继续精确日志容量验证。本设计落实该已确认方向，只做当前分支的源码／纯测试，不增加原生探测、网络、真实数据或资源操作权限。旧 exact-log 案例只有 create.intent，无法恢复原 CREATE 是否发送或响应状态；新诊断不能补写旧事实。

## 方案比较与选择

采用 **HTTP 内部状态观察 + API 不可变失败事实 + 容量案例私有失败记录**。HTTP 在完整、受限且语法有效的状态行之后只报告一个数字；API 内部保存本次操作和失败阶段；容量控制器尽力保存这些事实。原来的异常仍为固定安全文本，接受规则不变。

另一方案是跨所有封装传播携带上下文的异常，容易在规范化异常路径中丢失数据或误带依赖内容；不采用。读取 daemon 响应正文／原始日志可提供更多信息，但会增加数据接触面，也无法可靠重建旧调用；不采用。

## 分层接口

### HTTP

`read_response` 增加可选关键字 `on_status=None`，旧调用保持兼容。非 None 必须可调用。Receiver 在原有状态行长度上限内取得完整 CRLF 行，匹配现有语法，并确认代码为严格整数 100–599 后，在原 guard 前后围栏中调用一次观察器。只传该整数；不传 reason、header 或 body。随后仍要求等于 expected_status；不匹配即按原路径拒绝，不读取后续头部／正文。无效／不完整行没有状态事件。观察器异常仍转成固定 HTTPError，不能泄漏其文字。新增观察不绕过 EOF、字节上限或截止时间。

### API

观察器由 `_OwnedCall` 内部构造，调用者不能通过 APIClient 的构造参数传入；不新增路由或 transport 参数。每个 OwnedCall 的状态从 None 开始，只接纳一次严格有效数字。APIClient 增加只读 `last_failure`，为 None 或 frozen `FailureDiagnostic`。字段固定为 operation（现有操作名或 None）、phase（request / transport / decode / publish）、http_status（None 或 100–599 严格整数）、injected_for_tests（严格布尔）。这些是观察事实，不能叫 response_accepted、sent 或 succeeded。

`call` 的首次失败在原有取消／poison 处理范围内尽力保存快照；之后被拒绝的调用不得覆盖首次失败。记诊断本身失败不得跳过原取消或泄漏异常，仍抛固定 APIError。正常 Result 和准入条件不变，普通 cancel 不清除首次失败。无效 operation 不得通过属性访问或字符串转换导出内容。状态只来自内部观察事件，绝不从依赖抛出的异常中提取。已有 injected 来源标记不可被清除。

### 容量控制器与私有记录

新增仅供失败观察的 `failure.json`，加入原有 Records 的精确允许名称集合。控制器用固定 phase 枚举记录关键位置：准备、INFO、IMAGE、借用检查、CREATE、CREATED 校验、START、轮询、输出校验、借用复核、最终复核、完成发布。阶段在调用／校验前设置，不能表示远端已完成。记录含既有 case 绑定、已知 CID 或 None、phase、start_attempted、可用的 API 失败事实和整体 injected 标记；无原始响应、异常、头部、路径或命令内容。

成功案例不写 failure.json。失败时先执行现有取消／停止逻辑，再在原 cleanup_end 剩余预算和原目录身份仍有效时尽力写一次。不得续期、重新打开替换目录、覆盖旧记录，或因为诊断失败而跳过停止。无时间或记录失败时允许缺失诊断；缺失只能表示未知。该文件不能授予 CREATE、START、KILL、重试或接受回执的权限。

已有 failure.json 的目录不能当作新案例启动；成功路径的围栏也须拒绝新出现的失败标记。将来真正运行前，Root 的新冻结 loader 会对应检查该标记；本轮不修改或运行历史 loader。

## 文件与认证

八个可修改文件是 pack_http.py、pack_api.py、pack_executor.py、capacity_probe.py 及其四份对应测试。其余十个 AV 文件按独立清单冻结。executor 生产修改限于增加精确记录名及更新最终 API_SHA；PROFILE_SHA 保持现有值。HTTP/API 接口上下游和旧正常调用须有覆盖。旧私有 stage／冻结／failed case 不升级、不执行。

## 验证与交付门

新增纯合成 RED/GREEN：有效错状态只报数字且仍拒绝；无效／超长／截断行无事件；callback 只调用一次，抛出、超时或 guard 失败不能成功；依赖伪造异常不产生状态。API transport／decode／publish／request 失败分别保留正确阶段，首次事实不可覆盖，正常结果不变，injected 不被清除。容量错状态失败在私有文件中留下对应事实，但没有回执或重试；停止先于写诊断，诊断写失败不改变停止行为，不新增预算，目录替换／旧文件拒绝覆盖。

覆盖 HTTP、API、profile、executor、capacity 五份测试，保留 RED/GREEN、完整八文件 diff、baseline／frozen 副本、受保护哈希与报告。Root 新鲜运行并独立审查后，仅关闭本源码诊断项。下一次真实案例仍须单独冻结并确认其资源授权；不以此设计或测试批准重放原不确定调用。本轮不调用 Docker 或模型，不更改前端／工作台／钱包／生产服务，不打包、不 commit/push/merge。

自审：无待定占位；观察状态与响应准入、阶段与远端执行、诊断与权限分别定义；截止时间／停止优先级／写失败处置一致；范围仅覆盖新调用的诊断。
