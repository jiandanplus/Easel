# 中央 AV installed-status 安装模拟与副作用计划

2026-10-28；目标仅 `easel-zhiyin` → `ser362540173134`（154），严格 host key，root。依据 AV 计量计划第7节执行。

**结论：固定官方 ffmpeg 的“仅新增、零升级、零删除”依赖解存在；实际安装尚未授权，服务与缓存副作用门槛仍待 root 审批。** 本轮没有执行实际 APT/dpkg 事务、maintainer scripts、服务 start/restart/stop、生产模型或钱包操作。没有连接115，没有读取业务 config/env/auth、PG 或原用户状态。

## 已执行的隔离模拟

- 新建 `/root/easel-av-installed-plan-20261028`，root0700；未覆盖旧目录。复制 `/var/lib/dpkg/status` 留服务器，副本0600。当前649个 installed、0 held，无 half-configured/error/pending dpkg updates；不存在需要先修复的半配置事务。
- 复用 `/root/easel-central-av-preparation-20261028` 的既有输入；重新核验固定 Ubuntu keyring SHA、3个官方 InRelease 签名及其12个 Packages索引链，并重新核验233个已有deb精确SHA/bytes。没有 apt update、网络下载或向本机传deb。InRelease 无Valid-Until，属于本次固定签名快照证据，未来安装前须再核目标状态与来源。
- `APT_CONFIG` 在读取其他配置前重定向 Etc/main/parts/sources/preferences/auth、State、Cache、Log到新私有目录；唯一状态源为复制的status。sources仅官方HTTPS+固定signed-by私有keyring。`Dir::Bin::dpkg=/bin/false`、`Debug::NoLocking=true`、`APT::Get::Simulate=true`；所有host hooks不加载，并通过实际apt-config dump核对。没有修改宿主hold、selection或锁。
- 未使用 `--no-upgrade` 推断保证。逐行解析全部Inst/Remv/Conf并与完整installed版本map比较：任何已有包Inst或Conf、任何Remv都会失败。

实际唯一solver命令如下，完整输出留在服务器 `one/simulation.log`：

```sh
APT_CONFIG=/root/easel-av-installed-plan-20261028/one/apt.conf \
  apt-get --simulate --no-install-recommends --no-remove \
  install ffmpeg=7:6.1.1-3ubuntu5
```

| 结果 | 数值 |
|---|---:|
| solver exit | 0 |
| Inst | 150，全为未安装的新包 |
| Conf | 150，全对应上述新包 |
| 已安装包Inst/Conf | 0 / 0 |
| Remv / held变化 | 0 / 0 |
| 新增deb压缩总量 | 110,943,646 bytes |
| 新增Installed-Size合计 | 372,205 KiB（约363.48 MiB） |

Installed-Size是control字段合计，不等于实际事务峰值；尚需APT缓存、临时解包和系统缓存余量。开始约41.49GB可用，结束41,236,062,208 bytes可用。第一方案已经新增-only，因此没有运行第二个全installed版本pin方案；没有隐藏失败solver或为求解放宽依赖。

使用当前libapt2.7.14的parse_depends/version_compare，对status副本和已验官方包metadata逐项解析Depends/Pre-Depends与Provides、版本条件和alternative，明确选出572条依赖边；全部150个新增包均从固定ffmpeg可达，没有Recommend或无关服务被直接加入。精确边及选择理由留在服务器 `receipts/dependency-safe.json`，SHA `ff830e675516c6677bd741e499539ad0714877efa9da2e6770aebc1b491873db`。私有检查脚本早期的Deb822空字段/TagSection终止及libapt初始化错误已纠正；这些是检查器问题，无安装或状态变更，不是solver失败。

## 实际安装副作用：尚未批准

**`x11-common=1:7.7+23ubuntu3` 是不可凭 no-install-recommends 去掉的本次真实依赖。** 路径为 `ffmpeg → libsdl2-2.0-0 → libxss1 → x11-common`。人工读取其官方postinst，正常configure分支会执行 `update-rc.d x11-common defaults`，且新安装时选择 `invoke-rc.d --skip-systemd-native x11-common start`；有旧版本参数时选择restart。是否被既有策略拒绝、init脚本实际动作及其系统影响，本轮未执行、未验收。不能声称“150新包且无升级，所以无服务动作”。

另有明确系统副作用：

