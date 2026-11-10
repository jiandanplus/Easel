# ROOT 可写子挂载分类：实际失败接续与本地实施账

继续完整剩余交付目标；上一轮为progress：新可见挂载图和固定FD已在获准Linux作业中越过collector，但原ROOT可写子挂载策略拒绝。当前仍在codex/ui-redesign，不改前端、账号、钱包、模型配置或生产，不提交／推送／合并。

## 已取得的实际证据

六文件93707字节的精确载荷获用户单独一次批准后，仅运行 `easel-av-src-fadd4a945213cc1e.service` 一次，SSH终态exit1，无观察超时或重试。worker诊断为kernel_validate/mounts/mismatch，ROOT read_only=true、own_mount=true、writable_count9。按冻结producer与validator顺序，可确认先前身份、NoNewPrivs、能力集、netns、cgroup与资源比较已在该样本通过；ROOT至少一条可见RW后代超出现有允许子树。9是所有可见RW后代的总数，实际违规数／类别／路径仍unknown。不能把后续code／input／cgroup2／work700／权限探测一并判过。

work空，未创建成功结果、GPG或提取记录；unit已collected、PID0、cgroup absent。not-found默认Result=success不能覆盖实际失败。指定4服务、3容器含启动时间、3份公开输入及6payload pin前后同，仅采样保全，不是完整业务或财务审计。[实际运行报告](../../validation/2026-10-29-av-visible-mounts-linux-run.md)保留独立复核与精确证据。执行增量14文件闭集manifest为 `a2e229872ee7e023a678c32300297ab281a11073a73c7e6db07c6e7c3eb27019`，原34文件本地归档保持。

## 本地最小增强

只在原ROOT allowlist条件已经决定拒绝后，对已经取得且验证规范的children做纯分类。facts.writable_summary恰有17固定键：total、allowed、forbidden和WORK/TMP/VAR_TMP/DEV/PROC/CODE/INPUTS/USR/SYS/RUN/HOME/ROOT_HOME/VAR_OTHER/OTHER。按路径组件匹配，VAR_TMP先于VAR_OTHER；不同OTHER路径分别计数，不输出路径或basename。

每值必须精确非负int且<=65535，排除bool／对象／子类；总数必须等于writable_count、14分类和及allowed+forbidden，允许／禁止各自求和一致；stage/gate/target严格限定。未知键、坏总和、恶意dict或target整体省略诊断，仍保持原失败。导出为独立嵌套副本，原3072／4096字节上限保留。原18个if、允许范围、builder、collector、图模型、系统读取和接受决定未改。

作者20项真实RED/GREEN及恶意target比较修复保留；原测试误放根tests目录后已原字节移至指定services路径，历史日志命令原样保留。root首个联合调用因文件路径不存在在测试前退出，不是行为RED；纠正位置后唯一10测试文件联合 **370 passed in0.74s**，15输入前后SHA同。另有实际validator→worker CLI的2项合成联动，连同旧worker／archive49pass，验证不处理来源、单行大小和不泄露合成marker；重叠计数不累加。

独立限定Spec／Quality PASS，无P1／P2，报告SHA `ef138ad9e4b4a6ab9d11fdb6d6816453cc4af79284de9f6b82b4550f94a5c174`。移除新增metadata块可恢复原validator完整AST。合成9项的8允许1禁止或7允许2禁止都不是该次远端真实分类。

## 官方接口与后续边界

通过Context7解析systemd并查询v255，具体选项另核[255原始手册](https://www.freedesktop.org/software/systemd/man/255/systemd.exec.html)：API／运行时挂载有专门规则，文档对运行时目录的写权限有额外说明。它不能替代本次具体违规类别的实证，不据此盲改ReadOnlyPaths、TemporaryFileSystem或放宽allowlist。先取得分类，后按证据设计最小收紧／私有化方案并单独审阅。

新冻结目录 `outputs/_run/central-av-root-writable-diagnostics-20261029`，5代码＋807字节manifest，共6文件96543字节；manifest `cd34f175efef580449139eb8ac483d844525e581076c22b7e7897b28b6b84a5c`。bootstrap130796字节，SHA `72c7f98eb302241e9f6fe3870e42c61f5ce1f412da5c5570dcfa43088e652b18`，模板仅第8行固定参数／公开bytes变化，其余23行不变，嵌入／副本／源pin闭合。新stage `/root/easel-av-source-run-20261029-1adc617e79ddf853`、unit `easel-av-src-1adc617e79ddf853.service`已具体发起新的一次运行确认；写入时未获回复、未上传或执行，旧三个一次批准均已用完。

[本地验证文档](../../validation/2026-10-29-av-root-writable-diagnostics.md)与32文件闭集manifest `2f1257d1b39777e27f72e263bd9324f7e5ed489ff9624aadac8935c2c7882b7b`记录当前候选。v33的904应用源和60构建输入逐SHA仍相同；不重打包、不查账号或新增模型调用。ROOT权限问题、完整隔离、GPG／提取／构建、供应商／生产与完整总目标其他门均未因计数增强而完成。
