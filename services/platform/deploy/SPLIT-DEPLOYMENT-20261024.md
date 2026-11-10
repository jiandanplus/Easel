# 2026-10-24 三机分工运行手册

本手册更新至2026年10月26日，记录已部署状态和后续操作边界。对应[迁移执行计划](../../../docs/superpowers/plans/2026-10-24-zhiyin-full-service-migration.md)、[10月25日执行稳定性阶段验收](../../../docs/validation/2026-10-25-execution-stability.md)及[10月26日待核算工作台R1报告](../../../docs/validation/2026-10-26-reconciliation-workbench.md)。历史 `CUTOVER_20261024.md` 和收据保留原时点内容，不能以其旧主机／公网Console描述覆盖本文。所有远端变更由root执行；以下命令说明维护位置与核验方法，不代表编写本文时再次执行。

## 1. 当前拓扑与入口

| 主机／对象 | 实际职责与边界 |
| --- | --- |
| `154.217.245.179` | 唯一新中央业务／身份权威，API、私有Console、Logto及两个PG16 |
| `115.190.211.112` | 原New API／PG17／Redis／插件继续供给；旧中央进程／PG停止且restart=no |
| `139.196.51.51` | 现有网站保持原状 |
| Mac原Logto | 应用停止且restart=no，原真实身份数据库 `ALLOW_CONNECTIONS=false`；保留数据／容器／归档 |
| `https://accounts.drawbookai.cn` | 公网注册／登录／订阅API，Nginx HTTPS代理到154 `127.0.0.1:19874` |
| `https://login.drawbookai.cn` | 公网认证入口；精确issuer为 `https://login.drawbookai.cn/oidc` |
| `console.drawbookai.cn` | 公网404，不反代管理后台 |
| `http://127.0.0.1:18885` | 维护者本机SSH私有Console入口，转发到154 `127.0.0.1:19875` |
| `http://127.0.0.1:18886` | 维护者本机SSH Logto管理入口，转发到154内部 `172.29.24.20:3002` |
| 本机18880 | 保留原1Panel专用SSH隧道；沿用已核验目标端口，不改成公网入口 |

公网只启用accounts／login HTTPS业务入口。数据库、应用原始端口和管理后台不直接暴露公网。`api.drawbookai.cn` 继续属于火山New API，不纳入本次站点迁移。

## 2. 发布、服务与持久状态

API／Console当前release为 `/srv/easel/releases/20261026-reconciliation-r1-v1`。此前的 `/srv/easel/releases/20261025-pending-concurrency-v1`、`/srv/easel/releases/20261025-execution-stability-v1` 和10月24日 `/srv/easel/releases/20261024-split-v2` 保留为各自时点的发布记录。当前代码分支为 `codex/ui-redesign`，HEAD `e972af0`，未合并到 `main`。迁移操作及敏感配置留在154 `/root/easel-migration-20261024` 和服务各自受控配置目录。不输出私有env、DSN、模型配置、dump、密钥或容器完整环境。

| 对象 | 当前名称／地址 |
| --- | --- |
| API systemd | `easel-central-production.service`；loopback19874 |
| Console systemd | `easel-console-production.service`；loopback19875 |
| New API上游隧道 | `easel-gateway-tunnel.service` |
| HTTPS代理 | `nginx.service` |
| 中央PG16 | `easel-central-pg-20261024`；内部 `172.29.24.10` |
| 身份PG16 | `easel-identity-pg-v2-20261024`；内部 `172.29.24.12` |
| Logto1.45.0 | `easel-logto-v2-20261024`；内部 `172.29.24.20` |

旧失败目标的两个容器停止保留，不是当前服务；不按模糊名称批量start／stop／删除。维护前重新确认full ID、固定镜像、卷、网络及实际地址。当前内部地址不能作为未来重建后不变的推定。

API／Console使用各自低权系统用户、DSN及OIDC secret；owner只用于一次性维护，不进入常驻进程。支付保持 `disabled`，不为部署发体验点数。Console provider registry只含公开绑定元数据，不放上游业务令牌。

