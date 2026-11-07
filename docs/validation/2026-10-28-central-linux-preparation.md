# 154 独立 Linux 依赖准备与受控导入验证

**已完成隔离 Linux wheelhouse、冻结依赖离线安装及候选导入／PNG全解码验证；没有部署生产。** 仅SSH alias `easel-zhiyin`，StrictHostKeyChecking=yes，确认目标154.217.245.179，未访问115。所有写入限定新root私有准备目录，没有修改systemd、生产release、数据库、钱包或旧pending。

## 服务器与候选身份

| 项目 | 实际结果 |
| --- | --- |
| OS／架构 | Ubuntu24.04.1 LTS，x86_64；kernel6.8.0-48-generic，glibc2.39 |
| Python | 已安装正规 `/usr/bin/python3.12`，3.12.3；venv／ensurepip可用，未升级系统Python |
| 隔离环境 | `/root/easel-central-linux-preparation-20261028/venv`，pip24.0；未升级pip |
| 私有准备目录 | `/root/easel-central-linux-preparation-20261028`，新建root所有0700，创建前不存在 |
| root分区余量 | 开始42,507,374,592 bytes；结束42,380,840,960 bytes |
| 候选wheel | `easel_central_platform-0.1.0-py3-none-any.whl`，SHA `e7af8e66cc9a34deb92b47b5bc6f22f6d7406e0e510d3199b9a768efae3f8487` |
| 原交付材料包 | durable本机SHA `bb638bb7c38789bcaa33ca685d87a83c5b84f6a0a627f90239ce16ba28942294`；本轮只上传小材料，不传完整源码包 |

候选来自 [431冻结包](2026-10-28-central-m3-incho-header-wheel.md)，没有改业务源、迁移或依赖版本；没有把新Pi worker／前端或之后的SDK结果混入。只上传10个公开文件共469,597 bytes，含候选wheel、pins、uv.lock、pyproject、payload／ACL／迁移SHA清单和预检脚本。未读取或输出env、认证／供应商密钥、数据库凭据或业务数据。

## Linux marker闭包、官方来源与哈希

在服务器实际CPython3.12.3/Linux/x86_64 marker环境下，从冻结uv.lock根项目依赖递归求闭包，保留psycopg[binary,pool]与pyjwt[crypto] extras。结果**26包**，名称、版本和extras与原runtime pins完全一致；Windows的colorama／tzdata按marker排除。没有直接把macOS wheel目录搬到Linux。

每个兼容候选先请求 `https://pypi.org/pypi/<name>/<version>/json`，检查官方name／version、非yanked、requires_python、文件URL、大小和SHA与uv.lock一致，下载仅来自 `https://pypi.org/simple`／`https://files.pythonhosted.org`。不使用镜像、代理环境、trusted-host或TLS绕过。pip子进程使用最小环境并以PIP_CONFIG_FILE=/dev/null禁用配置文件；不读取用户环境里的索引或凭据。

实际下载用 --only-binary=:all:、--require-hashes，下载后再核对目标tags、uv.lock及官方metadata SHA。**26个wheel共21,295,897 bytes**，全部留在服务器 `wheelhouse/`，没有下载大wheelhouse到本机。官方metadata原文、approved-assets、下载日志和全部SHA留在同一私有目录。

本轮实际流程：

```text
/usr/bin/python3.12 -I -m venv /root/easel-central-linux-preparation-20261028/venv
venv/bin/python -I prepare_linux.py fetch
  pip --disable-pip-version-check --no-cache-dir download --index-url https://pypi.org/simple --only-binary=:all: --require-hashes --dest wheelhouse -r requirements-linux.lock
venv/bin/python -I prepare_linux.py install
  pip ... install --no-index --find-links wheelhouse --only-binary=:all: --require-hashes --report offline-install-report.json -r requirements-linux.lock
  pip ... install --no-index --no-deps --report candidate-install-report.json easel_central_platform-0.1.0-py3-none-any.whl
  pip ... check
venv/bin/python -I prepare_linux.py smoke
venv/bin/python -I preflight.py
```

路径在脚本中均展开为该独立目录的绝对路径。网络下载完成后，两次安装均明确--no-index；没有升级或写入系统site-packages。具体工具会话及终态见durable tool-sessions.safe.json。

## 每个Linux wheel的身份

