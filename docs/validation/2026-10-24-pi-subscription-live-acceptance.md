# 2026-10-24 原账号登录与 Pi 订阅真实验收

本记录补充[三机部署验证](2026-10-24-split-deployment.md)中原先待完成的产品本人登录、Pi工具调用、共享点数结算和网络断流查询。中央权威仍为154，New API仍在115，保留自定义接口及当前分支；本次没有修改应用源码、合并主分支或重新发放体验点数。

## 结果与验收范围

用户本人重新从工作台完成登录后，真实 `/v2/me` 与迁移记录中的原用户、租户、钱包和authorization epoch精确一致。当前产品账号为 `easel_1023_new`，使用原有内部体验额度，无需注册新账号。

通过正在运行的17868工作台后端 `/api/chat/stream`，执行一次真实内置Pi对话。它经中央订阅和火山New API调用GLM5.3，调用只读文件工具，再返回实际文件标记。客户端在首个非空文本token后主动断开HTTP流；首次查询同一轮时状态仍为running，随后从同一session／turn恢复完整结果。只发送一轮聊天POST，没有POST stop或重新派发聊天。

本次是工作台实际后端、内置Agent、真实供应商与数据库账本的联合验收。没有将API发起对话描述为浏览器点击发送的视觉验收；最后一次浏览器自动检查受CDP焦点命令超时影响，未取得验收后的新页面截图。

## 登录与账号边界

- 最初浏览器登录到 `admin`，该账号首次进入产品应用后取得一个空产品钱包，没有模型权益。这不是原体验钱包，不将其合并、充值或冒充为原用户。
- 正常退出该产品会话及浏览器SSO状态后，用户本人重新登录 `easel_1023_new`。新会话通过真实API检查，再与immutable migration candidate比较user／tenant／billing account和epoch，全部一致。
- 期间5分钟授权窗口多次到期。服务器记录中的密码验证与提交正常返回，最终由工作台新发起的完整回调成功。没有改登录时限、跳过PKCE／state／cookie检查或伪造本人会话；转圈现象未单独归因或宣称已通过代码修复。
- Console独立管理员本人登录仍是单独待验项目；普通产品登录不会取得后台管理员权限。原管理员映射不因本次产品登录而改变。

## 真实工具、流式与断流恢复

运行ID：`7cf7b57c-7f51-4b39-b74b-cc04cf7cc2ce`。中央invocation：`ed9bdcee-d179-484e-8466-250630554d9c`。

1. 在工作区独立验收目录创建随机marker文件。提示词只提供文件路径，不包含marker值，要求只调用一次 `read_file`，返回标记和1至64的完整编号。
2. 临时通过正常设置API将sandbox改为read-only，保持Pi／中央订阅／本机执行及原模型和供应商配置。没有执行命令、写入工具或读取用户业务文件。
3. Pi确实仅调用一次 `read_file`，正确返回未在提示词中出现的marker，并完成全部64行编号。
4. 收到首个文本token后关闭SSE连接；首次同轮 `chat/last` 状态为running，随后恢复为顶层及route均runtime=pi、`clean_end=true`、`stop_reason=completed`。
5. 同轮事件重放包含工具活动及done。中央发现两个live操作，均settled且结果缓存available。没有第二轮聊天POST，没有自动重试。

该轮首个文本token耗时120.8347秒，完整结果恢复耗时122.3591秒。这是一次包含工具往返的实际耗时，不能据此承诺响应速度、并发性能或将耗时直接归因于中央服务；性能优化和压测仍待完成。

## 真实扣点与账本核验

沿用原内部体验费率 `internal-glm53-20261023-v1`：输入每token为1/2微点，输出每token为3/2微点，两项相加后仅向上取整一次。该费率是内部体验配置，不代表已确定的商业套餐或人民币售价。

| 模型操作 | 输入token | 输出token | 实际微点 | 实际点数 |
| --- | ---: | ---: | ---: | ---: |
| 操作1 | 712 | 433 | 1006 | 0.001006 |
| 操作2 | 802 | 218 | 728 | 0.000728 |
| 合计 | 1514 | 651 | 1734 | 0.001734 |