10月24日受控新组件restart已通过：当时三个当前容器的ID、镜像、持久卷及内部IP保持不变，上述四个systemd unit均active；未执行整机reboot。当轮中央51表全行before／after一致，SHA为 `6f4af4e1f9be914093f048d6788a194f04a9d579289c92d6edb5b7c7f8e36eb8`。这是该次组件restart的数据保持证明，不是10月25日财务比较或新备份恢复演练。

10月24日资源实测：总内存3915MiB、available2907MiB；磁盘48.28GiB、free40.93GiB。限制为两个PG各512MiB、Logto768MiB、API512MiB、Console256MiB。这是单次运行基线，不是负载测试、长期容量承诺或整机恢复证明。

## 3. 私有管理访问

使用已验证host key的目标别名 `easel-zhiyin`。在维护者电脑新终端建立所需隧道；端口占用时先确认现有进程身份，不误停其他会话：

```bash
rtk proxy ssh -NT -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=yes -L 127.0.0.1:18885:127.0.0.1:19875 -L 127.0.0.1:18886:172.29.24.20:3002 easel-zhiyin
```

浏览器准确打开 `http://127.0.0.1:18885`，不能替换成localhost、目标IP或19875：Console检查精确Host／Origin。最终配置为 `CENTRAL_CONSOLE_PUBLIC_URL=http://127.0.0.1:18885`、`CENTRAL_CONSOLE_ALLOW_LOOPBACK_HTTP=1`，issuer仍为公网HTTPS login。Logto登记回调 `http://127.0.0.1:18885/oidc/console/callback`，原应用ID／secret及旧回调保留；产品回调为 `https://accounts.drawbookai.cn/oidc/easel/callback`。

Console保留HttpOnly／SameSite=Lax cookie、精确Origin／CSRF、`prompt=login`／`max_age=0`及签名auth_time检查。loopback HTTP只用于SSH私有入口，不放宽公网API／Logto TLS。多个127.0.0.1后台端口的同名cookie可能互相覆盖，管理员验收宜用独立浏览器profile。普通产品登录不会获得后台权限。

1Panel继续用原18880隧道。不要为方便将Console、Logto管理端或1Panel改为公网反代。

## 4. 火山New API供给通道

154的 `easel-gateway-tunnel` 使用专用来源IP／key／host-key约束，将本机 `127.0.0.1:19443` 转发至火山 `127.0.0.1:443`。154仅把 `api.drawbookai.cn` 在本机解析到127.0.0.1，运行endpoint为 `https://api.drawbookai.cn:19443/v1/responses`，保留原域名SNI及证书验证。源New API、供应商Key／渠道、PG17／Redis和其他使用者配置不变。

通道允许面是火山loopback443全部virtual hosts；SSH PermitOpen不能限制HTTP Host或URL路径，不能声称只允许模型接口。专用账户不提供shell／TTY／agent forwarding，不准远程转发或任意目的端口。

在维护者本机执行下列命令，经SSH在154做无消费通道探测；RTK只在本机使用，不假定154安装RTK：

```bash
rtk proxy ssh -o StrictHostKeyChecking=yes easel-zhiyin 'curl -q --noproxy "*" --resolve api.drawbookai.cn:19443:127.0.0.1 --silent --show-error --max-time 10 --output /dev/null --write-out "%{http_code}|%{remote_ip}|%{remote_port}|%{ssl_verify_result}" https://api.drawbookai.cn:19443/api/status'
```

预期 `200|127.0.0.1|19443|0`。此检查不调用模型；不用 `-k` 或环境代理绕过隧道。endpoint／secret／版本／model mapping变化会改变provider binding；不能借换绑定重派旧操作或消除旧阻断。

新目标直连火山上游已完成真实glm5.3两次SSE、工具往返及合成marker验证，输入343／输出225，共568tokens。该验证使用真实上游，但不经过中央wallet，不能据此宣称17868工作台、中央套餐／预留／扣点或中断查询通过。

