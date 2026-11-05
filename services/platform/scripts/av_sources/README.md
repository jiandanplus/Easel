# AV 源码准备工具

本目录提供有界传输、原始 Git 对象链校验和归档检查／提取部件。它们尚未组成生产构建器；真实来源的签名绑定、受限 Linux 执行、完整依赖和构建采用由后续执行流程分别验收。

| 模块 | 接口 | 当前职责 |
| --- | --- | --- |
| `safe_archive.py` | `inspect_archive`、`extract_archive`、`ArchiveLimits` | 先核归档 size／SHA256，再逐成员验证；完整验证后才向新目录写入；支持本批所需 GNU 普通 tar 头和单流 xz |
| `git_objects.py` | `verify_commit_tree(commit_oid, read_object, limits=Limits())` | 重算原始 commit／tree／blob 身份并生成完整清单；gitlink 只记录，不自动取子模块；symlink 只核 blob，不跟随或物化 |
| `smart_http.py` | `parse_advertisement`、`build_fetch_request`、`decode_pack_response` | Git v0 浅层获取的有界 packet／sideband 解析及 pack 容器校验；不自称对象或签名验证 |
| `bounded_fetch.py` | `fetch_pack(source_id, destination, limits=HttpLimits())` | 仅固定 libplacebo／Vulkan-Headers 仓库与 commit；TLS 校验、禁用代理／认证继承和重定向；无自动重试 |

`fetch_pack` 的 `source_identity_verified`、`build_inputs_adopted`、`os_resource_limits_verified` 均保持 `false`。原始 HTTP 输入预算与 socket timeout／协作时钟检查不能替代 OS 内存、进程数、整批时间和 DNS／目的地址隔离。下载候选目录不能直接作为已认证构建输入。

归档提取和候选落盘要求调用方提供自己控制的、有效 UID 所有且权限恰为 0700 的父目录。调用方须排除并发恶意同 UID 修改；no-follow、inode 比较和权限检查不构成对同 UID 恶意进程的隔离。文件排他写入 0600，原可执行位只记入清单。已有目标和失败部分不会被自动覆盖或清理。

归档头及其压缩容器 metadata 默认 8 MiB：实际 FFmpeg 输入的 10,525 个 tar 头就占 5,388,800 字节，旧 4 MiB 值不足。HTTP metadata 及单个 Git metadata 对象仍为 4 MiB。归档拒绝 PAX／GNU 扩展、链接、特殊成员和多流压缩；bzip2 保守地把全部压缩输入计入 metadata，因此仅支持满足该预算的子集。任何未支持输入都明确拒绝，不漏项后宣称完整成功。

从仓库根目录验证这批独立离线部件：

```sh
rtk proxy .venv/bin/python -m pytest --noconftest -q \
  services/platform/tests/test_av_source_archive.py \
  services/platform/tests/test_av_source_git_objects.py \
  services/platform/tests/test_av_source_smart_http.py \
  services/platform/tests/test_av_source_bounded_fetch.py \
  services/platform/tests/test_av_source_bounded_fetch_extra.py \
  services/platform/tests/test_av_source_http_review_regressions.py
```

这些测试不需要平台数据库 fixture。`--noconftest` 仅排除无关的数据库 conftest 导入，不将平台全量回归记为通过。两项真实本机 Git 回放使用全新的合成裸仓库及空模板，禁用用户／系统 Git 配置；它们证明协议、严格 pack 导入与合成完整对象链互通，不代表真实上游签名或 Linux 资源验收。

实施与后续门见[源码获取工具实施账](../../../../docs/superpowers/plans/2026-10-28-av-source-acquisition-tooling.md)。