- 141个新增包含ldconfig相关声明；显式activate为ldconfig，实际已有interest-await处理器是libc-bin，未来可能更新系统动态链接缓存。激活者的postinst不等于会作为handler被触发，审查已区分activate与interest。
- fontconfig configure/triggered会创建或更新 `/var/cache/fontconfig` 并写 `/var/log/fontconfig.log`。
- libgdk-pixbuf-2.0-0初始configure和triggered会运行gdk-pixbuf-query-loaders并写loaders.cache。
- 已有shared-mime-info/appstream有文件路径interest；本轮未解包data payload到系统，也未完整重演路径trigger分发，所以不声称其必然触发或不触发。
- 宿主APT存在70debconf、20packagekit、99update-notifier、99needrestart等hook；PackageKit含DBus通知，needrestart位于DPkg::Post-Invoke。另有只在APT update生效的hook。本轮的完整私有配置未加载它们。实际安装若使用宿主默认APT配置，会引入额外行为；不能靠当前模拟代替未来hook策略审查。

150个deb的control.tar.zst均作有界内存解压，只将control/preinst/postinst/prerm/postrm/config/triggers等文本保存到新root私有目录，文件0600、从未执行。正则用于筛选，x11-common/fontconfig/gdk-pixbuf动作已人工读取确认。未来安装必须由root另行批准精确服务/缓存/hook策略；本轮没有写系统policy、修改脚本、禁用真实服务或擅自安装。

## 状态保全与剩余门槛

`/var/lib/dpkg/status` 前后SHA为 `8d5d512b61c141b44eb065186d50e1df09c697dd973ff07e1f3b8e3599b35240`；status-old、diversions、statoverride、APT extended_states五个文件全部前后一致，私有status副本也一致。精确五文件SHA见safe。服务无主动操作；这不是对所有主机文件或运行进程的全量不变证明。

本轮不证明实际安装成功、零重启、maintainer scripts/trigger执行安全、实际API UID及systemd命名空间可运行，亦不证明cleanenv直接ffmpeg命令、AV模型计量修复、PG、钱包或真实媒体任务验收。m3计量代码由其owner负责，本任务没有修改那四个生产文件，也没有修改F1/F2、desktop或sourcefreeze。后续先由root审本计划；任何实际安装和真实API/systemd验收均需单独明确授权。

## 来源与回执