## 5. 已完成迁移证据和失败保留

- 中央46表严格比较通过，余额／journal平衡，migration010已应用，旧迁移保持原合同。
- Logto source／target完整schema、data／sequence、用户及应用集合严格一致；v2恢复必要角色／成员关系，两个tenant PG登录通过。
- proof：`1967b14dc39044af8dfc010f73d8b4d0`；migration：`052cbf92399d480a8f93c1cbceac5a60`；2个产品alias、1个管理员alias，无冲突。原owner-only流程追加映射，不重建用户／钱包或余额。
- 原stock WAN CLI的180秒及一次性300秒尝试失败。最终采用同库旁原collector、内存签名／认证加密传输、原严格 `_matching_inventories` 和原proof／inspect／apply。证据方法为 `co-located-private-workers`，`stock_wan_cli_passed=false`。
- 目标地址守卫曾因 `inet_server_addr()::text` 的 `/32` 表示在consumed前拒绝。独立核对中央proof／migration／alias均零后，保留旧STATE／helpers／intent／密文及人工恢复记录，仅将附加投影改为 `host(inet_server_addr())`，fresh nonce／key重新采集；原collector及context／schema／data hash算法不放宽。
- 20项入口／端口检查通过：原8项，加API错误Origin403、私有Console错误Origin403，HTTP accounts／login308与console404，及5432／3001／3002／19874／19875／19443／18080七个端口公网不可连。
- `certbot renew --dry-run` 及deploy hook成功，续期timer启用；当前证书有效期为2026-10-24至2027-01-22。仍需观察未来真实续期结果。
- 10月24日本机17868工作台切换时仅变更中央URL环境变量，provider／config／session文件hash不变，当时无认证／无pending。此后用户本人已登录并返回工作台，确认为原 `easel_1023_new`，user／tenant／wallet／epoch与迁移候选精确相同。10月25日独立管理员本人私有Console登录和只读跨页查看已完成，详见下节。
- 复核115 New API healthy、旧中央PG restart=no、旧API端口关闭；Mac旧Logto真实库allowconnections=false、client数为0，旧writer持续隔离。

失败尝试、旧容器／卷及历史收据不覆盖、不删除。一次性proof helper不是日常服务，不复用本次nonce／permit。若consumed存在而没有proof回执，或apply-started存在而没有applied回执，事务可能已经提交；只能先只读核验原表／inspect，禁止自动重跑协调器。

### 工作台后端真实Pi订阅验收

详细证据见[2026-10-24真实订阅验收报告](../../../docs/validation/2026-10-24-pi-subscription-live-acceptance.md)。本轮通过工作台后端 `/api/chat/stream` 实际Pi路径执行，不是浏览器UI发消息验收，也不同于第4节不经过wallet的上游直连测试。

- run：`7cf7b57c-7f51-4b39-b74b-cc04cf7cc2ce`；invocation：`ed9bdcee-d179-484e-8466-250630554d9c`；仅1次聊天POST，恰好1次read_file，随机marker及64行结果完整回收。
- 2个live job均settled：第一笔输入712／输出433、实际1006微点；第二笔输入802／输出218、实际728微点；合计1734微点，即0.001734点。
- 钱包available由9995178降至9993444，spent由4822增至6556；held=0、pending=0、无发点／退款。原7个历史job完整不变；各笔receipt／rate／reservation一致，每笔只有一份reserve与settle且实际分桶金额精确匹配，分桶累计等于lots，全部journal平衡。
- 首次文本后主动断开HTTP，首次GET显示running，随后取得clean_end／completed及完整结果；两次额外只读recoveries各有0次聊天POST，均查到2个operation为settled且result available，各自查询前后全部财务表hash完全相同。没有重发模型请求。
- 首段120.8347秒、完成122.3591秒，性能未优化。临时只读配置已恢复，harness config与bundled provider最终字节均与原始文件相同，自定义接口保留。