文件名、官方URL、大小以及完整tag集合见 [wheelhouse库存](../../outputs/_run/central-linux-preparation-20261028/wheelhouse-inventory.json)。以下SHA均同时匹配冻结uv.lock、官方PyPI资产元数据、实际下载字节，安装后再次核对未变。

| 包／版本 | wheel tag | SHA-256 |
| --- | --- | --- |
| annotated-doc 0.0.5 | py3-none-any | `117bac03a25ede5df5440e855b32d556049ca169ead221505badf432fed4b101` |
| annotated-types 0.8.0 | py3-none-any | `f072f4d804ea359e4eaf198b1af7a8b0943881a87f31bb764f8bf219bb9419e0` |
| anyio 4.15.1 | py3-none-any | `6152fdbbf9a77fdec97731721bebf7c4c44f7c29b424b0065826173efc7ed101` |
| certifi 2026.10.19 | py3-none-any | `42d35340cc5b9ba78e818ddbff0c5b1c2b779de448a340fd1b989f5fa8670e1b` |
| cffi 2.1.1 | cp312-cp312-manylinux2014_x86_64; cp312-cp312-manylinux_2_17_x86_64 | `c1453022f490d2459a11819d83ad1d586e9ff65a12ac3e705ffebd46d3685dcf` |
| click 8.5.0 | py3-none-any | `255bc9599cf7748b4b1a446ccc735421bd08a2ae529a8b88597d3de5664ee360` |
| cryptography 50.0.2 | cp311-abi3-manylinux_2_34_x86_64 | `9dab55f57c74c3cad24c323bacbbd04be4705ba6eb0d92e920b1fc4837ed5079` |
| fastapi 0.159.1 | py3-none-any | `49b94c29afec2fca1390e9c39be202c59755d5cda8e4f1148a133761e4313e7e` |
| h11 0.16.0 | py3-none-any | `63cf8bbe7522de3bf65932fda1d9c2772064ffb3dae62d55932da54b31cb6c86` |
| httpcore 1.0.9 | py3-none-any | `2d400746a40668fc9dec9810239072b40b4484b640a8c38fd654a024c7a1bf55` |
| httpx 0.28.1 | py3-none-any | `d909fcccc110f8c7faf814ca82a9a4d816bc5a6dbfea25d6591d6985b8ba59ad` |
| idna 3.20 | py3-none-any | `ab7ae7122974553370f0bdb919e1a960b2cd1bc1ef0276416d896db81c14582c` |
| opentelemetry-api 1.45.1 | py3-none-any | `b31553efa588ae44bc306f863c785c5333a9ecc091248c6ee68b4b6c87fdedfb` |
| pillow 12.3.0 | cp312-cp312-manylinux_2_27_x86_64; cp312-cp312-manylinux_2_28_x86_64 | `78cb2c6865a35ab8ff8b75fd122f6033b92a62c82801110e48ddd6c936a45d91` |
| psycopg 3.3.6 | py3-none-any | `a1db9f7148b06a28606767efaca51fa6f9398c5c0a3810519be69d7000bdb631` |
| psycopg-binary 3.3.6 | cp312-cp312-manylinux2014_x86_64; cp312-cp312-manylinux_2_17_x86_64 | `1679a1cb93fbe5a6d1fd58d82cbddcc6fcb8c61446ba7cae6eb2a7b19bc585de` |
| psycopg-pool 3.3.3 | py3-none-any | `9b9cd6a4fcec47a410f7e82d408540e7f77b478509e91b44c1a5457a13e5ff37` |
| pycparser 3.11 | py3-none-any | `51d5a8ba2be0bbe440b99d2112604c95bbbc3c2748a64260186c541e1729cd80` |
| pydantic 2.14.1 | py3-none-any | `9195d967ec791692a04438115466764fb8b9a27b31f14a760437694f40d6b454` |
| pydantic-core 2.50.1 | cp312-cp312-manylinux2014_x86_64; cp312-cp312-manylinux_2_17_x86_64 | `93ba4e9d8210d941c200431a56b2c0400b131865947903937ed3ec5404307d2e` |
| pyjwt 2.15.1 | py3-none-any | `42d59d631f7768a1028a64c7ff581a9bf7519804daf91fc5b6c56e30eec5e193` |
| qrcode 8.2 | py3-none-any | `16e64e0716c14960108e85d853062c9e8bba5ca8252c0b4d0231b9df4060ff4f` |
| starlette 1.9.0 | py3-none-any | `fd3f159936e882d06c1108f84688f7cda2cf85fc6a592a50edbd175e882c2ddc` |
| typing-extensions 4.16.0 | py3-none-any | `481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8` |
| typing-inspection 0.4.4 | py3-none-any | `65b8397ba37ccbce054456aaccddfc91e6e3083c92824df348d96ca832f3f147` |
| uvicorn 0.54.0 | py3-none-any | `505bdb0f318731d45f1f712071fc781a8981f6847a31c902c9f5e652d4f67faf` |

