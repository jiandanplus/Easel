# AV ROOT可写分类诊断：本地候选验证

本批补充原ROOT白名单拒绝时的安全分类计数；**没有修复ROOT权限、放宽allowlist或取得新真实Linux通过。** root修正测试路径后的唯一10文件联合为 **370 passed／0 failed，0.74秒**，15份实现／测试输入前后SHA同；[独审](../../outputs/_run/central-av-root-writable-diagnostics-20261029/evidence/reviews/independent.md)限定Spec／Quality PASS，无P1／P2。前三次真实一次授权均已消费，本地通过不产生第四次许可。

## 改动只在拒绝后的metadata

[Task A](../../outputs/_run/central-av-root-writable-diagnostics-20261029/evidence/reports/taskA.md)从已经验证的内存children派生writable_summary，且只在原ROOT allowlist条件已经决定拒绝之后执行。分类异常被捕获以保留原SandboxError，不创建成功结果，不增加系统读取。[范围safe](../../outputs/_run/central-av-root-writable-diagnostics-20261029/evidence/validation/taskA-scope.safe.json)和独审确认：去掉一个新增metadata try块即可恢复旧validator完整AST，原18个if及接受决定保留；builder、collector、固定FD观察、可见图、资源和挂载配置未改。

summary精确17键：total／allowed／forbidden加14固定类别，前五类WORK/TMP/VAR_TMP/DEV/PROC对应旧允许规则，其余九类仅统计。每值exact int0..65535，排除bool／子类／任意对象；total必须等于writable_count、14类和及allowed+forbidden，allowed／forbidden分别等于两组和。target必须精确ROOT、stage/gate正确；未知／缺失／畸形／不一致嵌套使整个诊断省略。返回detached dict，不输出真实路径、basename、原始记录或任意异常文本；3072字节诊断及完整ASCII行含LF4096字节门保持。

[Task B](../../outputs/_run/central-av-root-writable-diagnostics-20261029/evidence/reports/taskB.md)使用实际validator→worker CLI的合成联测核对正确分类、来源seam不进入、单行长度和无私有marker；畸形summary仍为原失败、整份诊断省略。worker源码及sandbox来源首门未改。

## RED、路径错误与最终回归

A最初4failed／15passed；新增恶意target比较先1failed／19passed，再修类型守卫，最终20新项连原guard／diagnostic为170passed。保留[初始RED](../../outputs/_run/central-av-root-writable-diagnostics-20261029/evidence/logs/taskA-red.log)、[hostile RED](../../outputs/_run/central-av-root-writable-diagnostics-20261029/evidence/logs/taskA-hostile-red.log)与[最终日志](../../outputs/_run/central-av-root-writable-diagnostics-20261029/evidence/logs/taskA-final.log)。两项新worker联动是实现后验证，不宣称均RED-first；与既有worker／archive合[49passed](../../outputs/_run/central-av-root-writable-diagnostics-20261029/evidence/logs/worker-integration.log)。

A的20项测试曾创建并运行于根tests/，随后原字节移到services/platform/tests/test_av_root_writable_diagnostics.py，SHA仍fb69e3d1af7e513ce097cf362a2af984f32c531727009fdfe678a163c679283d。历史报告和日志保留真实原路径，未改成从未执行的命令。root首次指定services路径的调用在任何测试执行前因文件不存在退出，属于路径／收集准备错误，不是行为RED。修正后[root唯一370项日志](../../outputs/_run/central-av-root-writable-diagnostics-20261029/evidence/logs/combined.log)及[safe](../../outputs/_run/central-av-root-writable-diagnostics-20261029/evidence/validation/combined.safe.json)收口；各作者170／49与370重叠，分别列证，不相加。

合成9项得到8允许1禁止与7允许2禁止是两个不同fixture，**此前真实9项仍无分类结果**，不能把测试中的SYS/RUN等类别当主机判断。公开systemd文档的一般语义也不证明实际bad类别，因此没有盲改ReadOnlyPaths／TemporaryFileSystem或layout。

## 新冻结载荷与桌面保全

[计划](../../outputs/_run/central-av-root-writable-diagnostics-20261029/evidence/plan/payload-plan.safe.json)绑定154.217.245.179、stage「/root/easel-av-source-run-20261029-1adc617e79ddf853」、unit「easel-av-src-1adc617e79ddf853.service」。6初始文件96,543字节＝5代码＋807字节manifest，不是后续提取文件数。manifest SHA「cd34f175efef580449139eb8ac483d844525e581076c22b7e7897b28b6b84a5c」，bootstrap130,796字节／SHA「72c7f98eb302241e9f6fe3870e42c61f5ce1f412da5c5570dcfa43088e652b18」。[冻结核对](../../outputs/_run/central-av-root-writable-diagnostics-20261029/evidence/validation/freeze-verification.safe.json)及独审静态闭合嵌入／payload／当前五源pins；24行bootstrap仅第8行参数／FILES变，其余23行保持。资源仍512MiB／200%CPU／16tasks／120秒，无配置放宽。

计划冻结approved／uploaded／executed均false，保持原字节；本地审阅与归档不授予第四次运行或复用旧unit。[desktop guard](../../outputs/_run/central-av-root-writable-diagnostics-20261029/evidence/validation/desktop-guard.safe.json)记录v33 904应用源、60构建输入无差异，无预览／账号／工作区访问；归档不重新扫描App或重打包。旧[visible候选](2026-10-29-av-visible-mounts.md)及[实际失败](2026-10-29-av-visible-mounts-linux-run.md)保持，不能升级为新分类实证。

## 精确归档与未完成门

[本地目录](../../outputs/_run/central-av-root-writable-diagnostics-20261029/evidence)30份精确白名单副本，含5源码、10测试、计划／manifest／freeze、作者／独审、RED／GREEN及范围／desktop回执；加README／copy-manifest共32文件，集合闭合，各项源before／copy／after SHA相同。[清单](../../outputs/_run/central-av-root-writable-diagnostics-20261029/evidence/copy-manifest.json)SHA「2f1257d1b39777e27f72e263bd9324f7e5ed489ff9624aadac8935c2c7882b7b」。已有[payload manifest](../../outputs/_run/central-av-root-writable-diagnostics-20261029/payload/payload-manifest.json)和[bootstrap](../../outputs/_run/central-av-root-writable-diagnostics-20261029/fixed-bootstrap.py)仅链接，不重复复制或运行。

root本轮研发确有此前已获准的第三次SSH、只读前后采样、公开文档查询和本地测试，分别由既有记录说明。**本归档过程**仅复制指定材料、静态阅读报告／核摘要，不执行候选、生成器、测试或网络／SSH，不读真实kernel／账号／钱包／业务workspace raw；不改总账、旧归档、代码、前端或v33包，不提交／推送／合并。新统计尚无真实样本；ROOT权限、完整Linux后续策略、GPG／提取、依赖／编译profile、API UID／供应商及生产仍未完成。
