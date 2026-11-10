# Linux 媒体族原节点覆盖账本

以下按冻结原22个函数／37个参数节点统计，不把纯函数检查、代码适配、builder成功或附加维护用例计作原节点通过。本段为最新计数；下方过程记录中的“当时”状态不覆盖本段。R4缺少解码工具负例已实际通过；当前原节点17／37、最小权限14＋旧宽fixture3，剩20＝slow1＋AV19。以下过程数字指各历史批次。

| 原范围 | 节点数 | 当前证据 |
|---|---:|---|
| 文件写入 incomplete／fresh／promote | 3 | 实际通过；原R8较宽fixture DML，证明OS／非owner／控制面及原业务断言，不等于正式最小DML |
| scope、wallet-concurrency | 2 | 实际通过；真实产品最小权限及root／app检查，Next2归档 |
| storage-cross-product、storage-global两参数 | 3 | 实际通过；真实最小权限，metadata-only；Storage3归档 |
| GC三个原函数／六参数 | 6 | 实际通过，真实最小权限、PNG物理写入／检查及GC；Root另行核对终态和回执 |
| wallet-independent-pools、wallet-expiry-pending | 2 | 实际通过，正式最小权限；双池本次赢家text，原lot过期结算及账本检查通过 |
| async-cancel慢GET | 1 | 旧fixture与仅恢复业务pool的新fixture均未通过：原227断言GET0／cancel0；原因未定，未放宽期限 |
| audio-unknown中的missing-tools负例 | 1 | R4真实最小权限隔离实测通过：缺工具时零POST／零reserve、OPEN、unknown0／physical0；R2／R3旧失败保留 |
| 其余真实音视频／语音／incho／异步交付 | 19 | 需要完整受审Linux工具与原格式／解码范围，仍开放 |
| **合计** | **37** | **原节点实测通过17；其余20未通过或未验收** |

已通过17节点中，14个使用正式产品最小运行权限，3个仍按原宽fixture权限限定证明范围。不能把后者扩展成已通过所有正式最小角色业务路径。

GC6的六个原节点在reviewed002批次顺序首次通过，均exit0/noOOM、原1800s期限内接受，unknown分别为0／1／1／1／1／1，physical均0。Root只读核对6个实际容器终态及28个固定安全文件，归档30份资料于 `outputs/_run/b2-development-20261030/gc6-reviewed002-root-20261031/`，清单SHA256 `1e7d38e6895a2266ddaa372962b27cfae7ccf028297cea84d036300b0af1a392`。资金／PNG／原nonce事实由已冻结root检查器实际执行，没有新增私有业务逐行快照的复制或独立重读。

Wallet2两项首次顺序通过，分别约7.216s和7.120s，exit0/noOOM、原1800s期限内接受，unknown0／physical0。过期结算真实完成原稳定job许可一次，双池测试的被拒媒体nonce按原规则完成；没有选择赢家重试。Root复核12个固定安全哈希和2个实际容器终态，14份资料归档于 `outputs/_run/b2-development-20261030/wallet2-reviewed001-root-20261031/`，清单SHA256 `ef2b6031454182f9ba7b910a39aba6c93390df6748647973630ab73b8775ebb4`。两项各自boot摘要和原guard均保留，不宣称跨case相同或由此推断主机重启。

SLOWpool新一次未通过，约7.525s退出1、无OOM、未迟到；原assert227仍为GET0／cancel0。未得到post-app资金／permit oracle成功回执，实际unknown／physical／control明确保持null，不能按预期值1／0填充。Root复核14个固定哈希与1个实际失败容器，16份安全资料归档清单SHA256 `491cb190bee60a3f276d90ab8f67f622d7fe8c2c5ef7a6bfe8a6e0142ad987ec`，目录为 `outputs/_run/b2-development-20261030/slow-pool-reviewed001-root-20261031/`。该pool批次结束时，被动阶段诊断尚未运行；后续phase与single-guard结果见下文，不替换此失败结论。

