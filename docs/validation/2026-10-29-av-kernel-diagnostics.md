# AV 内核首个失败诊断：本地实现与冻结载荷

本批补齐受限Linux作业首个失败门的安全诊断，并完成本地回归与限定静态复审。root唯一五测试文件组合为 **262 passed／0 failed，0.75秒**，9个公开实现／测试输入前后SHA相同。**尚无新载荷的真实Linux执行、source／GPG或提取结果。** 旧一次授权运行在来源处理前被kernel gate拒绝，未自动重跑；这批本地通过不修改旧失败结论，也不重打v33桌面包。

## 实现与原保护

[Task A](../../outputs/_run/central-av-kernel-diagnostics-20261029/evidence/reports/taskA.md)为原sandbox错误补首个kernel_collect／kernel_validate的stage、gate、reason和各门独立白名单facts。exporter仅接受exact类型和普通字典，值只允许有界整数、布尔或固定枚举；整个结构3072字节以内。未知／恶意／不可编码的诊断整体省略，不将错误正文、str／repr、traceback／cause、路径、环境或原系统文件内容转成输出。原拒绝和收集顺序保持，诊断不能将失败变成接受。

[Task B](../../outputs/_run/central-av-kernel-diagnostics-20261029/evidence/reports/taskB.md)使用固定失败类别，替代可泄露任意子类名的动态异常类别。只有exact SandboxError进入A exporter，helper异常／编码失败／超限回落固定失败记录。最终ASCII JSON连一个换行最多4096字节，失败仍exit1；sandbox仍在签名／来源／工作目录处理之前。

## 测试与独立审阅边界

A最初共享os.stat补丁破坏pytest自身路径处理，保留为[harness错误](../../outputs/_run/central-av-kernel-diagnostics-20261029/evidence/logs/taskA-harness-error.log)，不算行为RED。修正隔离fixture后[genuine RED](../../outputs/_run/central-av-kernel-diagnostics-20261029/evidence/logs/taskA-genuine-red.log)为26failed；[export异常RED](../../outputs/_run/central-av-kernel-diagnostics-20261029/evidence/logs/taskA-export-red.log)记录33passed／1failed，最终[150passed](../../outputs/_run/central-av-kernel-diagnostics-20261029/evidence/logs/taskA-final.log)。B的8项初始RED、39项初步GREEN和47项实际validator→worker CLI合成集成日志均原样保存；这些与[root联合262项](../../outputs/_run/central-av-kernel-diagnostics-20261029/evidence/logs/combined.log)重叠，分别列证，不累加。合成collector seam没有真正读取内核，不据此推断旧线上失败根因。

[联合safe](../../outputs/_run/central-av-kernel-diagnostics-20261029/evidence/validation/combined.safe.json)记录9输入与日志摘要；本归档逐项再次核对公开当前输入与这些pins，但不重新运行测试。[独立审阅](../../outputs/_run/central-av-kernel-diagnostics-20261029/evidence/reviews/independent.md)及[safe](../../outputs/_run/central-av-kernel-diagnostics-20261029/evidence/reviews/independent.safe.json)为限定Spec／Quality PASS，无P1／P2。独审确认原13个helper字节不变、11条validator if守卫保留，worker仅main变更与新增failure_receipt；bootstrap共24行且仅第8行变化。独审只静态检查既有测试证据、AST／literal／base64，不执行或import候选，也不批准远端运行。

## 新载荷与一次运行门

[冻结计划副本](../../outputs/_run/central-av-kernel-diagnostics-20261029/evidence/plan/payload-plan.safe.json)绑定154.217.245.179、`/root/easel-av-source-run-20261029-791399b0dc890cf0`和`easel-av-src-791399b0dc890cf0.service`；资源仍512MiB、200%CPU、16tasks、120秒，无网、无编译／安装／模型／账号／钱包／生产重启。计划冻结字段approved／uploaded／executed均false，原字节保留；后来任何新授权和实际执行必须另有明确记录，不能把本页或独审当批准，旧一次授权不复用。

5个载荷文件合82,321字节，含4份代码与657字节manifest；manifest SHA256 `2f13a72476accacea6408da5b336656f37a81501e6d01a9512b373551a1f688c`。固定bootstrap111,794字节，SHA256 `035655a614be107bd77b2c26147b539e782de1147b889e4b4749d8ff377440e1`。[freeze回执](../../outputs/_run/central-av-kernel-diagnostics-20261029/evidence/validation/freeze-verification.safe.json)记录payload与当前4源码相同、旧r2 bootstrap仅第8行变更；stage／unit／manifest／FILES替换不扩大旧执行许可。这里的5是传输文件数，不是将来提取文件总数。已有[payload manifest](../../outputs/_run/central-av-kernel-diagnostics-20261029/payload/payload-manifest.json)及[bootstrap](../../outputs/_run/central-av-kernel-diagnostics-20261029/fixed-bootstrap.py)仅链接，不重复复制或运行。

## 安全归档与尚未完成

[证据目录](../../outputs/_run/central-av-kernel-diagnostics-20261029/evidence)只复制26个准确白名单文件，含4源码、5测试、计划／manifest、作者和独审、TDD与联合日志、freeze脚本及安全回执；加README／copy-manifest共28个文件，集合闭合。各项源before／copy／源after的字节数与SHA一致。[复制清单](../../outputs/_run/central-av-kernel-diagnostics-20261029/evidence/copy-manifest.json)摘要为`a94b4815dc63948c74f432ad4227d722d81ecdfc620b6e752e0ff2919f8bba5e`。没有扫描或复制整个payload树、真实workspace、备份、账号／钱包／raw内核资料，没有执行freeze脚本、bootstrap、候选或测试，也无网络／SSH／远端动作。

真实kernel facts、签名、完整提取及unit终态／残留／原生产状态仍是独立执行门；之后尚有完整依赖、编译profile、API UID／systemd与供应商／生产验收。本次只做本地文档归档，不修改总执行账、前端、v33 App、资金或配置，不提交／推送／合并。
