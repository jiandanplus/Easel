# 中央平台三机分工部署执行计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development for independent preparation and review. The root agent owns every remote mutation.

**Goal:** 按用户确认的不扩容方案，将中央 API、私有管理后台、Logto、中央与身份 PostgreSQL 部署到 `154.217.245.179`；New API、其 PostgreSQL 17／Redis／插件保留 `115.190.211.112`；阿里云 `139.196.51.51` 现有网站不变。保留真实身份、共享钱包及自定义接口模式，以154作为唯一新写入权威。

**Architecture:** 新机集中中央业务与身份数据。公网仅开放 `https://accounts.drawbookai.cn` 和 `https://login.drawbookai.cn`，由 Nginx 处理 HTTPS；`console.drawbookai.cn` 返回404。Console 只通过 SSH 本机18885访问，Logto管理端通过18886，1Panel保留原18880隧道。中央 API 经受限 SSH＋TLS 通道调用火山原 New API，不跨云访问其数据库。此阶段不实施多节点高可用或云端 Agent。

**Tech Stack:** Ubuntu 24.04.1 x86_64、Docker 29.9.2、Compose 5.6.1、Python 3.12、现有平台 wheel／锁依赖、Logto 1.45.0、PostgreSQL 16.15；火山保留 New API v1.0.0-rc.45 与原 PostgreSQL 17／Redis 7。迁移不同时升级业务组件。

## 全局约束与证据口径

- 在 `codex/ui-redesign` 工作；计划起点 HEAD 为 `e972af07444e181e3eb737441ecac306bca778b3`。起点已有247个脏路径是当时观测，不代表当前计数；保留用户现有状态，不自动 commit／push／merge。
- 用户已明确批准本次美国目的地的真实中央及身份敏感载荷迁移。最终实际范围为中央业务和身份数据；New API、其供应商 Key／渠道／插件／PG17／Redis留在火山，中央仅持所需业务令牌。
- 敏感载荷采用源端应用层认证加密、目标 root 私有解密；不把明文包、私钥或凭据写入中转文件、源码、浏览器或聊天。公开记录只保留状态、摘要和必要对象标识。
- 原服务、容器、卷、配置、备份和失败现场保留。旧 writer 必须持续隔离，不能两端各写一套钱包。未知费用／pending不清零，不赠点、不重建用户或钱包。
- 按主机、容器full ID、镜像、cluster／database／schema、PID＋启动时间和管理器核验来源；禁止复用旧permit／nonce／intent或把失败尝试改记成功。
- 原 Logto PG为arm64、目标为x86_64，使用逻辑备份／恢复。owner／ACL／角色、数据／sequence、应用ID、管理员active／role和资金恒等式分别核验。
- 严格 identity collector 保持原 SHA `11d2e78c2025c439121e1bfb548dee17f69fa238a757a3e710356f2b60cfabcf`。原 stock WAN CLI 的180秒及一次性300秒尝试失败，不能宣称其通过；最终采用受审 co-located 原 collector、认证传输和原严格比较／proof／inspect／apply完成。
- 支付保持 `disabled`。部署、本人登录、真实模型／工具／扣点、中断查询、重启、全新备份恢复、异地恢复分别记录，不能相互替代。

## Task 1：新目标与加密传输准备

- [x] 核验154目标、115实际来源、Mac Logto来源和现有1Panel，取得美国目标敏感载荷授权。
- [x] 建立 `/srv/easel` 发布根与 `/root/easel-migration-20261024` 私有操作根，绑定主机及 operation `fd13efa319487866926acf525e389dbd`。
- [x] 完成受审认证加密、接收密钥、加密传输及目标私有解密／摘要核验；不落本机明文中转包。
- [x] 配置公网accounts／login HTTPS、console404及私有管理隧道；未以普通注册授予管理员权限。
- [x] 建立154到火山的专用最小权限 SSH＋TLS 通道，完成无消费接口检查。转发限定源loopback443，允许面是该443全部virtual hosts，不宣称限制HTTP路径。

