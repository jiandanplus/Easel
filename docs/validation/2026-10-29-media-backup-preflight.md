# 媒体日常备份：缺口复现、定时器绑定与设计复审

**本轮完成调查及设计复审，没有实现或部署新的媒体备份。** 当前分支仍为 `codex/ui-redesign`；不改变前端、账号、历史、自定义接口、钱包或现有定时器。本记录与 AV 的一次性运行分别计证，不互相授予权限。

时序说明：本页保留设计批准前的预检状态。用户随后明确批准路线 A／B1，开发与最终验证另见 [B1 交付记录](2026-10-29-media-backup-b1-delivery.md)；下文“待确认／零实现”均指本次预检收口时，不覆盖后续批准与开发。

## 当前可确认的事实

已有空媒体、两成品及 mixed 恢复工具，不是零实现；其文件清单、大小、SHA、权限、同一中央 snapshot 的 dump／registry 绑定已有代码。F1／F2 合同和只读 producer 也已实现，实际 Linux／PG／WPD／新 mixed DR 仍待验。旧真实 mixed DR 的最后 source 复核失败仍保留，不据本轮设计或纯文件测试改成成功。

两位审阅者分别完成[文件生命周期调查](../../outputs/_run/media-backup-preflight-20261029/evidence/reviews/lifecycle.md)及[备份缺口审查](../../outputs/_run/media-backup-preflight-20261029/evidence/reviews/backup-gap.md)。当前共享私有目录以随机 hex key 平铺，租户／产品授权由数据库维持；写入、attach 和 GC／unlink 并非一个原子步骤。单独导出数据库 snapshot、关闭模型或取得现有 advisory lock，不能独立证明整个捕获窗口没有文件变化。

## 合成数据复现及既有回归

本轮在未修改的 `verify_mixed_media_restore_drill.py` 上运行两个纯文件用例组：`test_exact_missing_staged_allowlist_is_absence_and_restores_no_placeholder` 与 `test_any_extra_missing_or_unknown_file_and_mismatched_allowlist_reject`。实际结果为 **10 passed in 0.04s**；[原始日志](../../outputs/_run/media-backup-preflight-20261029/evidence/local/baseline.log)保留。没有运行数据库、Docker、模型或网络测试。

[容量观察](../../outputs/_run/media-backup-preflight-20261029/evidence/local/capacity-observation.safe.json)使用全新自有合成目录：一个逻辑大小 33 MiB（34,603,008 字节）、实际分配为 0 字节的稀疏文件，可通过现有单 blob 64 MiB 元数据上限，却被 32 MiB 聚合上限拒绝，文件内容 `os.read` 次数为 0。既有源码 SHA `34df13489773a3e3bf8c28601e634546d494012923dde54b0bd76e792581271e` 前后未变。该结果证明固定演练的容量边界；不是生产备份失败、真实媒体解码或新大媒体恢复通过。合成文件未删除，归档不包含其大文件本体。

## 当前服务器的限定只读观察

仅检查指定主机 `154.217.245.179` 上三个固定文件的摘要、权限及指定服务／定时器状态，没有读取真实 env、数据库、密钥、媒体或已有备份归档，没有触发备份、创建远端文件或改变服务。

[首轮元数据](../../outputs/_run/media-backup-preflight-20261029/evidence/remote/metadata-r1.safe.json)在 UTC 08:47:41 返回：脚本 15,087 字节、SHA `d330bccf0c71f9c4d808411ba48d87c217048dde0763023e4e9ca0cb45bb14a1`，timer 为 active／waiting；但其严格命令匹配为 false。该结果原样保留，不能单独作为命令绑定成功证据。

[后续只读绑定观察](../../outputs/_run/media-backup-preflight-20261029/evidence/remote/binding-r2.safe.json)在 UTC 08:54:00 返回：命令有三个参数，Python basename 为 `python3`，包含识别的 `-B` flag，且只有固定脚本参数。unit 字面命令与 effective ExecStart 相符，无执行 hooks、drop-in 或待 daemon reload；timer 指向该指定 service。首轮 false 来自不接受 `-B` 的观察器，不是服务变更；两轮脚本及 unit 摘要相同。第二次 SSH 已观察到终态 exit 0，未重跑任何 AV 或备份任务。

