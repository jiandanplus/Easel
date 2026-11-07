# 2026-10-24 三机分工部署验证

本记录对应 operation `fd13efa319487866926acf525e389dbd`。中央服务已经部署并开放约定入口；本人登录与工作台账本验收仍待完成。部署、恢复比较、HTTP 检查、上游真实调用和用户扣点分别记录。

## 实际拓扑

| 主机 | 本次实际职责 |
| --- | --- |
| `154.217.245.179` | 中央 API、私有运营 Console、Logto、独立中央 PG16 与身份 PG16、Nginx、加密备份 |
| `115.190.211.112` | 原 New API、PG17、Redis 和插件保持运行；通过专用 SSH＋TLS 通道供给新中央；旧中央 writer 停止 |
| `139.196.51.51` | 现有网站保持原部署；本次未对该机实施变更 |

公开入口为 `https://accounts.drawbookai.cn` 和 `https://login.drawbookai.cn`。API 供软件调用；产品从工作台发起 OIDC 登录。`console.drawbookai.cn` 的 HTTP／HTTPS 均返回 404。维护者通过 SSH 转发访问 `http://127.0.0.1:18885/` 的中央 Console，以及 `http://127.0.0.1:18886/` 的 Logto 管理端。1Panel 沿用本机18880隧道及原访问路径。

发布目录：`/srv/easel/releases/20261024-split-v2`。保持 `codex/ui-redesign` 分支和 HEAD `e972af07444e181e3eb737441ecac306bca778b3`，未提交、推送或合并本批变更。

## 已验证项目

| 验证层 | 实际结果与边界 |
| --- | --- |
| 中央逻辑迁移 | 最终源备份在新服务器真实恢复，46张原业务表的结构、全部数据、sequence、owner／ACL严格比较相同，钱包lots恒等式及journal平衡；随后单独应用010迁移 |
| 身份逻辑迁移 | 保留原应用ID／secret、用户密码hash、租户角色／成员和数据库凭据。原严格collector确认源／目标完整schema、data／sequence、用户与应用集合一致；两个租户角色实际登录通过 |
| 身份映射 | proof `1967b14dc39044af8dfc010f73d8b4d0`，migration `052cbf92399d480a8f93c1cbceac5a60`；2个产品alias、1个管理员alias，无冲突，没有重建用户或钱包 |
| 公网与私有边界 | 20项检查通过：API与私有Console健康、匿名401、错误Origin403、错误Host拒绝、精确HTTPS issuer、公开Console404、两个公开站点HTTP308、7个内部／运维端口公网不可连 |
| 本机工作台 | 17868预览仍使用同一v6软件包、工作区和HOME，仅中央环境变量改为 `https://accounts.drawbookai.cn`；provider/config/session文件SHA256前后不变，切换时无认证及pending登录 |
| 重启／持久性 | 对新中央两个PG、Logto、API／Console与供给隧道进行受控组件重启；容器ID、镜像、卷及内部地址不变，当前51张中央表全部行数据前后一致。未做整机reboot |
| 跨云供给 | 重启后 `https://api.drawbookai.cn:19443` 经SSH到火山443，SNI／证书验证有效；认证模型清单含5个模型，已配置模型存在 |
| 真实上游模型 | 经新通道对 `glm-5.3` 执行2次有限SSE请求，工具调用、合成工具结果回传和最终文本标记通过；输入343／输出225，共568tokens。只发合成内容，无项目文件。此项直接验证上游，不经过中央钱包，不等于工作台扣点验收 |
| TLS续期 | 证书有效期2026-10-24至2027-01-22；certbot timer启用，renew dry-run及nginx deploy hook成功 |
| 定时加密备份 | 首次真实备份selfverified、内存解密／归档hash与pg_restore-list通过；密文root0600，目录root0700；每日19:30UTC定时器启用，随机延后最多5分钟。尚未将这份定时备份恢复到新隔离库 |
| 旧writer隔离 | 115旧中央进程／PG停止，PG restart=no，旧API／Console端口关闭；Mac原Logto停止且restart=no，真实身份DB禁止连接、客户端0。原卷和备份保留，原New API健康 |

入口检查的7个受限端口为5432、3001、3002、19874、19875、19443、18080。新机API／Console／供给隧道只监听对应loopback端口；PG和Logto没有Docker端口发布。1Panel虽有主机监听，公网探测受防火墙限制。

## 恢复证明方法