## Task 2：固定发布与隔离恢复准备

- [x] 安装固定发布 `/srv/easel/releases/20261024-split-v2`，使用本批wheel／锁依赖及独立环境，不沿用115预检安装收据。
- [x] 建立独立中央与身份PG16持久容器／内部网络，完成本次真实恢复和权限核验；New API PG／Redis不迁移。
- [x] 安装独立API／Console systemd服务及Nginx／gateway tunnel。Console最终配置采用已有literal loopback HTTP契约，不启用模板的公网Console代理块。
- [x] 记录当前组件运行状态和固定容器／地址；旧失败目标容器停止保留。
- [x] 完成受控新组件restart及资源基线观察：三个当前容器的ID／镜像／卷／内部IP不变，四个unit均active。内存3915MiB、available2907MiB；磁盘48.28GiB、free40.93GiB；两个PG各512MiB、Logto768MiB、API512MiB、Console256MiB限额。此为单次运行观察，不是负载测试或整机reboot证明。

## Task 3：身份索引与严格结构门槛

- [x] 完成两条索引的精确白名单、来源／依赖／owner／状态守卫和事务回滚方案审查；保留严格collector，不删cast／指纹字段或过滤hash。
- [x] 最终恢复的真实Logto source／target完整schema、data／sequence、用户和应用集合严格一致；旧partial-index表示差异不再作为放宽比较的理由。
- [ ] 将同版本合成DDL演练、源规范化操作及固定点验证各自执行回执与最终归档统一索引。此项是证据整理待补，不将纯测试当作真实数据库执行证明。

## Task 4：最终维护窗口、停写与归档

- [x] 固定来源进程／容器／管理器及最终备份合同，保留真实任务、预留、pending及账本事实。
- [x] 115旧中央API／Console进程及中央PG停止、restart=no；原New API／PG17／Redis继续供给。
- [x] Mac原Logto停止且restart=no，真实Logto数据库 `ALLOW_CONNECTIONS=false`；原数据保留，不能直接重新开放作为正式身份入口。
- [x] 最终源归档经认证加密传输后在154实际恢复；owner／ACL／角色及必要应用回调纳入本次恢复合同。
- [x] 保留失败恢复容器、旧helper／intent／密文及手动恢复记录。地址表示守卫失败发生在consumed前，经独立零proof／migration／alias核验后另起fresh nonce，不回滚业务数据。

## Task 5：严格恢复、身份连续性和新服务

- [x] 中央46表严格比较通过，余额／journal平衡；Logto两端完整schema／data严格一致，两个tenant PG登录通过。
- [x] 新中央库执行migration010，保留001–009；建立对应低权运行角色及API／Console权限。
- [x] 通过同库旁原 `_inventory`、源认证加密packet、目标原 `_matching_inventories` 和原owner-only proof／inspect／apply完成身份迁移。proof `1967b14dc39044af8dfc010f73d8b4d0`；migration `052cbf92399d480a8f93c1cbceac5a60`；2个产品alias、1个管理员alias，无冲突。
- [x] 证明与映射后启动154 Logto／API／Console；原writer持续隔离。最终证据方法为 `co-located-private-workers`，`stock_wan_cli_passed=false`。
- [x] accounts／login使用正式HTTPS；Console用 `http://127.0.0.1:18885`、SSH转发至目标loopback19875，issuer仍为 `https://login.drawbookai.cn/oidc`，原应用ID／secret保留。
- [x] 本机17868工作台仅在环境变量中切至 `https://accounts.drawbookai.cn`；provider／config／session文件hash不变，切换时无认证／无pending，不修改自定义接口。

## Task 6：入口、备份与待完成验收

