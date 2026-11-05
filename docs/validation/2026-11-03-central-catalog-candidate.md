# 中央服务目录校验修复候选包

2026-11-03，`codex/ui-redesign`。本报告记录当前001–020中央服务 wheel，不是新的 macOS 桌面发行版。

## 已构建产物

文件：`outputs/_run/central-catalog-candidate-20261103/wheel/easel_central_platform-0.1.0-py3-none-any.whl`

- 大小：496,103 字节。
- SHA256：`a6d5553286c69b3a62f8ae6d7f67f8a43b615b0cd2c0ef67bce13c45cb59e268`。
- 标准 PEP517 隔离构建：uv0.11.16、Python3.12.7、Hatchling1.32.4。
- 公开构建依赖6个，完整版本与哈希记录在候选目录的 `build.lock`；获取完成后实际构建使用 `--offline`、`--require-hashes` 和固定构建约束，没有 Python 下载或系统安装。
- 精确138个公开构建输入，1,739,734字节；没有复制整个项目、配置或私有数据。

包内140成员、139个有哈希的RECORD记录、20份实际迁移。完整CRC、路径唯一性／无穿越／常规文件、逐RECORD大小与SHA、137个映射源码／资源成员均已验证。与原 `c23cb4cb…` 候选相比，无新增／删除成员，只有 `profiles.py`、`source_facts.py` 和 RECORD 三个成员变化；控制SQL、公开迁移、前端静态资源及其他成员字节不变。

本轮实际离线构建命令记录：

```sh
rtk proxy env -i PATH=/opt/homebrew/bin:/usr/bin:/bin:/usr/local/bin UV_CACHE_DIR=/private/tmp/easel-central-catalog-candidate-20261103-r1/cache PYTHONDONTWRITEBYTECODE=1 /Users/jiandan/.local/bin/uv --no-config build --wheel --force-pep517 --offline --no-python-downloads --no-sources --python /opt/anaconda3/bin/python3.12 --build-constraints /private/tmp/easel-central-catalog-candidate-20261103-r1/build.lock --require-hashes --no-create-gitignore --out-dir /Users/jiandan/Code/GitCode/Easel/outputs/_run/central-catalog-candidate-20261103/wheel /private/tmp/easel-central-catalog-candidate-20261103-r1/source
```

新前缀安装采用 `uv pip install --offline --no-deps --link-mode copy`，随后才添加共享锁定依赖的 `.pth`。本轮六组合实际执行入口是候选目录内的 `run_installed_whole.py`；精确argv、最小环境、cwd与原期限保存在其独占intent回执中。该次运行已经完成，固定运行目录保留。

## 已安装验证

新建独立运行前缀并离线安装候选 wheel。隔离 `-I -B` Python 的实际产品、db、profiles、source_facts、B1 contract 来源均在新前缀内；139个分发文件和20份迁移已逐项重核。候选包实际安装于源码目录之外。

运行依赖复用既有固定T12环境（PyNaCl1.6.2／cryptography50.0.2／Pillow12.3.0／psycopg3.3.6／HTTPX0.28.1）；这次没有重新安装这些依赖，也不作为净机验收。候选产品优先从新前缀加载，未将checkout源目录加入产品路径。

外部合成测试工具只从已复制测试目录与原固定测试工具目录加载。profiles／source_facts／bundles／self_verify共 **80 passed，1.09秒**。运行环境收集与产品来源的证据分开保存在 `installed-origin.safe.json`、`install-harness.safe.json` 和 `installed-pure.safe.json`。

已安装包随后在同一外部测试目录实际完成原六组合：**6 passed，49.34秒**，父进程观察49.618秒，exit0、未超时且在原180秒截止前完成。pytest进程以 `-I -B` 启动，在运行原用例前逐模块核对来源，全部来自新候选前缀。运行后139个分发文件仍与 wheel 对应哈希一致；前缀额外缓存另在 `installed-postflight.safe.json` 中列出，不声称整个安装目录的文件集合零变化。

本轮六组合实际经过捕获、认证加密与提交、新进程私有文件恢复，使用原合成数据库／角色及MockTransport图片。它不是把旧源码结果移记给新包，也不是数据库导入／权限重建验收。实际运行记录通过 intent SHA、同一run路径、runner及测试文件SHA和candidate SHA关联；原始私有日志没有对外导出。

独立候选复核见 [审查报告](2026-11-03-central-catalog-candidate-review.md)。该审查的独立部分为包内容、源码和公开材料一致性；实际安装与执行由Root完成，不把manifest当独立运行证明。原当前源码的九项捕获／文件恢复结果继续单独保留。

## 交付范围

这是包含目录损坏输入修复的中央服务候选；版本元数据仍为原0.1.0，独立目录与SHA标识此候选。`production_compatible=false` 保持，未进行生产安装或服务切换。完整B2／B3、数据库与权限恢复、Linux实际全覆盖、正式桌面包／签名／公证仍需各自证据。

本轮未改变原17868工作台、登录、对话历史、订阅／自定义接口模式或前端文案；未提交、推送或合并。
