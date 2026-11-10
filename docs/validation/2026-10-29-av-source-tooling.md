# AV 完整源码准备：工具实现与离线验证

本批完成四个部件的实现、缺陷修正和限定独立复审；root 六文件组合实际 **230 passed／0 failed，3.12 秒，退出 0**。这是源码准备工具的离线验证，尚未完成真实完整源码取得、受限 Linux 执行、编译、生产 AV 或供应商验收。分支继续为 `codex/ui-redesign`，没有新增提交、推送或合并。

## 本次实现

| 部件 | 已实现行为 | 当前证明边界 |
| --- | --- | --- |
| 归档检查／提取 | 归档 size／SHA pin、压缩容器与 tar 元数据预算、逐头／逐文件读取上限、GNU 普通头、先完整验证后排他写入新目录 | 支持本批所需格式的合成测试；未提取真实 FFmpeg |
| 原始 Git 对象链 | 原始 commit／tree／blob 重算、规范 mode／排序／路径、预算、gitlink 只记录、symlink 不物化 | 完整合成链及本机 Git 兼容；不把 hash 当签名 |
| Git v0 协议 | 固定目标引用、浅层请求、pkt／sideband 有界解析、pack 容器 header／trailer 校验 | 34 项含两次真实本机 Git 回放；容器通过不代表对象／来源通过 |
| 固定仓库 HTTP | 两个公开仓库与 commit、TLS／禁代理认证继承、禁 redirect、原始 HTTP 与 advertisement 共享预算、异常闭流、私有目标排他落盘、无重试 | 真实 HTTPResponse／opener 内存流回归；尚未在线获取完整来源 |

代码入口见 [av_sources README](../../services/platform/scripts/av_sources/README.md)；该目录不是生产构建器。`source_identity_verified`、`build_inputs_adopted`、`os_resource_limits_verified` 继续为 false。

## 失败、修正和复审

归档首轮 37 项通过后，独立反例发现目录身份、压缩 metadata、限额过晚应用、原始路径和异常类型五项缺口。统一修正后 69 项通过，第二轮限定 Spec／Quality PASS，另 16 个独立观察项通过。初审报告已保存，但 reviewer 最终工具状态 error；本次保留其完整文件及该状态，不改写旧失败。

Git 对象首轮 58 项后，独审发现全零 gitlink 与负时间戳两个问题。先新增回归取得 12 failed／59 passed，再修正至 71 passed。第二轮限定 Spec／Quality PASS，另 4 个独立观察符合预期。对签名、真实上游及可采用性未作扩大结论。

HTTP 首轮 31 项后，独审发现原始 headers／chunked trailers 预算、目录使用前 chmod、错误流泄漏三类问题。先取得 13 failed，再实现读取层计费、正确目录身份前提和 HTTPError／redirect 闭流；HTTP、25 个新增回归及协议组合 90 项通过。第二轮限定 Spec／Quality PASS，另 10 个独立检查通过。r2 报告写入前发生连接中断，恢复同一审查后落盘，原 FAIL 和中断记录保留。

目录安全结论依赖调用方控制的、有效 UID 所有且权限 0700 的父目录及可信单写者；不宣称这些检查能够隔离任意恶意同 UID 并发修改。各作者、独立复审及 root 组合计数不相加为一次全量。

## root 完整组合与环境

root 实际使用本机 Python **3.12.7**，运行 README 中六个测试文件，`--noconftest` 排除无关的数据库 conftest 导入。原始普通 pytest 入口缺 `psycopg` 的错误另存；没有安装依赖，也没有将它算作功能 RED。六个文件不依赖数据库 fixture，230 个用例没有跳过。10 个实现／测试输入在运行前后逐 SHA256 一致。

两次真实本机 Git 回放使用 Git 2.50.1 的全新合成裸仓库、空模板和最小环境，关闭用户／系统配置及替换对象。实际 upload-pack 产出的浅层响应经 Python 解码，进入另一个空接收仓库的严格 index-pack，最后读取原始对象生成完整树清单，marker 字节／SHA 匹配。初次 native 用例错误假定根 commit 不会被标记 shallow，原 1 failed 留存；纠正测试预期后 34 项通过，生产协议代码没有因此改动。这不是对真实上游、服务环境或生产 bounded loader 的验收。

## 实际服务器只读事实

严格主机校验的既有 SSH 连接只读取 154 主机的工具、资源以及原有已签名 FFmpeg 归档；没有上传新工具、提取、安装或创建服务。归档仍为 12,040,788 字节、SHA256 `8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e`。原始流扫描得到：

- 10,525 个 GNU 普通 tar 头：126 目录、10,399 普通文件；没有链接、PAX／GNU 扩展或稀疏成员。
- 最大普通文件 949,519 字节；普通内容 90,358,343 字节；展开 tar 流 98,703,360 字节。
- 头本身共 5,388,800 字节，超过 root 最初给的 4 MiB 预算；因此仅将 tar／压缩容器 metadata 改为 8 MiB。新值有同规模合成回归，显式 4 MiB 仍拒绝；未把新结果倒写成旧值通过。HTTP 和单 Git metadata 仍 4 MiB，其他限制不变。
- 目标具备 Git、Python 3.12.3、gpgv、unshare、systemd-run 和 prlimit；systemd 为 255.4-1ubuntu8.18。资源数据只是采样；预检自身设置的 AS／CPU 不代替后续工具执行门。

systemd 255 官方文档已查询并保存，但没有执行临时单位或应用配置。完整执行器仍需实现并验证整个作业的内存、任务数、wall time、网络及文件系统隔离；不能仅凭支持某属性或协作式 timeout 声称限制有效。

## 证据及下一步

[完整白名单归档](../../outputs/_run/central-av-source-tooling-20261029)保存当前源码／测试、原始 RED／GREEN、三轮限定复审、root 完整日志和只读服务器回执；[manifest](../../outputs/_run/central-av-source-tooling-20261029/manifest.safe.json)逐项记录复制前／副本／复制后 SHA、大小和来源。

[最终只读保护检查](../../outputs/_run/central-av-source-tooling-final-guard-20261029.safe.json)另存：10个源码／测试输入仍与完整运行一致，branch／HEAD不变，diff check退出0，原17868返回200且HTML精确匹配v31，原ZIP SHA不变。没有新模型／钱包操作、生产服务变更或源码提取／构建。

下一步接入受限 Linux 执行器，再验证已签名 FFmpeg 的实际安全提取，以及 libplacebo／配对 Vulkan 的完整 Git 对象、签名绑定和子模块边界。之后才进入完整依赖、编译／profile、真实 API UID／systemd 运行和供应商／生产验收。原支付暂缓、第二产品、签名净机、F2 与工作区接续等目标仍保留在[总执行账](../superpowers/plans/2026-10-27-delivery-completion.md)；本批不将总目标标记完成。
