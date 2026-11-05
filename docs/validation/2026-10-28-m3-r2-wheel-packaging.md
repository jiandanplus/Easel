# M3 r2 中央 wheel 本地交付收据

已从最终 423 文件冻结快照独立构建并核验中央 wheel，未混入活动工作树的新 Incho 协议。仅本地构建、离线安装与受控解码检查；没有 SSH、上传、生产迁移／部署、真实供应商请求或新的完整测试。

## 交付物

| 对象 | 路径／SHA-256 |
| --- | --- |
| wheel | `/private/tmp/easel-m3-r2-wheel-20261028/dist/easel_central_platform-0.1.0-py3-none-any.whl`；`936b7cb181b60a9fae9c0b9f868ed8ea0a1df081d2f51d8b77ed865333be54f1` |
| 完整交付材料包 | `/private/tmp/easel-m3-r2-wheel-20261028/easel-central-m3-r2-local-delivery.tar.gz`；`7f5daf04877baa4d083639a959eb4275f09f10127e7318ac438b21dcd20e311d`；1,566,992 bytes |
| 结构化收据 | `/private/tmp/easel-m3-r2-wheel-20261028/packaging-receipt.safe.json`；`423daee785c97d2c00ec73d316c2d45db54224f0586e430dc19fac85b450e44c`；仓库副本见同名 `2026-10-28-m3-r2-wheel-packaging.safe.json` |
| 来源快照 | `/private/tmp/easel-m3-final-central-final-20261028/input-source-snapshot.tar.gz`；`657d8540c22dfcd1e65724580a4a777ca21ac2663bb7a2e7c6d168931ff12ffb` |
| 来源 manifest | 同目录 `input-manifest.json`；`a0881006646406dbabd78f112f7b6506c1bafdcb6b205a1a619d5edf434bbeed` |

材料包包含 wheel、完整原始423源码快照与manifest、依赖锁及清单、每个wheel文件SHA、补充资源来源、完整ACL合同、001–019迁移SHA、只读预检脚本及部署预检说明。不包含生产凭据、真实媒体、DB快照或目标平台第三方依赖wheelhouse。

## 实际执行与核验

- 将原 tar 安全解包到独立 `/private/tmp/easel-m3-r2-wheel-20261028/source`；423 文件 SHA 构建前后全部匹配。未读取活动中央代码作为构建输入。
- 使用冻结 pyproject 及 uv.lock，离线调用已缓存 Hatchling 1.32.4 的 `hatchling.build.build_wheel`，退出0。完整工具版本记录于 `build-tools.json`，stdout保存于 `build.safe.log`。
- wheel共101成员：94个冻结Python／SQL、4个有单独来源的补充资源、3个dist-info元数据。94个源码文件与tar逐字节匹配，包含全部001–019；所有98个payload与期望集合严格相等，RECORD内哈希和大小逐项通过。
- `pip install --no-index --no-deps --target` 安装到独立临时目录，退出0。没有更改现有虚拟环境或旧wheel。
- 从安装目标导入7个主要模块，实际 `__file__` 均位于该目标。Python为3.12.7，满足wheel的 `>=3.12,<3.13`。
- 当前平台26个运行依赖的闭包及extras逐项满足METADATA约束，版本与冻结uv.lock一致；完整记录在 `runtime-dependencies.json`。Windows条件依赖colorama／tzdata不适用于本机，不算安装失败。
- 安装后的预检通过98个文件、Python版本、实际ffmpeg／ffprobe路径／权限／版本和合成媒体目录／配置的权限检查。配置为新建的空JSON测试文件，不读真实配置。
- wheel内 `inspect_av` 使用真实ffmpeg／ffprobe全解码本地合成WAV：32,044 bytes、16kHz、单声道、1000ms，SHA `643f8a8dc8bd9c19225afffad2becfec5426180b3749cb208abdf1a6c8354efc`。不涉及供应商或钱包。
- 预检负例：错误固定AV版本返回 `av_version_mismatch`；给临时安装目录加入一个合成额外Python文件后返回 `unexpected_installed_code`，移除该测试文件后原安装再次通过。未修改冻结源码或wheel。
- 预检脚本编译通过。`--database pre016`／`post019`模式**没有连接数据库执行**；其生产执行、恢复副本验证和独立审查留到发布准备，不能把语法检查称为PG预检通过。

## 补充资源来源

423快照缺少pyproject声明force-include的Console dist和Pillow许可证。四文件从历史已记录wheel提取：`/private/tmp/easel-media-m1-wheel-20261027-review-fixes/easel_central_platform-0.1.0-py3-none-any.whl`，SHA `b070d12f7c48f65709ebdfb295a76a2e1e45b79b612404893e5e541f00d1dfd9`。具体为原 `console_static/index.html`、一个JS、一个CSS及 `licenses/Pillow-12.3.0-LICENSE.txt`。来源成员名、目标位置、大小、SHA均见 `supplemental-inputs.json`；它们不被重标为423测试输入。旧wheel最终SHA仍相同，保留不覆盖。

## 测试与部署边界

复用 [最终中央验证](2026-10-28-m3-r2-central-final.md)的1794 passed／7 skipped／0 failed／0 errors、会话77597；复用独立r2定向8 passed／5.44秒的限定本地APPROVE。本次打包检查没有重跑或累加这些测试。所有原失败收据、源码快照及229／243／247定向证据保留。

可执行的迁移／schema owner／API与Console ACL／ffmpeg预检顺序及回滚限制见 [部署预检计划](../superpowers/plans/2026-10-28-m3-r2-package-deployment-preflight.md)。生产仍为f50…／016，017／018／019未部署；真实渠道／商业费率不全的能力和旧DashScope enrollment保持closed。真实mixed DR仍failed、models已恢复、待诊断；本轮不能解除恢复门槛。

本wheel虽然标记 `py3-none-any`，并不包含目标Linux的第三方二进制依赖或ffmpeg／ffprobe；目标架构wheelhouse、解码沙箱、生产DB／ACL、完整新备份恢复均未验收。项目版本仍0.1.0，后续只能用本收据SHA绑定发布，不能按同版本名推断安装的是该候选。
