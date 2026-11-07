# F2 原生 ARM64 验证：V2 原八场景通过，保留初次失败记录

最新结果：V2初始化证明修复后，在全新容器实际执行原始八场景，全部通过，完整主机执行器accepted=true／exit0／noOOM，且运行前后证明、完整三帧和全部终态围栏通过。原三份载荷和八项断言未修改。见[实际V2报告](../../outputs/_run/f2-native-init-alias-20261102/ACTUAL-V2-REPORT.md)及[实际摘要](../../outputs/_run/f2-native-init-alias-20261102/runtime-v2-safe/actual-eight-summary.safe.json)。以下初次失败及分阶段诊断为历史证据，保留原判断，不代表当前V2结果。

新接入包保持在 `codex/ui-redesign`。Root 在暂停后恢复，重新核对14文件闭集、代码、profile及未存在的运行目录；独立报告 `6388967e…` 复核固定接入差异无阻断，Root完整阅读。源码 `b6a16d30…`、profile `c44ee9d7…`、recipe `bf2439c9…`、expected `610fff3c…`，全部与18项纯逻辑回归的候选相同。原旧执行器不变。

Root仅复制14份精确公开输入177029B到新的私有操作目录，绑定原守护日志勘误 `c428438e…`，记录一次实际准入。实际命令是 `rtk proxy python3 -B /private/tmp/easel-f2-native-manifest-once-code-20261102-reviewed001/once_manifest.py`，执行句柄61485最终exit2。没有重跑此命令。

实际容器 `b1a347d41eb5329a15b9f29526785023075918604363731f0155a9feada18069`：

- 固定API1.49镜像GET经过原始manifest/config/index验证，M镜像引用与C配置关系通过。
- CREATE后完整配置与从未启动检查通过；实际容器的Image、Config.Image均为M，ImageManifestDescriptor为M／1754／OCI／linux-arm64-v8，后续同CID只读复核也通过。这关闭该具体镜像／容器表示的实际未知项，不关闭运行时。
- 守护在唯一START之前就位；原资源限制1CPU／1536MiB、无网络、只读根、cap-drop ALL／NNP、私有PID与cgroup、两个原tmpfs均保持。
- StartedAt `2026-11-02T05:20:18.972902253Z`，FinishedAt `2026-11-02T05:20:19.111027669Z`，约0.138秒，exit1／noOOM／restart0。完整主机执行器正确拒绝成功；没有日志受理或acceptance回执。

对同一已退出容器另做一次末40行只读诊断，结果只有37B stdout、0B stderr，无堆栈。Root未保存或打印原始日志，通过精确哈希与原公开bootstrap常量匹配，确定为 `carrier_error=execution_failed`。无原始carrier header，失败属于其前置路径（运行时证明、解码、落盘或header构造），具体检查项尚不明，不能据此猜测PID1或修改白名单。原八场景结果仍未取得。

守护PID已不存在，wall-terminal回执存在、无kill意图；Root没有发送清理信号或重启容器。保留失败容器、私有stage及原始watch日志。仅9份逐名、键词和标量形状验证后的安全回执6424B被复制，manifest `502f1d7c…`；原始watch日志未读、未导出。

证据：[本批目录](../../outputs/_run/f2-native-once-manifest-20261102/)、[脱敏启动分类](../../outputs/_run/f2-native-once-manifest-20261102/startup-classification.safe.json)、[新鲜终态配置](../../outputs/_run/f2-native-once-manifest-20261102/fresh-terminal-representation.safe.json)。下一步是先补充无私有信息的前置阶段诊断，再依据实际失败检查项修复，不能以退出0、诊断DTO或合成八场景替代原严格验收。

本次没有真实账号、模型、密钥、钱包、支付或远程服务操作，未改变前端文案、原17868、原工作区、分支或交付包。PG/sourcefacts/FWPD、媒体／B2／B3／AV、签名／净机、旁白关联及完整发行范围仍保持各自未完成状态。

## 前置阶段诊断候选：已复核，尚未实际运行

R1/R2保留为未完成的历史候选，不得用于实际运行。R3纯程序生成器、严格报告解析、有界压缩传输和脱敏逻辑已实现；原始运行时源码保持固定SHA `e0687150…`，生成程序使用非main命名空间，按原序执行capture／decode／materialize／header并在原runner前停止。报告始终original_cases_executed=false、approved=false、actions=[]；退出0也仅表示诊断报告完成。