另有2个不在原37节点中的维护来源用例 `maintenance-async`、`maintenance-voice` 实际通过；它们仍属于原宽fixture权限证据，单独记账。原slow失败runtime、数据卷和安全回执保留，没有用新增用例替换原失败；后续仅按准确范围删除9个冗余公共builder，见[清理记录](2026-10-31-media-backup-b2-logto-family.md#本轮后续空间清理的准确范围)。

Root安全归档：Next2 `next2-minimum-actual-20261031/manifest.safe.json`，SHA `7feeb0d83a425dd158289484144862163e3b847a3553e17e904ba9c34df72925`；Storage3 `storage3-reviewed001-root-20261031/manifest.safe.json`，SHA `f7682df1515a34185158b84c21eb976cc5932d23adcc65c436029fc04a5364e0`；附加THREE `three-reviewed002-root-20261031/manifest.safe.json`，SHA `b9a1e20b695a79f65f604dd426372e4c379d9e7a008193f04f4865e9a1a2a30c`。各路径均位于仓库 `outputs/_run/b2-development-20261030/`。

这份账本仅覆盖上述媒体族增量，不代替完整Q1／维护包测试、Logto全事实恢复、T13／T14、B2／B3、真实模型验收或桌面签名发布门槛。


## 截至R6恢复和后续两节点代码准备时的计数边界（历史）

固定合成Logto Linux恢复、whole与fresh B1文件恢复已实际通过并独立复核，记录见[Logto增量](2026-10-31-media-backup-b2-logto-family.md)。这项不属于原媒体37节点，不增加本账通过数。

新的SLOW阶段诊断样本仍在原227行失败：GET0／cancel0，实际expire返回1／claim返回0，未进入execute；root已核固定安全哈希和终态。恢复原单层service guard的新样本也已实际失败，Root归档清单为c20460c2a465c91c2a2c8096ea0f452dc0717927a5d7df042d13acab2b81642f，本轮不再作pool／guard／时钟实验。缺解码器missing_tools的R2／R3实际失败和R4代码状态见下文；该单参数适配不调用codec，原main／helper／零POST与零reserve断言保持，其余三个failure继续待AV。不将上述代码准备或诊断计为通过。

当前总数仍16／37，正式最小权限13、旧宽fixture物理节点3；附加维护2项单独记账，remaining21保持。全family／T13／T14／B2／B3未关闭，正式支付与其他交付门槛不由这份账本替代。


## missing_tools首次实际失败与只读参数修正

AUDIO R2代码候选通过39项pure、6项minimum结构和独立外层异常传播复审后，首次实际环境在seed阶段以ReadOnlySqlTransaction失败，5.522262002s、exit1/noOOM。唯一stdout安全诊断不含frames，不能声称已获得确切SQL调用栈；没有达到app、最小角色或资金oracle成功回执，仍不增加通过数。

随后源码和独立7项source-extracted READONLY proof确认root观察器内的两处API误用：在READ ONLY事务中，IdentityService.validate_scope和credits.authorize默认lock=True会请求FOR UPDATE。新的R3候选只在root观察器这两个调用传lock=False，保留READONLY及所有scope、model、binding、entitlement检查，实际产品业务的默认锁不动。该段为R3当时的CODE-ONLY状态；其后实际失败见下一节，37节点计数未增加。


## R3实际结果与R4代码准备时的epoch契约核对（历史）

R3首次实际运行仍为失败：AssertionError／seed，5.573519836s、exit1/noOOM，actor原时限与fresh boot匹配；root另行只读状态／资源／原wall核对也匹配。没有frames，未取得app、角色、资金或permit成功证明，相关值保持NULL；8份安全归档清单SHA256 `9bb87fd09dadf16123667bc769c38c97f9ea3067d720e666480fdbf1fdb54658`。

进一步独立核对原SQL的静态ABI，发现新的oracle把control epoch与permit epoch混同。Q1／当前6ce源码均初始化OPEN／0，OPEN签发permit＝control＋1，worker审计取control、completed审计取permit。正常fresh seed／app流程不打开window，故应要求control精确0、许可和completed为1、worker审计为0。原verify_seed的>0、terminal的permit＝control都与此矛盾；注册／attest审计还应绑定control epoch。这是源码及9项pure反例的确定结论，不冒充已读取R3实际数据库值。

R4已冻结并完成B独立复审，Root单独授权唯一新隔离运行：修正oracle到原SQL契约；把现有根私有seed checkpoint移到验证前，保留原检查失败；增加有界count／bool诊断和公开函数／行号frame，不记录行、标识符、token、DSN或异常文本；关闭未知元数据发布与诊断失败的默认traceback。生产SQL、真正业务默认锁、Scope、金额和许可均不修改。Root已核132条来源pin、50-record digest及四个runtime文件的完整差异；R4 manifest为0a397c69f4857bf3a76010372066bb300262fa259f2212dd87261a463d2025e8。当前仍16／37；代码冻结和pure GREEN不替代实际验收。

R4独审报告SHA256为`2f6808bdc51ce7340db5b496436ccfd1bd183f90a8565a6c71d51adf2876db4c`，限定代码范围P1／P2为0；完整patch、132pins、50-record digest、27表白名单及适用pure／CLI均已复核。安全归档位于`outputs/_run/b2-development-20261030/audio-r4-code-review-20261031/`，清单SHA256为`0fd191bf13af108f0cac4d6f341016812cf4b04e7969673f3c096dde3e5ab644`。这不替代真实节点、SQL或资金验收。

## R4缺少解码工具负例实际通过

唯一新reviewed004运行自然退出0／noOOM，耗时7.103579920s；Root另行只读同一CID核对资源限制与新原始boot／wall，期限未重置、未迟到。原accept_case一次返回accepted=true，终态回执SHA256为`8f956458b0e918827d7b7ec13b9ce2e8100fad29f5a66dc7064712703e931ea6`。

原main与helper实际执行，缺工具能力关闭在HTTP POST／reserve之前拒绝；原根检查器比较完整资金行、Scope、nonce、原token／invocation行、mint capability及epoch、worker审计前缀与generation关闭，均通过。终态为app_exit0、PG存活、控制OPEN、unknown0、physical0、稳定job完成0、winner=rejected-before-admission。最小权限安全回执178B（SHA256 `c9eb5844e96b0ea8d11042043b4aa724ce4bc5c7c98147946da89240070d1130`）确认原宽权限被拒、最小grant已提交、root assert_safe通过；app实际assert由原root事件校验和退出0证明。Root没有重读私有业务逐行快照。

本节点没有运行codec、生成或解码媒体，没有调用模型；不扩为其他audio分支或19项AV通过。该节点加入原清单后为17／37（14最小权限＋3旧宽fixture），剩20＝慢GET取消1＋AV19；额外维护2与Logto R6另计。完整T13／T14／B2／B3仍OPEN。安全归档目录为`outputs/_run/b2-development-20261030/audio-r4-reviewed004-root-20261031/`。

Root最终归档10份安全记录，清单SHA256 `9d6ff321c323e18385905cc97a66f13da4dbef3345e0d6f32a4526d91ad86a46`；重新核132条候选pin、51条实际stage投影与19份原host安全文件。Root独立Docker GET只包含原资源／终态核对，没有新的stdout、stderr、seed probe或DB逐行查询。原17868首页无cookie只读GET为200／2,196B；默认沙箱首个请求被权限拒绝，授权环境的实际请求成功，不将前者记为服务故障。