| 钱包项目 | 验收前 | 验收后 |
| --- | ---: | ---: |
| 累计发放 | 10点 | 10点 |
| 可用 | 9.995178点 | 9.993444点 |
| 累计已用 | 0.004822点 | 0.006556点 |
| held／预留 | 0 | 0 |
| expired／refunded | 0／0 | 0／0 |
| 待核算 | 0 | 0 |

费用验证从服务器两份只读数据库快照独立完成，不仅依赖前端余额或SSE done：

- 原7个settled job和旧receipts／entries／journals／holds完整保留。
- 两个新job与各自唯一attempt、receipt、reservation匹配；冻结费率与真实usage计算得到1006及728微点，均与actual一致，合计严格大于零。
- 每job恰有一笔reserve和一笔settle，各journal的实际bucket金额精确一致：reserve扣available并增加held；settle扣除held、转入spent并归还未用预留。
- 各bucket累计的credit_entries与credit_lots逐项相等；每个journal总delta为零；钱包available减少1734、spent增加1734，发放／到期／退款不变。
- invocation的operation_count=2、spent=1734、held=0，与两笔结算一致。

## 两次只读状态查询没有重复扣点

在实际调用完成后，另取财务基线，连续两次使用 `/api/platform/central/execution-recoveries` 查询原invocation／operation，再取只读财务快照。

两次查询脚本的聊天POST计数均为0，返回的两个操作集合和财务状态一致，均settled、result_state=available。前后相关财务表全部行摘要、各表计数及所有余额完全相同：

```text
26c79345d046d4496b4f0d13ecf5f0866488953b7bd78f8cb8f93d2652434fca
```

这证明本次网络断流后的状态查询没有重新生成、重新派发或再次扣点。显式任务stop及成本未知的pending_reconciliation是不同情形，本次未通过主动取消真实任务制造未知费用。

## 配置、备份与保留状态

临时read-only设置已恢复。最终两个原文件均与本轮之前的原SHA256逐字节一致：

- `config.json`：`2f62f99a2b24df0c7d4a89793333a8c873e12d25c777d6fbd786e6a3a5f36d08`
- `bundled-provider.json`：`bc36a4ad692e1fd3289926f827d7ea251e026232c0110450681fb36b19b875d0`

因此自定义供应商配置、模型选择及原工具权限没有因验收而遗留变化。登录会话文件按正常本人登录更新，不将它与原匿名会话文件hash相同作为验收条件。

本次登录及结算后新建服务器本地认证加密备份：

```text
/var/backups/easel-encrypted/20261024T130357Z-7a68e359-57bf-4784-b238-19b398339e43.sealed
```

密文SHA256为 `060d04ef26a4eb499a99ebb55f2c61c9e68af2e0e41ae1825666dcb6308ebd39`，root0600，journal invocation为 `d3d3ef960e6a4ffea778eef5595d998f`，正常完成与唯一新文件hash对应。完成原备份脚本的认证解密／归档检查，仍未对这份新备份执行隔离fresh restore，不是offsite备份。

已有本轮新登录、空admin产品钱包和两笔真实消费事实，不能用旧快照覆盖154或直接重开115旧钱包源继续消费。

## 验证与后续

客户端纯单元测试本轮重跑14项通过、0失败、0跳过；随后执行上述真实验收。客户端SHA256：`0eeadc46f7c657f9112b23b811e9f5de78a6dcb19e20a23f930ad8f594875101`。14项纯测试不作为真实供应商证据；真实结果分别由工作台响应与SQL账本检查提供。

脱敏聚合证据：[本轮安全回执](2026-10-24-pi-subscription-live-acceptance-safe.json)。原始账本快照和诊断日志仍仅留服务器root私有目录；没有取回密钥、DSN、密码或原始数据库包。

后续仍需独立完成：管理员Console本人登录、浏览器UI发消息视觉验收、显式stop的财务情形、整机reboot、负载测试、运行备份fresh restore、异地密文及私钥恢复、告警、正式商户付款和订阅媒体／云端Agent。不得把本次文本订阅与网络断流成功扩大到这些项目。