## 离线安装、实际导入与PNG

- pip check：`No broken requirements found.`，退出0。
- 安装后26个版本与Linux闭包一致；输入文件和wheelhouse前后SHA全部一致。
- 候选100个payload逐项SHA与精确Python／SQL文件集合通过；001–020随wheel保留。
- app、console_app、execution_runtime、media_runtime、voice_execution、audio_execution、newapi_voice、newapi_incho共8个模块均从新venv导入。没有启动ASGI lifespan或任何服务，不连接PG。
- Pillow12.3.0生成32×32合成PNG，候选inspect_image执行verify／完整load解码：102 bytes，SHA `4a34dd222d0ba07c25cb01a7d183264871563dad20dbd4ec59dae12643b7efed`。原字节留在私有准备目录synthetic.png，不是业务图片。
- 独立preflight.py仅执行无DB的artifact／Python检查，退出0。pre016／post020数据库模式未执行。

## AV只读发现与后续方案

当前/usr/bin、/usr/local/bin没有ffmpeg／ffprobe；dpkg查询没有安装ffmpeg/libavcodec/libavformat/libavutil；ldconfig缓存也没有对应AV库。没有安装或运行AV解码器。

读取现有APT缓存时，ffmpeg、libavcodec60、libavformat60、libavutil58候选均为 `7:6.1.1-3ubuntu5`、amd64，仓库主机archive.ubuntu.com，source为Ubuntu ffmpeg。该信息**仅是现有缓存**，未apt update，不能当成当前最新或已验证安全版本。未下载deb或安装APT包。

| APT候选包 | 缓存资产SHA-256 |
| --- | --- |
| ffmpeg | `1a23baace5f2688a47e28119aedae6993cd638e6279a7227dfc52d9a337a1c17` |
| libavcodec60 | `970d92c1697f762235af439d72df8c6cb492be45050a1a00bbe0d0842a137f93` |
| libavformat60 | `3a855488d50b4ebe04425e5f690e00ebd65d70e60f85c9765d5823a2f82b4969` |
| libavutil58 | `e57f8cc358f4b1b2af721ca6242723dc12290df543a142811ce7747e2d5b30cc` |

后续若授权补AV门槛，应先在独立计划中核验新鲜签名的Ubuntu发行／安全仓库索引，冻结ffmpeg／ffprobe及完整依赖版本、官方asset SHA、目标架构和许可证，再决定受控安装方式；本轮不使用旧缓存直接定版。安装后需登记固定绝对路径／二进制SHA／相同版本字符串、root或API UID所有且不可group／other写，并在实际API UID及systemd沙箱内验证执行、临时文件、资源限制和合成AV完整解码。不能用本次root隔离venv导入或PNG检查替代该沙箱验收。

## 证据位置与剩余边界

- 服务器完整材料：`/root/easel-central-linux-preparation-20261028/`，wheelhouse、venv、官方metadata、安装report和日志全部保留，未清理。
- durable本机小收据：[outputs/_run/central-linux-preparation-20261028](../../outputs/_run/central-linux-preparation-20261028/)，仅取回8个小文件，加本机预检记录共43,499 bytes（不含后追加工具会话表）。
- 核心安全收据：[同名safe JSON](2026-10-28-central-linux-preparation.safe.json)，SHA `fb932b24429da4c96f089beebe634c4158421497ea8ddce35ffe613c551953a3`，与服务器文件完全相同。

Linux Python依赖准备和隔离导入这道门槛已取得实际证据；**没有生产部署、迁移、模型启用、实际服务沙箱、DB／ACL或恢复验收**。真实mixed DR失败诊断及freshfreeze门槛继续保留；原付费unknown和hold不变且不得重POST。未运行新的full或PG测试。
