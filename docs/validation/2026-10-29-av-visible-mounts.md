# AV可见挂载图与固定FD观察：本地验证

本批完成可见挂载图、固定五目录FD观察和受控payload依赖。root唯一8测试文件联合 **348 passed／0 failed，0.71秒**，13份实现／测试输入前后SHA相同；[独立静态审阅](../../outputs/_run/central-av-visible-mounts-20261029/evidence/reviews/independent.md)为限定Spec／Quality PASS，无P1／P2。本地结果不构成实际Linux隔离、来源GPG、提取或完整AV交付；旧两次真实失败保持，没有新远端运行或unit授权。

## 实现及安全前提

[纯模型A](../../outputs/_run/central-av-visible-mounts-20261029/evidence/reports/taskA.md)按完整ID／parent图解析可见层，不按行序或ID大小选顶层，不把不同OTHER点合并。全表先验证唯一根、可达性、循环、父路径、同父同点竞争及标志；仅唯一根允许合法self-parent，parent0仍拒绝。隐藏分支也先过图合法性；可见RW后代全部保留，再交原五目标四字段policy，不用五点RO代替全子树检查。

[观察与集成B](../../outputs/_run/central-av-visible-mounts-20261029/evidence/reports/taskB.md)只以O_PATH／O_DIRECTORY／O_NOFOLLOW／O_CLOEXEC打开固定五目录，fstat核目录，限量读取自身数字FD的fdinfo／mnt_id，finally关闭FD；没有O_PATH即拒绝，不派生任意路径或打开来源文件。先比较两份mountinfo文本相同，再解析可见图，并将得到的五个有效mount ID与FD观察值逐一核对；全部匹配后才返回facts。**这是采样一致性和五点交叉验证，不是原子快照、无ABA或整棵子树独立测量。** 仍依赖可信完整mountinfo、私有namespace、能力限制及可信宿主无并发挂载者。

独审确认原runtime validator／root／caps／namespace／资源／写挂载／own-mount及来源首门字节保留。实际collector改走新verified helper；旧纯parser仍在，旧parser测试不冒充新模型真实运行。错误只增加固定check/model_code枚举，不输出路径、fdinfo或mountinfo原文。operator只把mount_table.py加入外层hash必需成员，不改变资源／systemd／重试。

## 失败与最终组合分列

- A最初46项真实RED；唯一self-parent根补充3 failed／54 passed，最终57passed。5项已实现行为的额外验证不宣称各自RED-first。
- B最初26failed／1passed；初次GREEN为25passed／2failed，保留原日志。原因是fixture根parent0，修为合法外部parent999后177项组合通过，没有为fixture放宽实现。
- payload依赖先2failed，加入模型后与原operator合67passed。
- [root联合348项日志](../../outputs/_run/central-av-visible-mounts-20261029/evidence/logs/combined.log)及[safe](../../outputs/_run/central-av-visible-mounts-20261029/evidence/validation/combined.safe.json)收口；上述作者、旧套件和联合范围重叠，不合并总数。归档只核日志／pins，不重跑任何测试。独审也只静态读证据，不import／执行候选或内核探测。

## 冻结载荷及桌面保全

[新冻结核对](../../outputs/_run/central-av-visible-mounts-20261029/evidence/validation/freeze-verification.safe.json)另记录6个嵌入／副本／当前源pins闭合，bootstrap仅第8行变化。[本地复杂度样本](../../outputs/_run/central-av-visible-mounts-20261029/evidence/validation/local-perf.safe.json)只运行一次确定性的4096条合成记录（4095个不相交只读直接siblings），输入232,348字节，wall1.625856625秒／CPU1.624566秒，五目标shape断言通过。它没有真实mountinfo／collector，不是154测量、峰值内存、最坏情况或远端512MiB／120秒预算证明；不并入348项测试数。

[新计划](../../outputs/_run/central-av-visible-mounts-20261029/evidence/plan/payload-plan.safe.json)绑定154.217.245.179、stage「/root/easel-av-source-run-20261029-fadd4a945213cc1e」、unit「easel-av-src-fadd4a945213cc1e.service」；6初始文件93,707字节，即5代码＋807字节manifest，不是后续提取文件总数。manifest SHA「98b7c5cd0fc2fbe5ec7efc776bbfacf3cf556d41ef8be9be8a8ef3cb11b0b0cb」；bootstrap127,016字节／SHA「ae8f1a427836c7fa981177f9a1d865edf1d6346812a6917452a86b7710d09f93」。独审静态闭合嵌入字节、payload副本和五份当前源；24行bootstrap仅第8行参数／FILES变动，其余23行保持。

计划冻结approved／uploaded／executed均false，不修改为通过；本归档和独审均不批准新运行。旧两次一次授权均已消费，不能据旧许可重开unit。资源仍512MiB／200%CPU／16tasks／120秒，真正限制及挂载策略的正向Linux门还须新精确授权与实际回执。

[desktop guard](../../outputs/_run/central-av-visible-mounts-20261029/evidence/validation/desktop-guard.safe.json)记录v33 904应用源与60构建输入逐SHA无差异、前端未改、无预览／账号访问；归档不扩大到App或真实用户目录扫描，也未重打v33。前一[诊断候选](2026-10-29-av-kernel-diagnostics.md)及[实际失败](2026-10-29-av-kernel-diagnostics-linux-run.md)保持原证据，不改判。

## 精确归档与未覆盖门

[本地证据目录](../../outputs/_run/central-av-visible-mounts-20261029/evidence)复制32份精确白名单文件，含5公开源码、8测试、计划／manifest、作者／独审、RED／保留中间失败／GREEN、联合／desktop回执及freeze脚本；加README／copy-manifest共34文件，文件集合闭合，每项源before／copy／after SHA一致。[清单](../../outputs/_run/central-av-visible-mounts-20261029/evidence/copy-manifest.json)SHA为「89dfb966e32bc0a540ef844867727f09b6ae77c4173297a1259eca4c2335e8e5」。已有[payload manifest](../../outputs/_run/central-av-visible-mounts-20261029/payload/payload-manifest.json)与[bootstrap](../../outputs/_run/central-av-visible-mounts-20261029/fixed-bootstrap.py)只链接，不再复制或运行。

root研发的348项测试与一次纯合成性能采样，分别依据既有联合日志／safe和perf安全回执记录；公开文档查询属于研发活动。本归档过程仅复制指定材料并核对摘要，未读取真实kernel／账号／钱包／workspace raw，未访问网络／SSH，也未执行候选、测试或脚本；未修改总账、产品、前端或包，未提交／推送／合并。真实FD／namespace／挂载及资源验证、GPG和完整提取仍未完成；完整依赖、编译profile、API UID／供应商及生产仍是后续独立门。