这证明本人账号在新中央的后端Pi文本／工具／扣点及网络断流查询；显式stop／取消语义、浏览器UI发消息、管理员本人Console登录不由本轮推导。

### 10月25日方案A上线与v10阶段验收

用户批准的[方案A](../../../docs/superpowers/specs/2026-10-25-pending-concurrency-proposal.md)已上线。套餐执行并发只计 `reserved`／`dispatched`，仍沿用原套餐上限；`pending_reconciliation` 按共享钱包跨产品、模型、会话及资金模式独立统计，达到3笔时拒绝新操作。3是新操作准入阈值，既有并发请求后续转入待核算时仍须保留全部未知hold，不能裁剪或清零。原9728微点、未知actual、冻结费率及账本历史保持。

本次新wheel相对上一release只有 `credits.py` 变化，SQL迁移未变。切换前设置新准入及cancel维护门禁，等待reload前Nginx worker退出和全局执行中reservation归零，再停旧服务、切新release。首次恢复入口时，单次GET的405断言遇到Nginx异步reload窗口而失败，自动重新闭门禁；失败记录保留。后续finalize改为有界轮询，成功确认API／Console assets健康、原Nginx配置恢复、公开两条只读GET返回405。

成功切换回执确认原钱包九表财务投影前后完全一致、原任务保持 `pending_reconciliation`／9728微点／`actual=null`，provider配置哈希不变，无模型调用、无SQL迁移。这是原钱包范围的九表投影比较，结果payload按长度及摘要比较，不扩展为全库所有钱包的原始全行审计。

切换完成且新v10合成任务发起前，钱包快照为：`issued=10000000`、`available=9982185`、`held=9728`、`spent=8087`微点，`active_requests=0`、`pending_reconciliation=1`、`pending_reconciliation_limit=3`。新任务必须从剩余可用余额独立正常预留，旧未知费用不由新任务结果回填。

中央最终真实隔离PostgreSQL全套为953 passed；前端最终为251 passed、0 failed、0 skipped，各轮结果单独记录。v10精确包及全新HOME、最小PATH的离线隔离smoke通过，smoke模型调用为0。本机 `http://127.0.0.1:17868/` 已切v10，归档SHA256为 `337af54acb527da5360f45638e7e3cbc70a6326663359e90adf15f67da954cf4`；切包前后原登录、四个受保护文件、配置、HOME、工作区及钱包保持一致，切包本身没有模型调用。

管理员本人已通过私有Console登录并只读跨页查看概览、三个钱包列表、原钱包详情、一个启用模型、零条审计记录及当时十三次调用；原admin／role／active／epoch一致，没有新增权限或目录写操作。真实任务结束后，同一管理员session再次查看概览，显示十五个job、一个pending、零订单及下述同一余额。这是实际登录及页面查看验收，不代表管理写操作验收。

v10浏览器UI在新session／turn中只发送一次合成任务，Pi最终为 `clean_end=true`／`completed`，回复与未预先泄露的随机fixture精确匹配。对已有SSE的只读回放核对到工具列表严格为 `['read_file']`，且工具完成。两笔新live job均settled，分别为输入667／输出438、实际991微点，以及输入727／输出49、实际437微点；两个operation、两个attempt互不相同，每个job只有一个attempt，合计1428微点，即0.001428点。

本轮核账后 `issued=10000000` 不变，`available=9980757`、`spent=9515`、`held=9728`微点。除已核账的lot余额变化外，原有财务行逐行保持；旧pending仍为9728微点、`actual=null`，没有grant或payment。两次完整GET recoveries均为全部found、`truncated=false`，返回两笔新settled／live及旧pending；lookup的聊天POST为0，查询前后财务hash均为 `15d5f24fe9eee71dd9c91ad2f5b061004ea0f6dd0f36e158132f568b8b8ad0eb`，没有重新派发模型请求。

