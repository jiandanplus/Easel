# AV 执行控制器代码与隔离测试验收

2026-11-03，当前分支 codex/ui-redesign。本项仅关闭控制器／guardian 的代码和纯测试阶段；没有运行真实 Docker worker、原生 guardian、真实源包解码或重新打包。

完整控制器已接通：固定源码及配置校验、单次创建和启动、精确容器身份、原始同 boot 截止时间、guardian 就绪与回收、报告校验、完整产物读取与私有落盘、落盘后状态及文件身份复核、限量输出。主 API 的七个固定操作保持原有范围。

独立审查首轮发现六项问题，已在一轮修复中关闭：输出阶段继续核对最初文件身份；拒绝 running 回退为 created；模拟依赖标记在 guardian 和 controller 中严格单向传播；修正实际未到达落盘阶段的 boot 故障测试；停止请求必须先取得成功持久化的一次性声明；独立文件描述符在关闭异常时仍逐一尝试释放，关闭结果不明确时不盲目重试。另补 START 前已拥有 guardian 的失败路径：写入绑定 abort 并尝试限时回收。

停止声明已有、不可写或持久化完成确认中断时，不再发送 KILL，资源状态继续未知，返回非成功结果并保留记录。这不能被解释成停止成功，也不会产生新的重试权限。

验证采用真实受保护 worker 的 FakeMain 产物与实际报告／产物校验器，控制器进程和 API 为模拟依赖；guardian HTTP 层另接模拟 socket。所有真实 socket、进程、线程、计时器、FFI、fork、系统资源控制和容量查询均有外部调用计数防护。测试没有调用供应商、修改钱包、访问生产服务或操作 Docker。

Root 完整重读 583 行源码、532 行测试、报告和全部修复 diff，独立复跑：

`rtk proxy services/platform/.venv/bin/python -I -B -m pytest --noconftest services/platform/tests/test_av_pack_executor.py -q`

结果 **69 passed，21.99 秒，exit 0**。独立规格与质量复审均 PASS，结论限于当前代码和纯测试边界。Root 还核对了 14 个受保护文件字节未变、两份新文件哈希、完整新增／修复 diff、30 份首轮留存材料及原报告前缀。原有失败和测试记录均保留。

冻结 SHA256：

- pack_executor.py：1f7dbf32b5dfe7c7e111970113d89d7221e1cf2b5b09727d79807e87bca13088
- test_av_pack_executor.py：8f7c58efd0bd16bc7ebc692c520daf3422e9ea48f2025648a0cc20ad2a917b7d

后续还需单独验证固定宿主 Python 解释器、真实 Darwin ABI、原生 guardian 调度及停止、worker AS／CPU／wall 限制、接近 16 MiB 的完整产物保留、真实源包与身份链。VM／日志容量仍未证明；就绪探针草案未冻结，不会以同父目录路径或 Mac 空闲空间替代文件系统容量证明。完整 FFmpeg 构建、AV19、B2／B3、F2、灾难恢复、签名／公证及最终发行仍未因此完成。

本轮没有新增前端说明文字，没有改变原 17868 的登录／历史／自定义接口配置，没有 commit、push 或合并主分支。按用户选择拆分的排队过期／在途取消结果见 2026-11-02-media-cancellation-functional.md；原 200ms 历史失败仍保留。

[Root 复验记录](../../outputs/_run/av-pack-execution-20261103/ROOT-TASK2B3B-R2-PURE.safe.json)；[独立复审](../../outputs/_run/av-pack-execution-20261103/TASK2B3B-REVIEW-r2.safe.md)；[完整实施及修复报告](../../outputs/_run/av-pack-execution-20261103/task-2b3b-report.md)。