- [x] 公网accounts／login HTTPS及私有Console拓扑就绪；20项入口／端口检查通过：原8项，加API错误Origin403、私有Console错误Origin403，HTTP accounts／login308与console404，以及5432／3001／3002／19874／19875／19443／18080七个端口公网不可连。入口检查不代表本人登录或中央消费。
- [x] `certbot renew --dry-run` 及deploy hook成功，续期timer已启用；当前证书有效期2026-10-24至2027-01-22。不以dry-run替代未来真实续期结果。
- [x] 首次运行后认证加密备份成功：`/var/backups/easel-encrypted/20261024T110020Z-d2bb952d-8769-4f7c-bebf-d7cabd41beb3.sealed`。完成self-verify、解密／hash及 `pg_restore --list`；未对这份归档做fresh restore，不属于offsite备份。
- [x] 启用定时备份：每日19:30 UTC，随机延迟最多5分钟（北京时间次日03:30起）。timer enabled不代表未来每次都成功。
- [x] 新目标直连火山上游完成真实glm5.3两次SSE、工具往返及合成marker验证；输入343／输出225，共568tokens。该请求不经过中央wallet，不属于17868工作台或中央扣点验收。
- [x] 受控新组件restart后，中央当前51表全行before／after一致，SHA `6f4af4e1f9be914093f048d6788a194f04a9d579289c92d6edb5b7c7f8e36eb8`。与迁移基础46表严格恢复比较是不同阶段的证据；未执行整机reboot。
- [x] 复核旧来源fence：115 New API healthy、旧中央PG restart=no、旧API端口关闭；Mac旧Logto真实库allowconnections=false、client数为0。
- [x] 用户本人完成产品登录并返回17868工作台，确认为原 `easel_1023_new`；当前user／tenant／wallet／authorization epoch与migration candidate精确一致，未新建钱包。
- [ ] 独立管理员本人通过18885登录，核对原admin／role／active及拒绝边界。
- [x] 工作台后端 `/api/chat/stream` 的真实Pi路径完成1次聊天POST、1个invocation、2个live job settled，恰好1次read_file；随机marker和64行结果完整回收。使用已有授权／余额，实际扣1734微点（0.001734点），无发点／退款／held／pending，原7个历史job不变；逐笔费率、reserve／settle分桶及全部journal平衡通过。
- [x] 首次文本后断开HTTP，首次GET仍为running，随后取得clean_end／completed；另两次只读recoveries各有0次聊天POST，均查到2个settled／result available operation，查询前后全部财务表hash完全相同。此为网络断流与只读查询验收；未测显式stop，未做浏览器UI发消息验收。
- [x] 登录后新增加密备份 `20261024T130357Z-7a68e359-57bf-4784-b238-19b398339e43.sealed` self-verify成功；该备份尚未fresh restore或offsite。
- [ ] 对后续运行／定时备份完成fresh隔离恢复核账；另行建立offsite及目标私钥恢复方案。迁移本身的真实目标恢复和严格比较已经成功，不属于此待补项。
- [ ] 补充备份失败／磁盘／资源告警及最终交付回执。未验证项目保持待补；整机reboot未经本次组件restart验证。

## 当前进度与恢复边界

2026-10-24：三机分工实际部署、严格身份恢复、产品本人登录及工作台后端真实Pi工具／扣点／网络断流查询已完成；154是唯一新中央权威，火山New API供给与阿里现有网站保持原状。运行细节见[当前运行手册](../../../services/platform/deploy/SPLIT-DEPLOYMENT-20261024.md)，本轮Pi证据见[真实订阅验收报告](../../validation/2026-10-24-pi-subscription-live-acceptance.md)。首段120.8347秒、完成122.3591秒，性能未优化；harness config和bundled provider最终恢复为原字节。管理员本人登录、显式stop、整机reboot、压测、新备份fresh restore／offsite和告警仍待验。原历史CUTOVER／收据保留原时点内容。

当前已经产生本人登录及真实中央消费，新事实必须保留。故障先关闭新准入／派发，保全154最新中央与身份数据、journal、订单、held／pending及审计，优先前向修复或在当前权威库上回退兼容代码。**禁止用旧快照覆盖当前权威库、直接重启115旧钱包源继续消费、清零未知费用或删除alias／审计来伪装回退。** 数据库恢复必须覆盖最新核账截止点并重新证明只有一个writer；受控恢复副本不得成为第二套可花余额。