过程中曾短暂显示 `platform_unavailable`，一次recoveries受10秒预算限制而truncated；后续在原会话完成上述两次全量查询。该现象的根因未确定，单次恢复成功不代表长期稳定性。日志正文读取曾被自动审批审查拒绝，未执行；替代的服务器固定分类计数均为0，仅支持该限定统计，不能扩展成“没有错误”或根因已明确。

本轮浏览器UI、Pi文本／只读文件工具、可信usage、1428微点结算和两次只读恢复验收已通过，最终[安全汇总回执](../../../docs/validation/2026-10-25-execution-stability-safe.json)已写入，状态为 `passed_for_current_stage`。10月24日已通过的两笔job／1734微点继续作为独立历史证据保留。详细结果与截图见[10月25日执行稳定性报告](../../../docs/validation/2026-10-25-execution-stability.md)。

最终只读探测确认工作台HTTP 200、原产品登录仍authenticated、钱包HTTP 200及上述新余额，`active_requests=0`／`pending_reconciliation=1`／`pending_reconciliation_limit=3`；产品scope保持，login OIDC issuer正确、中央API 200、公开Console 404、私有Console 200。同一管理员session及既有权限保留。最终自定义 `config.json` 与 `bundled-provider.json` 均与原始字节一致。

本轮自有 `test-v2` 合成PostgreSQL测试容器已停止，inspect确认为running=false／status=exited，容器保留；生产与用户容器未操作。这项清理不涉及生产数据库或旧pending。

### 10月26日待核算运营工作台R1生产发布

R1已部署到当前release，migration `011_console_reconciliation.sql` 已应用，SHA256为 `ad3219dcfe496137a32493bd97e69519799f6cc221494ac54e8209bf7efef7d5`。最小Console列权限及启动guard通过，上述四项systemd服务active；原配置哈希保持，原Nginx与模型准入恢复。R1只有队列、安全详情、运营进度和不可变审计，不能补扣、释放原预留或改变execution财务状态。

切换前后全钱包八张财务权威表hash相同，为 `3fcd787bf72e79a76d6669255e8e15feb2eda2a2ae68312e73d385503c248e79`。本轮范围为 `credit_lots` 1、`credit_reservations` 27、`credit_holds` 27、`credit_journals` 53、`credit_entries` 131、`execution_invocations` 11、`execution_jobs` 27、`execution_receipts` 25行，不含 `execution_results`，不能扩展成所有业务表的全库审计。当前切换快照为available 9954247、held 19456、spent 26297微点，pending为2；原9728微点、`actual=null`仍保留。生产review写入为0，模型调用为0，没有用生产旧请求做合成工单或资金写入测试。

本地中央最终1030项真实隔离PostgreSQL测试、Console前端38项及类型/构建验证通过，独立轮次不相加。合成浏览器验证一次进度POST、一次原键GET和一次模拟响应丢失，最终为等待凭据revision 1、审计1条、资金快照不变；这些是隔离合成证据，不是生产进度写入验收。unknown registry当前只在App内存保留，可跨关闭详情与站内导航；整页reload后原键持久恢复未实现。

18885私有隧道已恢复，入口接口200。此前登录白屏在新建IAB tab13后未复现，点击统一登录约3.8秒显示用户名、密码和登录按钮；没有修改认证、代理或服务器配置，不宣称根因已修复。随后一次登录进入待审批申请页；只读比对确认该subject在任意issuer下匹配admin及alias均为0，原active admin为1、当前issuer原管理员alias为1，近三天原管理员session为3。这次使用的是不同于原独立管理员的账号，没有新增权限，不将此解释为权限丢失或映射损坏，历史记录保留。

再次打开18885后已有有效本人管理员会话，AX显示“管理员 独立管理会话/已验证管理会话”。本人已在真实后台只读查看队列两笔和各自独立详情：`690ef707…`与`33344a42…`均pending、每笔原预留0.009728点、actual未知、无中央用量且tokens未知、工单版本0、暂无处理历史；未填表或点击登记。tab15保留队列，三张生产截图见[本次R1报告](../../../docs/validation/2026-10-26-reconciliation-workbench.md)。这完成本人生产只读页面验收，不用此前申请页或历史登录替代。