这只证明**该命名定时器引用已审阅旧脚本**。旧脚本的 `collect` 收集两库 dump／roles 及固定配置／公开文件，没有 media registry／media.json／blob 遍历；`archive`／`main` 使用整体内存载荷和自校验。其[静态审阅收据](../../outputs/_run/media-backup-preflight-20261029/evidence/local/legacy-source-review.safe.json)与服务器脚本 SHA 绑定。没有核验解释器二进制、进程环境或其他备份作业，也没有解密任何现有归档；因此不能外推“服务器没有任何其他媒体备份”，更不能宣称当前完整恢复能力已经通过。

## 新设计及审阅边界

[已冻结设计 R2](../../outputs/_run/media-backup-preflight-20261029/evidence/specs/design-r2.md) SHA `f0df55e383e815078e96b926c02322f3dcb617b68df8d332ef3d0ae76c0d5814`。推荐路线 A：B1 先做本地分块归档、文件清单闭包和私有目录恢复；B2 再完成整个捕获窗口的 write／GC 屏障及有界认证加密；B3 完成真实数据库／媒体 fresh restore、源后验和具体定时部署验收。

R1 的三项 P2 已在 R2 明确关闭：整个窗口阻止新 GC 并排空已领租约；deleted／purged 墓碑保留且物理 key／part 必须缺失；32 GiB 预算覆盖全部物理 header／padding／终止尾，按唯一长度读取并有界检查 EOF。原 R1、原发现和两份 R2 复审（[A](../../outputs/_run/media-backup-preflight-20261029/evidence/reviews/spec-a-r2.md)、[B](../../outputs/_run/media-backup-preflight-20261029/evidence/reviews/spec-b-r2.md)）全部保留。两位审阅者限定设计 PASS、没有新 P1／P2，未将文字审查称为实现或生产通过。

[当前设计](../superpowers/specs/2026-10-29-media-backup-closure-design.md) SHA `fd210395d8bf870e67b185d8eeb56997f5d13f97bfe1825ad70e6bd64bc438bb`。归档原 R2 后，仅将歧义句“本次请求仅批准”更正为“本次待用户确认”，并追加版本说明，没有技术设计变化。root 精确字符串变换核对成功；原 review 仍只绑定冻结 R2 字节，没有冒称当前修订经历了新一轮独立复审。

B1 只会接受受信捕获器提供的固定逻辑名／已打开文件描述符，不接受任意宿主路径；只在合成资料上工作，不提供生产 CLI 或修改 timer。新功能设计已提交用户确认，尚未得到该设计的新批准，尚无新备份实现或测试。不能将 AV 第三次或第四次载荷批准、目标自动继续、独立 review PASS 视为此设计许可。

现有 macOS v33 交付保持；本轮不因中央运维调查重新打包桌面，不发送真实模型请求，不修改点数或旧 pending。完整 Linux 隔离、AV 来源／编译／供应商、F2／WPD／成功 mixed DR、日常媒体备份、永久工作区接续、macOS 签名／公证／净机等门仍分别待验。

## 本地归档与保全

[证据目录](../../outputs/_run/media-backup-preflight-20261029/evidence/README.md)收纳 16 份固定副本，共 79,233 字节，加 README／manifest 共 18 文件、106,908 字节。清单 SHA `613918cad27852ed47e71ad112186685988b57fdbebfbc6f75053eda6a4352b0`。复制前后 stat／SHA、目标 SHA 一致；目录 0700、文件 0600；root 独立复核闭集、大小／SHA 及 15 份仍保持的当前源，R2 设计作为历史快照保留，见[复核收据](../../outputs/_run/media-backup-preflight-20261029/root-verification.safe.json)。没有复制合成大文件本体、真实备份、配置或密钥。

[桌面与中央源码保全](../../outputs/_run/media-backup-preflight-20261029/source-preservation.safe.json)重新核对 v33 的 904 份来源、60 个构建输入及四份指定中央工具源，差异均空；没有访问用户 preview 账号／工作区。第四次 AV 冻结计划仍 approved／uploaded／executed 全 false，独立授权／attempt 文件不存在。本轮没有新真实模型或资金动作，也未提交、推送或合并。
