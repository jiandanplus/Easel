# AV r2 原生帧审核：本机候选回执

四文件实现已冻结，等待独立 review、固定 Ubuntu 6.1 重放及 root 安排 fresh 中央 full。唯一最终 focused session **79282 / exit 0：137 passed，0 failures，0 errors，0 skips，13.45s**。本机工具为 FFmpeg/ffprobe 8.0.1；不是 Linux 6.1 或生产准入。

## 修复与资源边界

一次原生 ffprobe 全解码审核验证全部已确认的原 stream_index，每帧稳定 geometry/pix_fmt/sample_fmt/channels/layout；严格闭合 compact schema，固定行/读块/carry，900秒总量由最大tick/sample预算推导。后续 framehash 禁用 reinit，并与 native 视频帧数、音频 nb_samples 总和互证。sample_rate 不能从缺失逐帧字段伪造；三种短rate/补偿真实样本由固定命令输入门直接失败，不能拿总和相同或声明差异代替这一证明。

所有完整解码阶段共用60秒绝对wall，覆盖spawn/read/wait，spawn迟到仍保留子进程所有权并回收；已过期不启动下一child。每child仍CPU30、LinuxAS512MiB、FD32、FS1MiB、cleanenv；无音轨/纯音频2children名义CPU和60，AV双流3children名义和90。新增一次全解码有成本，不宣称请求累计CPU30。原version5秒/metadata10秒独立限制未改。stderr仍丢弃，无showinfo/SEI协议。

公共metadata、价格basis、原始字节、100ms声明守卫、SQL001–020、providerfacts和钱包均未改；20迁移SHA与r1证据全部相同。

## RED / GREEN 与实质验证

- 原独立两个样本逐字复制后的断言原为2failed0.29s；r2原样bytes回归加AV双流相同deadline检查为3passed0.62s。
- 仓库生成的两P1回归与native parser先RED；初轮24failed/1passed。迟到spawn测试随后收紧为“child已完成有效trace但handle晚到”，单例实质RED DID NOT RAISE，再修为green；原日志全部保留。
- 第一轮全面兼容1failed/119passed，失败仅本owned测试旧replay fixture没提供新native记录/index；同文件内最小补齐，无4文件外fixture变更。
- 真实同面积几何变化、pix_fmt变化和三种短rate/补偿为5passed；短rate默认转换误差23/80ms，但直接固定input gate非零，不依赖100ms比较。
- 保留真实CFR/VFR/B-frame/单帧/分数末帧/静态rotation、AAC全部PCM512/320ms、MP3、Opus、stereo1001、900秒音频与视频。新增native900秒长trace及tracemalloc，旧framehash长trace也保留。
- native未知/跨索引/重复/缺字段/半行/空/超量、PCM及视频数量不等；真实子进程取消、非零、输出超量、spawn迟到、共享剩余deadline及结束后无残留PID均覆盖。
- sample_fmt和layout中途改变的拒绝有parser测试；没有冒称生成了对应真实二进制样本。

## 冻结与输入范围

四源码SHA见 source-manifest.json，SHA **cb8f79b0c719d1f4a14d6785e4aa5a9d4a0d3a14dacf39bf02dc6678511955df**；四源码tar SHA **fb226658ea6acd33f2264c9ac3d67a26c86ed3ca1cf4d7cc7f778d4324a56013**。证据目录 `outputs/_run/central-av-native-audit-r2-20261028/` 包含原RED、最终日志/JUnit、r1→r2差异、实际工具版本/hash、输入manifest与获批4f8f方案原副本。

9个明确直接输入前后完全相同（四文件、rates/errors/credits/conftest/pyproject），这是直接输入记录，不冒称完整依赖闭包。另212个中央文件只是本focused窗口的扩展只读hash观察，前后相同；**不是全中央冻结承诺**。其它agent的F2 restore_capture源不属于本AV测试直接闭包，后续无关漂移不改变该focused回执，根会在各方稳定后重新冻结full。

## 未通过/未执行

本轮没有SSH、PG、生产、OS安装、模型请求、真实用户数据或full。Linux6.1 r2实际重放待根另授权；旧private-loader只说明旧算法兼容，不能抵消实际cleanenv/directELF库加载失败，APIUID/systemd门槛仍未通过。原r1 tar/95绿及两个P1失败样本与原源码保留，历史1895full不沿用到本r2。