原stock WAN CLI的180秒和一次性300秒尝试未成功，不计为通过。最终采用受审的 `co-located-private-workers`：在两个真实数据库旁执行原 `_inventory`，源端内存签名及认证加密，目标执行原严格比较和原owner-only proof／inspect／apply。原collector SHA256保持 `11d2e78c2025c439121e1bfb548dee17f69fa238a757a3e710356f2b60cfabcf`。

源索引规范化在完整备份、停写、精确索引／依赖守卫和合成DDL／回滚演练之后执行，使用服务器自己的原索引定义重建；没有删cast、忽略结构字段或放宽fingerprint。一次目标附加地址守卫因 `/32` 表示差异在consumed前拒绝，独立确认零proof／migration／alias后保存旧现场，以新nonce重新采集。该附加检查只改为SQL `host(inet_server_addr())` 投影，原context及结构／数据摘要规则不变。

重启前后中央全行摘要为 `6f4af4e1f9be914093f048d6788a194f04a9d579289c92d6edb5b7c7f8e36eb8`。这描述的是本次受控重启的静止窗口，不是未来任意时刻的固定数据库摘要。

## 备份与资源

首次定时备份文件：

```text
/var/backups/easel-encrypted/20261024T110020Z-d2bb952d-8769-4f7c-bebf-d7cabd41beb3.sealed
```

密文789322字节，SHA256 `a92a7779245bbaf43548ab03b657420bbbac414cd8b05e7c0f08f222797dbef1`。本次journal invocation为 `b81b2540965b488e9f17572b3035a23d`，正常完成事件与唯一新文件的UUID、长度和hash一致。oneshot结束后systemd可回收unit运行状态，不能用之后空的InvocationID或默认ConditionResult否定实际journal及文件证据，也不能只以 `systemctl start` 返回0宣称成功。

归档覆盖两个PG逻辑dump／角色，以及限定的应用配置；敏感内容仅在服务器内存和最终密文中处理，私钥未离开154。各数据库／角色／配置按顺序采集，不是跨库原子快照，不提供PITR。定时器已启用不等于下一晚已执行；本机密文与同机私钥不构成异地恢复。

当次空闲观察：总内存3915MiB、可用2907MiB；根盘48.28GiB、空闲40.93GiB。容器限制为两个PG各512MiB、Logto768MiB；API512MiB、Console256MiB。该数值为一次运行观察，未作并发负载、容量承诺或高可用验收。

## 仍待验收

- 产品用户本人经新域登录，核对原用户／租户／钱包、会员、lots到期、余额和历史；独立管理员经私有Console本人登录，核对原admin／role／active。
- 使用已批准的原体验额度完成工作台Pi真实流式对话、工具调用、扣点与中断状态查询；不重新发点，不重派未知结果。
- 本次新定时备份的独立fresh restore、异地密文及私钥恢复方案、告警、负载测试及整机重启均未验收。迁移过程中实际恢复成功与这份后续备份的恢复演练是不同证据。
- 支付仍disabled；真实微信／支付宝收款、媒体订阅与云端Agent执行不属于本次已完成能力。

截至19:09本机最终查询，产品登录尚未完成，先前5分钟登录窗口已经到期。继续时从工作台左下角重新点击登录／注册产生新窗口，不刷新或重用旧authorization链接；使用原账号，无需重新注册。不得由维护脚本伪造本人登录或替代权限验收。

154是唯一新中央权威。已有新登录或消费后，禁止以旧快照覆盖最新库或直接重新开放115旧钱包。故障应先关闭新准入并保全当前事实、held／pending和审计，再前向修复或回退兼容代码；未知费用不能清零。

## 证据位置

- [脱敏汇总回执](2026-10-24-split-deployment-safe.json)：10份服务器回执及20项外部检查，保留各自时点，不含私有env、DSN或密钥。
- [迁移执行计划](../superpowers/plans/2026-10-24-zhiyin-full-service-migration.md)。
- [当前三机运行手册](../../services/platform/deploy/SPLIT-DEPLOYMENT-20261024.md)：私有管理入口、重连、检查、备份及权威恢复边界。
- [私有Console入口截图](assets/2026-10-24-private-central-console.jpg)：真实18885页面；截图为匿名登录入口，不替代管理员本人登录验收。

历史失败尝试、旧容器和历史收据保留，不覆盖改写为本次成功。

后续更新：同日已完成原产品账号本人登录、Pi真实只读工具、1734微点结算和网络断流查询验证，见[原账号登录与Pi订阅真实验收](2026-10-24-pi-subscription-live-acceptance.md)。本文上方的待验清单保留部署首轮时点口径，当前进度以执行计划及后续回执为准。
