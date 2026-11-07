# 当前中央431候选 wheel：本地打包收据

已从实际完整运行8911的431文件快照构建M3＋Incho020＋关联头候选wheel，完成离线安装、合成预检正负及真实本地AV全解码。没有从活动工作树取代码，没有纳入正在修正的Pi root schema worker或新前端；没有重新运行full／PG、SSH、部署、真实供应商请求或调账。

## 交付物身份

| 对象 | 路径／SHA-256 |
| --- | --- |
| 新中央wheel | `/private/tmp/easel-central-m3-incho-header-wheel-20261028/dist/easel_central_platform-0.1.0-py3-none-any.whl`；`e7af8e66cc9a34deb92b47b5bc6f22f6d7406e0e510d3199b9a768efae3f8487` |
| 完整本地交付材料包 | `/private/tmp/easel-central-m3-incho-header-wheel-20261028/easel-central-m3-incho-header-local-delivery.tar.gz`；`bb638bb7c38789bcaa33ca685d87a83c5b84f6a0a627f90239ce16ba28942294`；1,582,994 bytes |
| 打包safe收据 | 同目录 `packaging-receipt.safe.json`；`51972ee4dcaf3dd5e1eab0ba04dee7c1a1a0a048b7961c87d4e839ab64eac409`；仓库副本为本报告同名 `.safe.json` |
| 原431源码tar | `54ac2460d2a912eeaaa47f0348ba67fd192965202b22db96f7080b0ec5f9731d` |
| 原431manifest | `5e8c9f416ac13478c332e089e3e557257fc4371090bce68c7256bd812f401cae` |

完整材料包含wheel、原431源码与manifest、pyproject／uv.lock、精确依赖清单、每文件SHA、补充资源来源、ACL和迁移清单、独立preflight.py及部署说明。打包后20个归档成员逐一核验通过。版本仍0.1.0，发布必须按SHA区分，不能凭版本名覆盖旧包。

## 本轮实际核验

- 原431文件安全解包至独立临时目录；构建前后逐文件SHA完全匹配。构建未使用工作区中央源，也未加入新Pi worker或前端文件。
- 使用与旧M3包相同的Hatchling1.32.4和缓存构建依赖，离线调用build_wheel，退出0。pyproject／uv.lock与旧M3包输入逐字相同，未更新依赖版本。
- wheel共103成员：**96个冻结Python／SQL＋4个补充资源＋3个dist-info**。100个payload与预期集合严格相等，源码逐字匹配，所有RECORD哈希／大小通过；完整001–020齐全。001–019与旧M3冻结包源码一致，020只更换media_work_adapter_check，没有模型／价格／授权数据写入。
- 离线pip --no-index --no-deps安装到新临时目标，退出0。8个主要模块（含execution_runtime及newapi_incho）均确认从该目标导入。
- 26个本机运行依赖闭包满足METADATA且匹配冻结锁文件，与旧M3依赖清单相同；完整ACL清单从新wheel导出，也与旧M3版本逐字一致。
- 独立preflight更新为pre016／post020。post020要求完整001–020 checksum，并精确匹配包含newapi_incho_v1的七adapter规范CHECK定义，不以“存在Incho字符串”代替闭集。
- 本轮9项本地合成检查覆盖：安装payload／AV／目录权限正例、错误AV版本拒绝、未接受额外Python文件拒绝、恢复安装后再验证、精确020定义通过、缺Incho／未知adapter／OR TRUE／缺失定义拒绝。未修改冻结源或wheel。
- 新wheel内inspect_av使用真实本机ffmpeg／ffprobe全解码合成WAV：32,044 bytes、16kHz、单声道、1000ms，SHA `643f8a8dc8bd9c19225afffad2becfec5426180b3749cb208abdf1a6c8354efc`。
- 数据库预检模式只完成编译和纯函数合成验证，**没有连接PG执行**。不把这些检查称为生产DB／ACL或Linux沙箱验收。

四个补充资源仍来自已核旧b070 wheel：三份Console静态文件和Pillow12.3.0许可证；每文件来源／SHA见supplemental-inputs.json。未拉取新前端，不将它们重标为431输入。旧M3 wheel `936b7cb1…` 与原b070 wheel最终SHA均保持不变，旧证据和包未覆盖。

## 历史验证与当前发布门槛

来源 [完整中央报告](2026-10-28-central-m3-incho-header-final.md)严格分列：8911为**1895 passed／14 skipped**，独立旧worker SDK补充92805为**7 passed／0 skipped**。本轮未重跑、未累加这些结果，也没有把后来的root schema168 SDK证据归给本431／旧worker来源。原7项恢复测试仍未覆盖。

[本候选独立部署预检计划](../superpowers/plans/2026-10-28-central-m3-incho-header-deployment-preflight.md)已明确016→017→018→019→020顺序、schema owner／API与Console最小ACL、固定AV工具、原付费unknown不重POST以及事务提交前后不同回滚边界。

主任务提供的当前基线为 **pending=2／held=1,009,728 microcredits**（旧图片1,000,000＋新文本9,728）；本轮没有读取生产账本。原恢复controller硬编码的旧钱包窗口不能直接复用，必须独立授权fresh freeze／新plan／nonce／完整备份和fresh restore，不能只改expected数字或重标旧失败。关联头fallback不等于解决原HTTP400或释放原hold。

真实mixed DR仍failed／待诊断；Linux依赖wheelhouse、实际AV沙箱、生产schema／ACL和新恢复窗口尚未验收。生产仍仅016，缺真实渠道／费率的能力及旧DashScope enrollment保持closed。此交付是可审本地候选，不是生产发布或恢复完成。
