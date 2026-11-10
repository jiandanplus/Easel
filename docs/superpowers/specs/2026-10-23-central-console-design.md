# 独立中央运营后台设计

用户已在五模块后台建议后回复“继续”，批准继续开发；随后明确首个管理员使用单独账号，稍后亲自注册。沿用当前 codex/ui-redesign，不合并主分支，不改 Easel 自定义接口或既有钱包。设计与计划留在工作区，本批不自动提交或推送。

## 选择与部署

选择独立中央 Console ASGI 服务和独立 React/Vite 管理站点，复用现有中央业务层与数据库。相比嵌入 Easel，这样不把运营权限和资产放入桌面包；相比仅在 CLI 外包装网页，这样能提供真正的管理员会话、权限和事务审计。

Console 使用独立 origin，隔离预览计划 http://127.0.0.1:18875/（启动前验证端口），服务端仅 loopback，经既有 SSH 单独转发。保留业务 API 18874、工作台17868和既有Logto。Console使用独立受限数据库角色，Web进程不持 migration owner、内部发点权限、模型供应商业务密钥或支付私钥。产品 runtime 原安全检查不得放宽；新 Console 表不得通过 SELECT ALL 误授权给产品角色。

## 登录与授权

独立 Logto Traditional Web 应用，固定回调 /oidc/console/callback；复用严格 OIDC discovery、JWKS、issuer/audience/azp/nonce校验。五分钟一次性登录事务，state及cookie绑定，S256 PKCE。管理会话为随机不透明 HttpOnly/SameSite=Lax cookie，数据库只存其哈希，有绝对期限、退出和即时角色校验；浏览器不获得 access/refresh token或client secret。

管理员ACL按精确(issuer,subject)绑定，角色为 admin/operator/viewer，默认无记录。普通注册不会获得管理权限。新账号经签名OIDC成功但尚无ACL时，只记录已验证的待授权申请，显示非凭证的申请编号；owner运营命令审核该申请并绑定角色，Web不能自行授权。用户将亲自注册独立管理员，不把easel_1023_new升级为管理员。

所有 /api 读取需管理会话；所有写请求要求规范同源Origin、同步CSRF token、明确权限、原因和幂等键。产品Bearer不作为Console凭据。权限不足返回明确403，错误不回显请求正文、token或供应商响应。登录错误页为静态安全页面。无任意redirect或跨域放行。

## 第一版功能

1. 总览：真实用户/产品/订单/调用计数，按资金模式分开的点数汇总，待核算数量；无伪造趋势。
2. 用户与产品：分页搜索、用户详情、所属租户/钱包/产品会话元数据；不返回凭据、登录事务、Cookie或原始JWT。首版不修改身份系统的用户密码，不在Web中授予管理员。
3. 套餐与钱包：不可变套餐版本列表与发布，钱包/资金批次/预留/不可变分录的只读查询，内部体验与测试资金明确区分。内部发点维持离线CLI，不增加网页发点或直接改余额。
4. 订单与支付：全量订单筛选、详情、报价目录与发布/撤回、退款状态及待对账信息；支付模式disabled如实显示，首版不从Console发起真实收款、退款或手工标记付款。
5. 模型与渠道：模型版本、费率、启停、真实调用记录、配置过的供应商标识与就绪状态；发布只允许服务器预先允许的provider，不在网页上传供应商Key。
6. 运营审计：管理员、动作、原因、目标、幂等键摘要和时间检索；不可变审计与目录变更同事务。无删除账本或强制结算功能。

目录写操作仅admin/operator且有对应能力；viewer只读。套餐、报价和模型版本沿用原有严格校验和不可变规则。后台不会启用未配置的支付、媒体或云执行能力。

## 边界与数据契约

独立模块 console_config/auth/permissions/service/catalog/app；Auth迁移008、操作审计迁移009。现有001–007不编辑。前端 services/platform/frontend 独立构建，不导入Easel App、用户store/api或portable-assets构建器。

列表统一 {items:[], limit:25, offset:0, has_more:false}，limit1–100、offset0–100000、搜索长度最多128，SQL参数化，固定排序。空态与错误态区分。微点用十进制字符串，前端通过BigInt格式化，不能浮点扣账。详情固定白名单，无任意SQL/表名/JSON日志dump。只读页不触发过期结算、查单、模型请求或账务写入。

REST /api/session、overview、users、users/{id}、products、plans、wallets、wallets/{id}、orders、orders/{id}、offers、models、providers、executions、audits。POST /api/plans、/api/offers、/api/offers/{id}/withdraw、/api/models、/api/models/{id}/state。写请求definition/reason，状态请求enabled/reason，Header Idempotency-Key；actor由服务端会话确定。

目录写服务在同一DB事务重新验证会话和当前ACL，复用现有业务校验/发布服务（连接绑定适配器，不执行外部调用），然后写不可变操作结果及审计。相同admin+key+相同内容重放返回原结果，内容变化返回409。未知模型费率或幂等冲突不可默默覆盖。

## 验证与完成标准

先写失败的真实PG测试，覆盖无ACL拒绝、普通用户Bearer拒绝、OIDC重放/超时/nonce、cookie/CSRF/Origin、角色即时撤销、低权DB角色、跨用户只读、敏感字段不泄漏、分页注入、目录原子审计、版本冲突和幂等并发。前端测CSRF请求、权限可见性、资金模式与整数格式化、错误/空态；构建与静态文件CSP/XSS/路径遍历验证。

浏览器以真实Console服务打开登录入口；真实管理员注册/授权依赖用户完成，不用测试账号冒充。功能浏览器验证可在专门的无生产凭据隔离测试服务中运行，明确标识证据范围。部署前审查精确源文件、独立角色/DB/schema/端口、备份配置和所有权；不得改旧钱包、重启NewAPI、放宽产品边界或对生产API发测试消费。最终分别报告代码/测试、浏览器、实际管理员登录和生产边界。
