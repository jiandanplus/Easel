# 恢复捕获 F2-r3：冻结合同、独立复审与证据归档

**后续更新（2026-11-02）：原始Linux进程八场景已实际通过。** 固定fc3 producer及原三文件／八项断言保持，V2仅修复实际初始化规范路径与argv0的关系并加强文件身份证明；完整执行器、原期限／输出／fresh终态围栏及独立实际证据审计均通过。见[实际报告](../../outputs/_run/f2-native-init-alias-20261102/ACTUAL-V2-REPORT.md)。此更新只关闭process-only子门，PG snapshot／25006、source facts、真实capture／FWPD、完整新DR等仍未关闭。以下内容保留2026-10-28冻结时的历史范围和结果。

**F2-r3 已通过限定本地源码／合成合同／本机进程测试范围的独立复审，无剩余P1／P2阻断。它不构成实际恢复或生产运行许可。** 结果仍为 `approved=false`、`actions=[]`；实际Linux、PostgreSQL snapshot／SQLSTATE25006、source facts、服务器capture及W／P／D均未验证，旧真实mixed DR仍为failed。AV的独立Linux结果不用于证明F2。

## 冻结来源与合同

| 文件 | SHA256 |
|---|---|
| services/platform/scripts/media_restore_capture.py | fc3fa9cbdaa751c8efeb7ddb5a40c69efa0b7a3a7675c4b3f7832210577ed261 |
| services/platform/tests/test_media_restore_capture.py | 2dd3311455ae4e9a886efa5f1fc4112a2e0852f4b7275b1e4569ea022ba2382d |
| docs/superpowers/plans/2026-10-28-restore-window-v2.md | a7bedbe99d78b78117553bd89bd984e7a45362d76de69da50908da1eac8458ae |

独立复审读取的10个仓库源／测试／计划及两个原probe前后SHA一致。F1两文件、旧五脚本和123个require不变；financial projector、waiver、query、ReadPolicy、SourceBroker、Docker argv、公开API参数与私有receipt字段均与原r1相同。详见[独立合同核对](../../outputs/_run/restore-capture-f2-r3-20261028/r3/independent/unchanged-contracts.json)。

F2使用固定本地Unix socket／CLI／空私有config和full container ID，固定只读导出snapshot下复用完整库存、原始lot／全部unresolved与实际enabled媒体观察。没有调用会expire写入的ledger summary。最终资产TTL严格依据推导上界 `max(最后实际OS UTC,最后已观测DB UTC)+5s`，要求剩余严格大于1800秒；它不是新测得的DB时间。原5秒偏差、900秒窗口及相邻样本不倒退约束不放宽。

每次非零signal都必须新证明同一个仍未reap的owned child身份。被KeyboardInterrupt打断且仍owned的清理可再次signal；reap／失所有权／预算耗尽后不得再发非零signal。Popen首cleanup时固定12秒总deadline，重入不延；leader wait最多10秒、组消失最多2秒且都裁剪到剩余总预算。预算尽只非阻塞reap／close并拒绝，不宣称未知进程已清完。Mac使用test-only同parent SDK bridge，不是生产Darwin路径或Linux证明。

## 实际结果分列，不能累加为一次full

| 执行 | 结果与边界 |
|---|---|
| 作者r3定向 | 136 passed，0 skipped；5.162s |
| 独立原TTL断言 | 1 passed，9 deselected；3.731s |
| 独立原r1阻断集合 | 11 passed，2个已撤回terminal-history期望deselected；1.945s |
| 独立原r2阻断集合 | 9 passed，已澄清B期望deselected；1.715s |
| 独立新增边界 | 22 passed，0 skipped；2.619s |
| 独立真实本机合成child ownership／重入 | 1 passed，0 skipped；1.396s；仅Mac test bridge |
| 独立完整候选测试文件 | 136 passed，0 skipped；3.999s |
| 独立未改F1闭包 | 320 passed，0 skipped；4.997s |
| 独立原pure core／mixed闭包 | 11 passed，0 skipped；0.031s |
| 原B未改断言，单独保留 | **1 failed，9 deselected**；1.527s；该断言要求整个生命周期只能发一次SIGKILL，root已澄清为过强期望、非阻断。它未被改成绿色，也不称全部原probe通过 |

完整报告见[独立r3复审](../../outputs/_run/restore-capture-f2-r3-20261028/r3/independent/review.md)、[安全收据](../../outputs/_run/restore-capture-f2-r3-20261028/r3/independent/safe.json)及[原B失败](../../outputs/_run/restore-capture-f2-r3-20261028/r3/independent/original-B-nonblocking.log)。本次文档归档只重新核对证据和SHA，没有重跑这些测试。

## 历史与复制范围

- r1：作者100绿后独立请求修改，四类P2对应原阻断集合6 failed／5 passed；两个通用terminal历史期望被撤回。旧源码、tar及失败保留。
- r2：修正前四类后，又发现final TTL被closing耗时消耗。原B整个生命周期单signal期望另外澄清。中间退出过渡1fail继续保留，不以重跑绿覆盖；内核查询非原子，不能只凭getpgid存在断言仍running或zombie。
- r3：纯最终TTL上界与共享首cleanup deadline修正，独立复审通过上述限定范围。r2报告期附加运行未单独核稳定源身份的注记原样保留，不冒充稳定r2执行。

持久目录为[outputs/_run/restore-capture-f2-r3-20261028](../../outputs/_run/restore-capture-f2-r3-20261028/copy-manifest.json)：原样复制188个白名单文件、1,654,777bytes，另生成copy-manifest。包含三轮public源码tar／逐项验hash的三文件展开、合成probe／log／JUnit、review／safe／manifest及七份未改公开合同源；**不包含真实F/capture、生产备份、账号或凭据**。没有执行包内代码。

实际Linux waitid／process-group、固定root／hostname／interpreter／socket／OS配置事实、PG25006、真实capture、同窗口审批／pause／恢复及完整新DR仍待独立授权与验收。fresh完整中央full仍在进行，本页不提前填写通过。root另行完成的composer实际CUA／合成后台双路径只验证开发源码，v30未打包、17868仍v29；它不是F2或新包运行证据，也不借旧full或AV Linux结果补齐。未部署、未修改生产数据／资金或App，未commit／push／merge。
