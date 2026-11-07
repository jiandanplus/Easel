# AV 完整解码计量 r1 定向验收（2026-10-28）

结论：批准的四文件实现已完成并冻结，focused green；等待独立 review 与 root fresh full。未将旧 e7af wheel、1895/14 或1794全量证据重新标为当前候选通过。

## 改动和不变合同

新增 `media/decoded_timeline.py`，关闭式解析 framehash v2 的 PCM/rawvideo 单流输出。音频以实际size/(2×channels)核对样本与timebase；视频使用passthrough+demux时间基，包含最后一帧正duration，严格连续PTS。只保存定长头部、半行和计数，不保存所有帧。记录/总字节预算从已支持的900000ms及采样率/timebase推导，移除了“整段128KiB”对于逐帧文本的错误限制，保留carry与行/头部硬界、原CPU/wall/AS/FD/FS限制。

`av_artifacts.py` 固定命令切换到framehash流式执行，Fraction精确max核对后一次floor毫秒。`-noautorotate`使验证保持原probe的编码尺寸，静态display-matrix不改变owned原bytes或public几何。AAC完整解码512/320ms按原basis保留；不以probe/editheader剪裁为500/300。

流式负例还发现：stdout积压时kill后直接wait可能不回收；创建进程后handle返回前取消可能遗留子进程。现在有界排空已终止进程的输出并wait，取消期间继续持有spawn handle直至kill/reap。真实子进程测试在失败/取消后用PID存在性验证已回收，不以mock断言替代。

公共metadata字段、原100ms声明一致性、format/protocol/drefs、原字节hash、价格/meter/basis/provider facts、钱包/unknown恢复语义不变。SQL001–020逐SHA匹配原431冻结源；没有数据库迁移或数据修改。

## 红绿证据

| 运行 | 结果 | 范围 |
|---|---|---|
| repo初始红，62617 | 58failed | 新解析/执行接口不存在与旧384ms行为拒绝合法500ms |
| 真实Ubuntu6.1旧源码，92700 | exit1，500→384ms、800→750ms | 冻结旧模块SHA112547…+真实私有ELF；仅测试loader桥 |
| 超量stdout清理红，28196 | 1failed，2.11s | kill后wait被未排空管道卡住；修后正常固定错误并回收PID |
| spawn取消红→绿 | 原迟到子进程仍在，修后绿 | 保留handle并完成reap |
| rotation行为红→绿 | 合法32×16/rotate90原误拒；修后500ms/32×16 | 初次夹具metadata写法未产生矩阵的fixture失败另保留，不当作业务红 |
| 最终本机，15089 | **95passed / 0failed / 0error / 0skip，7.57s** | 真实AV、真实900秒音频/视频、协议/范围/资源/取消/无残留PID；JUnit已保存 |
| Ubuntu6.1候选，77201 | **14正例 / 3负例，exit0** | 真实CFR/VFR/B-frame/单帧/分数fps/AAC/Opus/rotation/900秒音视频；两源SHA与候选同 |

首个绿色尝试在超量stdout用例处卡住后被终止exit130，日志保留；之后明确红测复现并修复，不把它计成通过。此前研究生成失败与9174 SCP失败也未覆盖或清理。

## 四文件冻结

- `services/platform/src/platform_service/media/av_artifacts.py`：`d90d14225f919cb792104ea68a9062e6a610348fda029ea40768428c4bce9949`
- `services/platform/src/platform_service/media/decoded_timeline.py`：`db95081705f6528e08224c4367db488c82735f41a4b40b25162e0dfe48ec2cea`
- `services/platform/tests/test_media_av_artifacts.py`：`a8ceda0cbd9e0aeb1ac9f5180d0895d68cfee737e83a51776df015ded9602957`
- `services/platform/tests/test_media_decoded_timeline.py`：`c16c993626d98ec16f8a23dde6866a624be20644fb58e7542af7edd2f1688677`

源码 manifest SHA256：`9c76264caf25661ebf44ae3839abe46076c4e7ecf22ee96ea5ce61956f8134ba`。

四文件源码快照 SHA256：`655597c5cf63ec4fd1bbfd5be867d39365335ff527171f609a11dee5b5f7ad71`。

材料路径：`outputs/_run/central-av-decoded-timeline-20261028/`，含manifest/source tar、原设计快照、红绿日志、JUnit、Linux小回执、001–020保护清单。9项明确记录的直接输入前后相同；这不声称冻结了整个中央依赖图，fresh full须由root另行完整冻结。

## 实际可用性边界与下一步

私有Linux测试明确替换的只有测试进程内spawn路径，用显式官方loader和固定私有libs验证算法。没有生产wrapper、LD_LIBRARY_PATH、全局ldconfig、系统安装或服务调整。相同ELF在cleanenv直接执行依然exit127缺libavdevice.so.60；真实API UID/systemd namespace未验。因此当前是算法和focused候选通过，不是生产AV已可用。

未运行PG、APT installed-status dryrun、完整中央suite、模型请求、钱包操作或接触真实用户数据；大媒体和deb未下载本机。下一步为独立review，然后由root冻结完整当前候选并安排fresh full；库加载/服务沙箱与系统安装仍是独立门槛。