浏览器后root另行完成数据库只读复核，新增回执已并入[安全汇总](../../../docs/validation/2026-10-26-reconciliation-workbench-safe.json)的`production.post_browser_verification`：八表全钱包hash仍为`3fcd787bf72e79a76d6669255e8e15feb2eda2a2ae68312e73d385503c248e79`，各表计数及available 9954247/held 19456/spent 26297不变，pending为2，原9728微点actual仍null；工单、操作、审计均为0，财务/journal平衡，四服务active，复核模型调用为0。这是本轮页面读取后的新增证明，不复用切换后快照充作新复核。

## 6. 备份现状和恢复边界

首次新环境认证加密备份已成功：

```text
/var/backups/easel-encrypted/20261024T110020Z-d2bb952d-8769-4f7c-bebf-d7cabd41beb3.sealed
```

已完成归档self-verify、解密／hash及 `pg_restore --list`。**这不等于将该归档恢复到全新数据库，也不等于异地备份。** timer已启用，每日19:30 UTC、随机延迟最多5分钟（北京时间次日03:30起）。继续检查每次安全回执、失败状态和磁盘空间，不能由enabled推导未来每次成功。

登录后新增备份 `/var/backups/easel-encrypted/20261024T130357Z-7a68e359-57bf-4784-b238-19b398339e43.sealed` 已self-verify成功；它同样尚未fresh restore或offsite。恢复前仍须核对该备份的实际核账截止点，不能仅以文件较新推断覆盖全部后续消费。

10月25日已确认的新加密备份为：

```text
/var/backups/easel-encrypted/20261025T091036Z-59975318-f7f7-4268-913a-23ffeccbf322.sealed
SHA256 6679e11d3b5f7df84965909562c5cceccaf51a3be34fd11cf6982ed238f983a4
```

该归档为809802 bytes、13 files，状态done、self-verified，权限0600；尚未完成此归档的fresh restore或offsite。核账截止点仍以对应安全回执为准，不能仅按文件名推断覆盖全部后续消费，也不能将新备份自校验表述为新恢复或异地灾备成功。

10月26日R1生产切换前后两份sealed备份均在服务器以0600保留，13个文件且自校验通过：切换前SHA256为 `7ce95a0240db601513e1484b4dff479bc4d03c16b9a1f2433b8625862de244e1`，809802字节，归档为 `/var/backups/easel-encrypted/20261026T024244Z-045ba8d9-a3ad-47a9-a711-3e1442c6eb33.sealed`；切换后SHA256为 `5aef207f6fddeb9c4ce1e7974d5dda83aa7d2c7c79eec878d147d8ee0d495907`，830282字节，归档为 `/var/backups/easel-encrypted/20261026T030425Z-177dbd5d-9e00-4a35-b691-4dcc4f1fed3b.sealed`。完整身份及部署后快照已绑定[本次安全汇总](../../../docs/validation/2026-10-26-reconciliation-workbench-safe.json)，不复用10月25日文件名；两份均未据此完成fresh restore或offsite。

迁移本身已对源最终归档进行真实目标恢复和严格比较；此处待验的是后续运行／定时备份的fresh restore。不要把已成功的迁移恢复写成未恢复，也不要用迁移恢复证明新备份可恢复。

在维护者本机经SSH查看154的实际timer及下次执行时间，不猜测或重建定时任务：

```bash
rtk proxy ssh -o StrictHostKeyChecking=yes easel-zhiyin 'systemctl list-timers --all'
```

备份／恢复应覆盖中央及身份数据库、对应owner／ACL／角色、必要应用secret和同代发布／网络／隧道／TLS配置，记录各自核账截止点。私钥恢复材料单独保护；密文与仅留同机的私钥不能构成完整异地灾备。

