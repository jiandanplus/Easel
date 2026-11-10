# AV 控制器准备层：Task2B3A 验证记录

2026-11-03，`codex/ui-redesign`。本项实现私有阶段目录验证、可信 helper 加载、不可变创建配置和 Docker inspect 校验。完整执行控制器、guardian 与真实运行验收继续单独完成。

准备层检查目录／文件权限、所有者、单链接、完整路径 nofollow、逐项大小／哈希及前后身份；在加载 worker 等 helper 前核对全部八个源文件和输入 pin。使用实际已固定 worker 的 profile parser、prepare_runtime 和保留的 runtime validator；pack 输入只流式核对大小与 SHA，不在主机解码。

创建配置固定现有 ARM64 Python 镜像、非 root 用户、两个只读挂载、32MiB work tmpfs、512MiB 内存且无 swap、PID16、CPU配额、支持的 ulimits、无网络及固定日志配置。地址空间限制由原 worker 设置与验证。inspect 校验区分 created／observe／terminal，核对类型、资源、安全、挂载、镜像和状态；配置摘要保留全部 Config／HostConfig／挂载内容，排除临时网络标识并规范化挂载顺序。

独立首审发现三项测试证据缺陷和一项失败清理缺陷，已同批修复：关键字段负例与成功基线使用相同 phase；加载 tripwire 在异常转换外核对调用计数；目录替换不再引入额外条目；重复准备失败后恢复原有模块绑定并移除本次新增绑定。旧结果和失败记录保留在 `outputs/_run/av-pack-controller-20261103/task-2b3a-r1/`。

Root fresh 验证：

```sh
rtk proxy services/platform/.venv/bin/python -I -B -m pytest --noconftest services/platform/tests/test_av_pack_profile.py -q
```

结果 **68 passed in0.96s，exit0**。均为小型合成文件目录、真实 pinned helper 与合成 inspect／runtime proof；没有启动容器、线程、计时器、原生 worker 或调用模型。Root核对12项受保护文件未变、两个当前文件及完整补丁逐字节一致。

当前 SHA256：源码 `690778cf4c26b3878d6b7e330c59344b893a15f95dd28cd166e338faf6a17d70`；测试 `00f57e174e0f8cca6fc3d6a176fddf9c42853583dfc7fad7bc6206641f6a1b26`。独立复审 `TASK2B3A-REVIEW-r2.safe.md` 规格 PASS、质量 PASS、四项问题关闭，报告 SHA256 `411347cb102d17c05dc2d736cf605fdde79056dff1495ff7f0809c38c5aa2dfb`。Root完整读取首版源码／测试、修订补丁、报告与复审结果。

本轮 Root 另外只读核对了本机固定公开镜像、APIv1.49字段和daemon；这些不是创建配置已被真实应用的证明。后续仍须完成可信 operator bootstrap、单次执行持久化、guardian、同boot截止时间和最终落盘检查，再执行原生 preflight／AS CPU wall 负控、近16MiB留存、固定真实pack／源链及全部原AV／备份恢复／发行验收。原17868工作台、前端、账号、钱包和接口配置未修改；无Git提交或合并。