APT_CONFIG先加载、然后才读取parts/main的顺序，已按用户Context7规则核对[官方APT源码](https://github.com/debian/apt/blob/main/apt-pkg/init.cc)；具体配置以154当前实际dump为准。精确版本选择及模拟命令依据[官方apt-get手册源码](https://github.com/debian/apt/blob/main/doc/apt-get.8.xml)。

- 服务器原safe：`/root/easel-av-installed-plan-20261028/receipts/final-install-plan-safe.json`，95,013 bytes，SHA `fee1735be2e6341fe8c267d16d4535417ceacdbf07d2d2980105bf29d988237c`。
- 只回传该小safe到 `/private/tmp/easel-av-installed-status-independent-20261028/server-install-plan-safe.json`，本机复核bytes/SHA全同；没有回传原status或deb。
- Durable安全回执：`docs/validation/2026-10-28-central-av-install-plan-safe.json`。其中150包各自的official pool路径、索引、版本、架构、SHA与bytes完整列出；以下表格是同一清单。

## 精确新增包清单

| 包 | 固定版本 | 架构 | deb bytes | SHA256 |
|---|---|---|---:|---|
| ffmpeg | 7:6.1.1-3ubuntu5 | amd64 | 1878920 | `1a23baace5f2688a47e28119aedae6993cd638e6279a7227dfc52d9a337a1c17` |
| fontconfig | 2.15.0-1.1ubuntu2 | amd64 | 180204 | `ddf0aace42b13a5bc38904e848b145b787ea254cddf5213d150254bd654db6e5` |
| libasound2-data | 1.2.11-1ubuntu0.3 | all | 21350 | `0c26896193480d9dbb055e85e8ddfb9c9f34cf19afee340987cd6311e2886fc1` |
| libasound2t64 | 1.2.11-1ubuntu0.3 | amd64 | 397772 | `5d17bfb5683eb99f1be951fe48e1be5bf3c52391913b6b74a8aecf9b4f5e779f` |
| libass9 | 1:0.17.1-2build1 | amd64 | 103874 | `a3dd7b4e6fa05d0d6607739b3c1f60a305f2fb78d9f6e8b8e38d10d0b4fc4052` |
| libasyncns0 | 0.8-6build4 | amd64 | 11276 | `f8483917fd216e248143eba1c80dfc9384b94fb4dac402194a98c11505674269` |
| libavc1394-0 | 0.5.4-5build3 | amd64 | 15442 | `28141f0e1a2011deef364a931b4a0fb9ab053416320ed7fd0b8747f251e6e5d7` |
| libavcodec60 | 7:6.1.1-3ubuntu5 | amd64 | 5851220 | `970d92c1697f762235af439d72df8c6cb492be45050a1a00bbe0d0842a137f93` |
| libavdevice60 | 7:6.1.1-3ubuntu5 | amd64 | 82320 | `62c55a1209332142eb01488894b3443060e59b44940189469ac7f8fbe03406ca` |
| libavfilter9 | 7:6.1.1-3ubuntu5 | amd64 | 4235172 | `083b800aaf4fd1de817fb8dc12995e6a277c3500159c4a0fa21d36a76b6208ea` |
| libavformat60 | 7:6.1.1-3ubuntu5 | amd64 | 1153444 | `3a855488d50b4ebe04425e5f690e00ebd65d70e60f85c9765d5823a2f82b4969` |
| libavutil58 | 7:6.1.1-3ubuntu5 | amd64 | 401184 | `e57f8cc358f4b1b2af721ca6242723dc12290df543a142811ce7747e2d5b30cc` |
| libblas3 | 3.12.0-3build1.1 | amd64 | 237978 | `1b0ef0ad0893ff77bf7d5ab75723771788e16137ea1efde557dbac7b7b83a758` |
| libbluray2 | 1:1.3.4-1build1 | amd64 | 159390 | `2cfb2a7e4b6170efcfb96e0dbe3e9321b648acc43883ab4e7c248216de18ea6f` |
| libbs2b0 | 3.1.0+dfsg-7build1 | amd64 | 10556 | `67d8af009d520e0b6f63e39d6d04d8c02e681f89858fb301b808a6c356068e2c` |
| libcaca0 | 0.99.beta20-4ubuntu0.2 | amd64 | 208666 | `f8ba81982beda87da75bef9a1035a2ff34479d613dfea757440442ae9d58c7af` |
| libcairo-gobject2 | 1.18.0-3build1 | amd64 | 126788 | `f5483cbe43455fc9e9cba7f0e166bd0a731c02db2902ba562719848f72e8c403` |
| libcairo2 | 1.18.0-3build1 | amd64 | 566292 | `96950b306889ff6e4248bb937b0a3b56e72dacf643fe732214ce375f0f6bbb36` |
| libcdio-cdda2t64 | 10.2+2.0.1-1.1build2 | amd64 | 16486 | `9651b439c9f8134052602cf5c03c5216e826bc6782a07baf1745d4dac411b109` |
| libcdio-paranoia2t64 | 10.2+2.0.1-1.1build2 | amd64 | 16570 | `4ca9df983c50971bf42a60ffb9072faa4b6af785bd0e3592d3511f37cc540503` |
| libcdio19t64 | 2.1.0-4.1ubuntu1.2 | amd64 | 62360 | `afe010f4b8fd43751e774889347d9f271bb327f3ba4a402443e679e0a6ebe50d` |
| libchromaprint1 | 1.5.1-5 | amd64 | 30524 | `ff766f8382fff4e5d3b267e4cb93f31e1805ebbb9a619cc8c805b9a2355baeea` |
| libcjson1 | 1.7.17-1 | amd64 | 24762 | `43d758e214612877ec9029f2384bdbb0a6eeba6e74bb2cd3e7a60f4108947c41` |
| libcodec2-1.2 | 1.2.0-2build1 | amd64 | 8998330 | `675a16e7260cc6773a325b9688c29f29fb7089f5780bc5360154ed6136d6331b` |
| libdatrie1 | 0.2.13-3build1 | amd64 | 18992 | `1ae164e40e413eac4fbd38ce1c6ef9591f7a03278ad9076d71fa29258727f447` |
| libdav1d7 | 1.4.1-1build1 | amd64 | 604018 | `7246303bd3ed4b99a2fb98e6199950ceb2520e7572265aefa0a6e44352adb3f0` |
| libdc1394-25 | 2.2.6-4build1 | amd64 | 90084 | `0f4a0768152636f2f941d3a29d24925be77f869b3d90a13f1694fef792a89810` |
| libdecor-0-0 | 0.2.2-1build2 | amd64 | 16508 | `64bf085d16e504e9ed39b91d2a46748fb8881ad7f3603fbacef6c11c04aa7064` |
| libdrm-amdgpu1 | 2.4.125-1ubuntu0.1~24.04.2 | amd64 | 21374 | `aa29b7898bae596bf07e55ba178cf8d5ea698fe83688e1b34520d86010251a7a` |
| libdrm-intel1 | 2.4.125-1ubuntu0.1~24.04.2 | amd64 | 63868 | `847195765b9ef7e7886677aeae9987aa54e496758b4e6e5a4ed2cb580b252244` |
| libfftw3-double3 | 3.3.10-1ubuntu3 | amd64 | 838254 | `ff9396076103b305efe741e9acff11d52a040c6d95527c0f2dbd11e82a11bab8` |
| libflac12t64 | 1.4.3+ds-2.1ubuntu2 | amd64 | 197320 | `f2396692573407b2fbc92cd83148342d4aec717d42d5e24ac64c471204a0b7a9` |
| libflite1 | 2.2-6build3 | amd64 | 13601102 | `367f1d0da5cd38759a0515eafc27aa133b2d7bf99308cac34831df0212e96b75` |
| libgbm1 | 25.2.8-0ubuntu0.24.04.4 | amd64 | 34172 | `4bfe348b456db5b445a80e0a3ffb9e9a01b91499bb86e0e568ca3634b134da7e` |
| libgdk-pixbuf-2.0-0 | 2.42.10+dfsg-3ubuntu3.3 | amd64 | 147442 | `de84e0d777d9c9e39148d8491338e6b8c62bc2d066867266b1643a7a725a86ba` |
| libgdk-pixbuf2.0-common | 2.42.10+dfsg-3ubuntu3.3 | all | 8302 | `57f24caf1fe608d009933591bdbbf70851a4dbee8b75e9c097f68f57a9a72e07` |
| libgfortran5 | 14.2.0-4ubuntu2~24.04.1 | amd64 | 916316 | `2b05896c477275ad3473bb63a5ac5a5b8a7e4a6ad6ed7cb23c442ccd1abc28f5` |
| libgl1 | 1.7.0-1build1 | amd64 | 101990 | `e2b2fadeb883f073b566ad1d7874f6702397514d258473e96152a89d4d502a09` |
| libgl1-mesa-dri | 25.2.8-0ubuntu0.24.04.4 | amd64 | 37950 | `2dabbe4292b201ca07b38c0672352f68dbfc55b5850f96f89ef54d3b4b6c8a59` |
| libglvnd0 | 1.7.0-1build1 | amd64 | 69594 | `33f5e07c74f73c2bfb44086ae0f9e6da52acd6c104d30ffe8fd768d8253d5e82` |
| libglx-mesa0 | 25.2.8-0ubuntu0.24.04.4 | amd64 | 110128 | `5aa6164059989d476b15caa8ade892b1dec37593934dc4f617ece7ea0d639bc0` |
| libglx0 | 1.7.0-1build1 | amd64 | 38558 | `aa4953182f30abde90cb5b072e83d7286b557b325769b8686196a7bd396d8795` |
| libgme0 | 0.6.3-7build1 | amd64 | 134050 | `a54fa608501f87f9e5064b60c75fc1bb62735c9f9ba9f7680cb804bb5eae2027` |
| libgomp1 | 14.2.0-4ubuntu2~24.04.1 | amd64 | 148062 | `e8a95ec58125b4933597f30ff56c2ae10edf90f287262e366d4b6edea3019144` |
| libgraphite2-3 | 1.3.14-2ubuntu0.24.04.1 | amd64 | 73352 | `2413ea0f6670d6610ee7c2f550551d69fa341f4b9c78e20eb397f0d1bbe914cb` |
| libgsm1 | 1.0.22-1build1 | amd64 | 27810 | `8cc765587a3275e861f166a98b4b79dd8d5c673f97e17967945ab4d1eb89c89a` |
| libharfbuzz0b | 8.3.0-2build2 | amd64 | 468874 | `d6f7eea2244f98aa0463a056680d4629476bf624767ede301560e24add686b5c` |
| libhwy1t64 | 1.0.7-8.1build1 | amd64 | 584050 | `c092b1ea155d81a5985073f867e1479c48c5c6efe4adc1bfcdde27e5d2b8b89d` |
| libiec61883-0 | 1.2.0-6build1 | amd64 | 24546 | `a47c6ec10068f1fc37a334949f6174f7ecbf0889bfde9d36baea8d403d76a727` |
| libjack-jackd2-0 | 1.9.21~dfsg-3ubuntu3 | amd64 | 288676 | `4fc5f4d0fbf2602df59bbed3ea504742bdcc7f899d7b32f6b2fd446ceb1fdf7d` |
| libjxl0.7 | 0.7.0-10.2ubuntu6.1 | amd64 | 1001370 | `dc4a6436f7ab5e887c157d70a2784494be0db43b55fbb3a00d87d6385057f51d` |
| liblapack3 | 3.12.0-3build1.1 | amd64 | 2646422 | `05ced5049d4fd393e0ae6dc71211e7c07ed0c85e8099649d679cf4b5a1aed59b` |
| liblcms2-2 | 2.14-2ubuntu0.1 | amd64 | 160898 | `f48368ce55ac35c4723a158c74f2c48f4398570d6f3207874e3a14e1cdada71d` |
| liblilv-0-0 | 0.24.22-1build1 | amd64 | 40976 | `5fd6b6861a021e8148c9086d36b70eea28a503dd4c2f2b922f56789d2967d0ad` |
| libllvm20 | 1:20.1.2-0ubuntu1~24.04.3 | amd64 | 30574382 | `f018e71c72c7a4742fb1aff9dc98b45586f42e6ea464d4918af7e3772b7f0658` |
| libmbedcrypto7t64 | 2.28.8-1 | amd64 | 209262 | `1f0b795a2274f68edddaf2567d738fcee43383c2c4ba802d54ba3b710bbb3228` |
| libmp3lame0 | 3.100-6build1 | amd64 | 142342 | `d739e9a3565f98b8b72a9e203a9a537bd1090af6cafb94c98adf8e27f9d3fb94` |
| libmpg123-0t64 | 1.32.5-1ubuntu1.1 | amd64 | 169494 | `bbe32128e3358f141f9f58ef6e76a7bef3e07d09165203665d30fdc27372b418` |
| libmysofa1 | 1.3.2+dfsg-2ubuntu2 | amd64 | 1157872 | `a3b5bb4bcc6ce996700f0120722b1c10af08e9dd1e0d497782edcea90079d4af` |
| libnorm1t64 | 1.5.9+dfsg-3.1build1 | amd64 | 153694 | `0ff2047ae4ccf2d1b5f338922f98a8652344ee1c0f18dc80a419753d06582cc6` |
| libogg0 | 1.3.5-3build1 | amd64 | 22686 | `fbdb2fedbafb02c3f07f2c2ddeb4018b5e94e13462f93fbececdcefc06fe2ac1` |
| libopenal-data | 1:1.23.1-4build1 | all | 160710 | `0de2f2b7a29fa337432fa044ce6c238bc511b83dac14d68a32673d9803cbab83` |
| libopenal1 | 1:1.23.1-4build1 | amd64 | 540202 | `1781774a540ab7c9baccc41b49592e3b427c5820c64e390af4868d6a861ef8c7` |
| libopenjp2-7 | 2.5.0-2ubuntu0.5 | amd64 | 173572 | `24b92ce9c4b306f5370e768024d35ec565792001678b510f0d40b6395b43524f` |
| libopenmpt0t64 | 0.7.3-1.1build3 | amd64 | 647356 | `512814f1ebdf8fe31b8185826ba08f7933998b7a7ac1599fac39717dbaaae6ff` |
| libopus0 | 1.4-1build1 | amd64 | 208122 | `6d8da32a832e655c9603543b47a0bc90c6f60bdee0c00fe7499bd50acad566cf` |
| libpango-1.0-0 | 1.52.1+ds-1build1 | amd64 | 230606 | `09dfa5c881ab273ec6bc1830adcefdc407fcd619316e68fb26ca3a23d0fc9f54` |
| libpangocairo-1.0-0 | 1.52.1+ds-1build1 | amd64 | 28828 | `98cf5fc9076c2911fe20dfc1e41cd8f686a2bb5d539b1105fab18105b0db1376` |
| libpangoft2-1.0-0 | 1.52.1+ds-1build1 | amd64 | 42532 | `cc6bc9d86ef5f329edacaa4895232d8a9b8f95c5cbc77c3c460a3f64cecc213d` |
| libpciaccess0 | 0.17-3ubuntu0.24.04.3 | amd64 | 19220 | `87a3b1c2a09f54d229e5e1f5424adc806e8f99bbb8aa8c8dea114b727f7a5a96` |
| libpgm-5.3-0t64 | 5.3.128~dfsg-2.1build1 | amd64 | 166620 | `9382625866a207b3da34f541799d10203a5693e5928c8d08f6d10be1c545d0a5` |
| libpixman-1-0 | 0.42.2-1build1 | amd64 | 279306 | `d9c2931c4c424615eeab9dd5ae08bfc608b84e803c1d3ccddf319270db213421` |
| libplacebo338 | 6.338.2-2build1 | amd64 | 2654026 | `9c173df229673887df3cce7d4a26de15f7477a62cd1391f9a88c346018ecad1f` |
| libpocketsphinx3 | 0.8.0+real5prealpha+1-15ubuntu5 | amd64 | 132898 | `26987384225666ec8d065fa05e484be9cd5967e28ac3bda20d1f818617e2b930` |
| libpostproc57 | 7:6.1.1-3ubuntu5 | amd64 | 49884 | `f0984bf6bff85dc29a44f650841b01a0594775f628b7a4bf5dd45d85d82cb2a7` |
| libpulse0 | 1:16.1+dfsg1-2ubuntu10.1 | amd64 | 292098 | `b8d52aa6c2f74ae99e749c9f98d6ad2dbf924fd422371ac469725245cdd919fc` |
| librabbitmq4 | 0.11.0-1ubuntu0.2 | amd64 | 38306 | `37a300e68d3de6e60c98969d561adf718e3eddc388a0b4d922ce15f391fe3a22` |
| librav1e0 | 0.7.1-2 | amd64 | 1021698 | `be10d7e2dc028d9d7791997a8710b7a9a3147151613750f0f08963fbe9ac993c` |
| libraw1394-11 | 2.1.2-2build3 | amd64 | 26192 | `0a6c9d979bd0301175340546a832c6dd4e94164eff9c4bcabcae97fb0dcf215b` |
| librist4 | 0.2.10+dfsg-2 | amd64 | 74894 | `c8050889949e75064db8c9c25c7e41d4d2a79be7a60e6537f9d9ab54774495dd` |
| librsvg2-2 | 2.58.0+dfsg-1ubuntu0.1 | amd64 | 2140940 | `47aa51a12ecc102b6dc867fe34319fcf6fa590c44bd8a3e22a2c473f3b8dd875` |
| librubberband2 | 3.3.0+dfsg-2build1 | amd64 | 129894 | `96ebaf773ad933a5269859cb3b09488b7b0b58387ebfbd7b7c469dd2b32d5d8b` |
| libsamplerate0 | 0.2.2-4build1 | amd64 | 1343954 | `8a389d1cae81860c9fb7cd9641c3f9d38815e80c0379b3181b4317489126f738` |
| libsdl2-2.0-0 | 2.30.0+dfsg-1ubuntu3.1 | amd64 | 685580 | `a2e733f1339272f22cf7777d454b5d4f71e27335d89d4c99cf41e1ce1bb5c462` |
| libserd-0-0 | 0.32.2-1 | amd64 | 43568 | `5ede35e66656c136b6a803aebeac92b2c061ec4d147820a730dd9c2a1315f57a` |
| libshine3 | 3.1.1-2build1 | amd64 | 23170 | `88a6e24ebb4be1c085acadb7778076de6980fa7e63b82570e29cc0f8a17a516b` |
| libsnappy1v5 | 1.1.10-1build1 | amd64 | 28634 | `c44dfa3b8be7c2873efb770890cb282a36a7e59b3b1900cb50d99f92e6be2da5` |
| libsndfile1 | 1.2.2-1ubuntu5.24.04.1 | amd64 | 208508 | `fe2461b27e747fa88ede157ea39c6379e2101c280a730cda9c825f566a3808e5` |
| libsndio7.0 | 1.9.0-0.3build3 | amd64 | 29562 | `b3944745ee48ea2c7a851fb78a617d34bb731a2f956a2c213cc9dffcaba35382` |
| libsord-0-0 | 0.16.16-2build1 | amd64 | 15820 | `a330fa014ee2eb48053395fb91fa39d95ca910e582d515254f50ef944240303c` |
| libsoxr0 | 0.1.3-4build3 | amd64 | 80038 | `2ddb6d21ad8ae0c850d380b551f109fc6b50c9a9b62c64ce37607c115bd3382a` |
| libspeex1 | 1.2.1-2ubuntu2.24.04.1 | amd64 | 59578 | `12d57caab1e3fa847cd385a21c1044f7d38209e97b0f87372fe41e346c2c17f7` |
| libsphinxbase3t64 | 0.8+5prealpha+1-17build2 | amd64 | 125618 | `80c184d04290cd1a55d6abac7c4b9f15bbf736e43a410857f992eba16b005a4c` |
| libsratom-0-0 | 0.6.16-1build1 | amd64 | 17312 | `a103c98906a7587718dabd52dfcd0af2dff6ff567781100bc8cf07de05bfc30d` |
| libsrt1.5-gnutls | 1.5.3-1build2 | amd64 | 316428 | `77bf75aaa74be9552d5a3b05349d15dc6e00f3a854a12ad0dfffea0df3bd6523` |
| libssh-gcrypt-4 | 0.10.6-2ubuntu0.5 | amd64 | 225808 | `ae5179eb5b44c5e258d5bec75dca8578064c0a0956dd0402762e65c8a7b9e208` |
| libsvtav1enc1d1 | 1.7.0+dfsg-2build1 | amd64 | 2424900 | `bac5f768bff928e85cecb39da8e37276a579639ef884692b089ed2de6c1cd2ad` |
| libswresample4 | 7:6.1.1-3ubuntu5 | amd64 | 63782 | `d55afe23e07b02b2def1647181a727dfad996001f72b5172da8a99691c3fa208` |
| libswscale7 | 7:6.1.1-3ubuntu5 | amd64 | 192938 | `2c17ae58b35112aace5179d7deea51faa20e82e04847b257f339376b11744e22` |
| libthai-data | 0.1.29-2build1 | all | 158016 | `6035d4a714290b97816bed7f3bf64921c6358ddb84d1dacdddccdc1cf39bffb7` |
| libthai0 | 0.1.29-2build1 | amd64 | 18914 | `5ada7045c5f84a4b774e6449151800aa6470e8d01dc9d6124ef4a44ae6af5508` |
| libtheora0 | 1.1.1+dfsg.1-16.1build3 | amd64 | 211470 | `8514bf3bb1496516b3bd0b2a5fc3ac9ea061e36360dcdfde52d51fa01c437b05` |
| libtwolame0 | 0.4.0-2build3 | amd64 | 52338 | `0dc3f4ae19623d92eb2c7a131737355cc385047fb8891a6ae380e4884e3442c0` |
| libudfread0 | 1.1.2-1build1 | amd64 | 18980 | `9340c4bdb5954ed98a81eb7979c25187d0c4d95b633cac29eefe1f5b31c05162` |
| libunibreak5 | 5.1-2build1 | amd64 | 25012 | `62576c95652b6c0a02e3d0fe9e2da6d0666c375d71c43da9d884c77417956ae4` |
| libva-drm2 | 2.20.0-2ubuntu0.2 | amd64 | 7124 | `7919c889e0bc8221dcfb595bc0707959354a5404cc1d9b818f85fc43914ca0d4` |
| libva-x11-2 | 2.20.0-2ubuntu0.2 | amd64 | 12028 | `9b13aaf035c6b607885ffaa6688866cf3cb720637909c7c0c9d3bedcd1d723cf` |
| libva2 | 2.20.0-2ubuntu0.2 | amd64 | 66362 | `a8ff8d3d16dba715cd24d67c0f8787016194fcee0dee20344a92d057577c0b40` |
| libvdpau1 | 1.5-2build1 | amd64 | 27836 | `a071370329f6dfb954e0cd2ed28cadc17fbf1bdf30f6d1db1e178ad350844fb9` |
| libvidstab1.1 | 1.1.0-2build1 | amd64 | 38550 | `345caabe592ee7733c2142bdfb3fe18a97da4ee7372f57d574b698d58e3fd431` |
| libvorbis0a | 1.3.7-1build3 | amd64 | 97554 | `d704ac0a5dd7a9797a2f2dc86fc8333c9973023b98b3510dcd2f1cc00da3624d` |
| libvorbisenc2 | 1.3.7-1build3 | amd64 | 80818 | `866c561fe81d4430a48c820f4e62f3cd138f4c8c7664f9203f02c1ced2eb55d4` |
| libvorbisfile3 | 1.3.7-1build3 | amd64 | 17092 | `fa12c52814bbfddff78186e7300b535bed30db02e4532fafae969aa9e5a79316` |
| libvpl2 | 2023.3.0-1build1 | amd64 | 99802 | `4f5d05ce3abb95a45d7b0d1e78e137f2849548dd461feb12d9066d550da0ca70` |
| libvpx9 | 1.14.0-1ubuntu2.3 | amd64 | 1143252 | `a8555944c01f7308f3436b6f109328ebe680969e72066eab0a1f4c82a95e1740` |
| libvulkan1 | 1.3.275.0-1build1 | amd64 | 142010 | `ccf4fe8f4461442f27ea2494c7ae650b60bd396fec2688b0c44a27d66a222f74` |
| libwayland-client0 | 1.22.0-2.1build1 | amd64 | 26406 | `6af0aed41d75149bea22fa468f01eb058ffe3e35ef07ff2f13fb88a90387881d` |
| libwayland-cursor0 | 1.22.0-2.1build1 | amd64 | 10406 | `29c20da27e8e37c984cf0518635c11cd0441f4cb6a2919a9f6eee1809171d327` |
| libwayland-egl1 | 1.22.0-2.1build1 | amd64 | 5628 | `76cd17d2776428e0a0551b8f8fd954eeb3d39ca8ed2f88a38a5e4ce3ce3b2f16` |
| libwebpmux3 | 1.3.2-0.4build3 | amd64 | 25674 | `0dd0f2de0fbbda7239209332ddfe512f4383d0489dd3f9fb25398f58c1621724` |
| libx11-xcb1 | 2:1.8.7-1ubuntu0.1 | amd64 | 7796 | `5ce7093e58aaaf753ed0904351a4e07f209e0c282ee8ef4f5f7988d0b40dccc1` |
| libx264-164 | 2:0.164.3108+git31e19f9-1 | amd64 | 603814 | `7a410be6797c32357a2b7f0cc68ee14ae0e3b652bb17d16cdc941e4d830c7842` |
| libx265-199 | 3.5-2build1 | amd64 | 1226422 | `795a6ca9287cd521882178d6503703ee8f0cde955f0ffc03e5ceee9f6d084de7` |
| libxcb-dri3-0 | 1.15-1ubuntu2 | amd64 | 7142 | `3d3d0e95e56ae16010a84883196b8153cba62d6831f9aaee2a68c46f20c7c004` |
| libxcb-glx0 | 1.15-1ubuntu2 | amd64 | 24774 | `d07cde26c70fe92df7c327928371e495cc4570fed2d6518758a5db5715cb34c1` |
| libxcb-present0 | 1.15-1ubuntu2 | amd64 | 5676 | `2e36e6557e87dbfd64df3143cbf81a4f75c3447501a545b4730511d228b0dd17` |
| libxcb-randr0 | 1.15-1ubuntu2 | amd64 | 17862 | `b5cb519823a05a617b543dc9b6a9289b7648b20048244abe021caa706309835a` |
| libxcb-render0 | 1.15-1ubuntu2 | amd64 | 16230 | `7d83a0668b1a693b5ea2e496206e8371fb6beda4c54a2b4b3902005915c272c5` |
| libxcb-shape0 | 1.15-1ubuntu2 | amd64 | 6100 | `ad57fed687ec9c2a4808b8e30ab1753d57daeddc9ad7fb883bfa0701f6da57c8` |
| libxcb-shm0 | 1.15-1ubuntu2 | amd64 | 5756 | `229d1280d459f1ba44c22939d3f9b61d9d20932d9a646b3fe4ce50be4cdf2325` |
| libxcb-sync1 | 1.15-1ubuntu2 | amd64 | 9312 | `14286795a258593259923619073e50c15345673befae2733f7b0577b07359aa8` |
| libxcb-xfixes0 | 1.15-1ubuntu2 | amd64 | 10190 | `0b2ab64af92a71e3d1a35e3c819880ee28d04bfac81360df68ae8d8e9663ebd2` |
| libxcursor1 | 1:1.2.1-1build1 | amd64 | 20748 | `1a4beee621454e42fe0d2f4e1633611126e43d891f0aec28e4a06744f6e9f04d` |
| libxfixes3 | 1:6.0.0-2build1 | amd64 | 10764 | `0ee1015cccd063249e01c0cd0bf45f513c8ac9a1e5e485070c12e69192455e4a` |
| libxi6 | 2:1.8.1-1build1 | amd64 | 32404 | `0ea7acb5e8a8ce4d6653b30e03a319bfe136db7bfb5ee7cad34c2aa1272ea8d9` |
| libxrandr2 | 2:1.5.2-2build1 | amd64 | 19670 | `f2955a5e594f5724b58ad241d9231ea191cb36574a0d5e5ca6b661cd41d6256d` |
| libxrender1 | 1:0.9.10-1.1build1 | amd64 | 19016 | `d70bd831aebe8d4834b5dd2ed98df26dd6bd27f1042c47543bd7f66df1ae22ea` |
| libxshmfence1 | 1.3-1build5 | amd64 | 4764 | `bc6de4bfaf9050a8ba83d4bcfb114131b081084a7972c03417f8523d52b5c742` |
| libxss1 | 1:1.2.3-1build3 | amd64 | 7204 | `0ac60a2cc034ccc4bf4e2f846f38110469d7ae43b47e808d67aafc538bc3695e` |
| libxv1 | 2:1.0.11-1.1build1 | amd64 | 10672 | `ddf5c60268d58fe0c57e80f241d6fa5e73ae2096a137c4d2de42f97cb379ece6` |
| libxvidcore4 | 2:1.3.7-1build1 | amd64 | 219400 | `4f70e6d3db3d3069797d84bccb082dbf6b14d6e837cbef6197519d8be8779cdd` |
| libxxf86vm1 | 1:1.1.4-1build4 | amd64 | 9282 | `48b2799f52b86505d2bc91dcb1bfa6536545635b9849cb693d274d934116a8da` |
| libzimg2 | 3.0.5+ds1-1build1 | amd64 | 254226 | `6c3d3e87277ff906011da1ceac5e44057df3710a53df3eab59675b02eea1ff12` |
| libzix-0-0 | 0.4.2-2build1 | amd64 | 23570 | `3bd3271411e9f25d03e16ca430da9551200b28e854ba9b606f819c23a4dc4992` |
| libzmq5 | 4.3.5-1build2 | amd64 | 260300 | `fff197d2a164e3ec83fef23afd55bad93971b3327d7353db9a305a199d55d402` |
| libzvbi-common | 0.2.42-2 | all | 42360 | `c099fcdaaa1f0ccfb0551470faae76f43798150809e07ff8a08f4743fc1a8074` |
| libzvbi0t64 | 0.2.42-2 | amd64 | 260558 | `0a596a628331e837b5756dec8bedf69ea699a9ef5d8a050c0c9b471f833039e1` |
| mesa-libgallium | 25.2.8-0ubuntu0.24.04.4 | amd64 | 10783312 | `0c50c3c18c1b3f3ac4243bbf1533c9202f9d7b4f646807e9ef0df4c801414256` |
| ocl-icd-libopencl1 | 2.3.2-1build1 | amd64 | 38530 | `6d98029b06335bc23166834719ac5e697bcb4a136b40eebc263dfe481e05839b` |
| x11-common | 1:7.7+23ubuntu3 | all | 21748 | `b545fa5196dd7467ba3770d6ce575abcd7941071961ea9f7535a319d9d20fd46` |