Root完整阅读所有小源码／测试／作者校验器，实际运行9项纯测试0.053秒通过。Root在内存中按原真实payload重新生成program/bootstrap，与固定fixture逐字节相等；重复Args和Config.Cmd共93629字节，低于114688上限。独立有限复核 `abd8d84c…` 无阻断，明确没有重复测试或实际运行。随后Root另补6项检查0.038秒通过：有效源码后的坏nonce、错误digest／长度、压缩输入上限、原源码字面量／非main命名空间、最终原函数调用顺序、最终异常分支使用同一有界脱敏器。未重复或累计宣称全覆盖。

候选及证据在 `outputs/_run/f2-native-preheader-diag-20261102/driver-r3-codeonly/`、Root九文件清单 `root-driver-r3-manifest.safe.json` 和 `root_additional_tests.py`。R3源码SHA `0b8b4f80c9b449c590901608997130805a7be632fc660c139530a83cf45bd195`，清单SHA `dbce323dbd7d62ceb957c12f5a1396a68e0ed9b981b3f1e39e74e1b8175f26c6`。尚无主机执行器集成、新profile／nonce封存、实际准入或新的诊断容器，不能据纯程序或DTO推导原八场景通过。

## 后续实际诊断：已定位到 PID 1 路径检查

本节更新上述候选状态，保留原失败记录。独立主机集成封存16文件、183018B，清单SHA `5c5ecbce66e53c70129f7c8183f88dd6a0b52b4e3057dac9421cdec6f96f68ef`；18项主机纯回归通过，独立有限复核无阻断。Root单独准入后只运行一次新诊断命令，句柄10634最终exit0；未重启原失败容器，未运行原八场景。

新容器 `e2e52ed1292607a6f8b1ba3dc742903f9aa52bf586f96aaf8fd0ea9112d4d19f` 于 `2026-11-02T05:54:45.231912751Z` 启动，`2026-11-02T05:54:45.364517209Z` 退出，exit0／noOOM。原固定镜像、完整安全参数、期限及输出终态围栏保持。报告严格解析后定位：`capture_runtime_proof:199 → init_prefix:153 → require:29`，`ValueError`，即原 `executable in INIT_PATHS` 检查拒绝。退出0只证明诊断完成；`original_eight_complete=false`。

历史AMD64探针的21字节路径SHA与公开常量 `/usr/sbin/docker-init` 精确相等，但**本次原生诊断未记录实际目标路径或文件身份**。因此这只是路径别名假设的历史支持，不能据此扩充白名单、改写原始观测值或宣称当前别名已验证。下一步需要证明当前原生 PID 1 的目标和别名文件身份，再设计保留同等约束的最小修复。

Root随后只读核对守护PID89674已不存在，不发送信号、不清理失败资源。原始watch日志保持私有、未读未导出。新归档仅含严格解析的291B公开报告、精确终态和显式重建摘要，加清单共4文件；清单SHA `8b7294cf02fd029ec9a71406bbccccb3b410427aef6041cc1fa4fb1e540ed90e`。见[诊断摘要](../../outputs/_run/f2-native-preheader-diag-20261102/runtime-safe/actual-summary.safe.json)。

## 后续原生身份探针：路径别名根因已确认

新固定路径探针经31项纯／模拟测试和独立复审，在新隔离容器实际运行一次，句柄49356最终exit0。当前观察确认 `/proc/1/exe` 的21字节目标SHA与 `/usr/sbin/docker-init` 精确相同，argv0为 `/sbin/docker-init`；两个固定别名均指向同一只读、root所有、安全模式的初始化文件，二进制SHA／601296B／ELF aarch64也已取得。实际身份从假设变为已观察；当前具体根因是规范文件路径和argv0不同，而原前置代码要求它们相同。

完整证据、pin-to-import修复及依赖边界见[身份诊断报告](../../outputs/_run/f2-native-init-alias-20261102/REPORT.md)。4文件安全归档清单 `1db6096739985801110615f05c724650c75cfbdf3ec139d1652cbb774701a26e`，不含原始守护日志。该探针不运行原八场景。下一步V2前置修复分别证明实际路径和实际argv0，绑定别名文件身份并保留所有非init检查，再验证原始八场景。