后续恢复演练必须使用全新隔离、无消费入口的PG16目标：核对备份身份与hash，重建受审角色／owner／ACL，严格比较数据／sequence／身份集合及资金恒等式。`pg_restore --list`、三表计数或 `--no-owner --no-acl` 结果不代表完整恢复。原现场及未知pending都保留。

## 7. 验收状态与后续顺序

1. 已完成产品本人登录、迁移候选业务scope精确一致、工作台后端真实Pi工具／扣点及网络断流只读查询，保留本轮报告和钱包核账回执。旧任务／会话不硬改origin归属续接，自定义接口保留。
2. 10月25日已完成独立管理员本人经18885登录、原admin／role／active／epoch核对和只读跨页查看；10月26日R1本人有效管理员会话又完成真实两笔队列和独立详情的只读验收，生产没有填写或提交处理进度。Logto管理端及1Panel仍只经私有隧道。
3. 方案A、中央新release和v10已完成上述部署及保全验证；本轮浏览器UI一次发送、Pi精确fixture回复、单次read_file工具、两笔新live job合计1428微点结算及两次完整只读恢复已验收，最终安全汇总和本轮自有测试数据库停止清理已完成。原9728微点pending仍未知；显式stop／取消语义仍未验收，不重派未知请求，不以查询恢复成功替代stop证明。
4. R1实现、本地验证、011迁移、最小列权限、生产部署、本人页面只读验收及浏览器后独立财务复核完成；安全汇总已归档，专用合成预览、隔离schema/role和自有测试容器已完成受控清理，容器保留，真实管理隧道和用户工作台不动。旧费用仍未知，不能扩展为R2或生产处理写入验收。
5. 保留已通过的受控组件restart、51表全行一致、容器身份／卷／内部IP及资源回执。整机reboot、压测尚未执行，后续整机维护需另验数据库、Logto、API／Console、Nginx、上游隧道及旧来源fence恢复顺序。
6. 对后续运行／定时备份完成全新隔离恢复核账，补异地备份／私钥恢复及备份失败／资源／磁盘告警；R1恢复还须保全011、工单元数据与原不可变操作/审计绑定及列授权。证书续期dry-run／deploy hook已通过，未来真实续期继续观察。

截至本手册更新，产品本人登录、10月24日后端Pi文本／工具／中央扣点／网络断流查询、10月25日管理员本人Console登录和只读查看、方案A部署、v10浏览器UI／真实Pi工具／usage／1428微点扣点／只读恢复，以及10月26日R1本地、生产部署、本人真实队列/详情只读验证和浏览器后独立财务复核已完成各自范围。旧pending费用仍未知；R2可信补结算、显式stop、正式支付、媒体、云端执行、高可用、整机reboot、压测、fresh restore、offsite及告警仍未验收。迁移严格恢复、组件restart、备份自校验、10月25日原钱包九表投影、10月26日八张权威表全钱包比较及真实消费均为不同证据，不能相互替代。

## 8. 故障与权威保护

154是唯一新中央权威。先关闭新准入／派发，保全当前中央及身份数据库、journal、订单、held／pending和审计，再诊断身份、网络、隧道、资源或代码。不能因隧道故障改回test模式、跳过TLS或切换供应商重派。

**当前已经发生本人登录及真实中央消费，禁止用旧快照覆盖154当前权威库，禁止直接重开115旧钱包源继续消费。** 优先前向修复；代码回退必须另行证明兼容当前migration011、R1元数据/审计绑定、列授权、alias及身份锁协议，不能直接回到未验证兼容性的旧Console。数据库恢复必须覆盖最新核账截止点并保留已发生事实，证明旧writer仍隔离且只有一个可消费目标；未知费用不清零，alias／审计不删除。

Mac原身份库ALLOW_CONNECTIONS=false、旧Logto restart=no及115旧中央停止均为持久fence，不是待顺手清理的故障。恢复旧环境须另行审查精确对象、依赖、最新数据和唯一writer，不能直接执行可能重新provision账户／钱包的旧脚本。
