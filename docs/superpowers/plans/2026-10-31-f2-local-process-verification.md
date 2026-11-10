# F2 冻结进程实现的本机 Linux 验收

本项承接已批准的剩余交付目标与本机隔离验证范围，补齐 F2-r3 原进程 helper 的 Linux 证据。原准备包的远端154 proposal不代表已上传或已执行；本次只在本机 Docker，零远端、账号、密钥、数据库、供应商或生产动作。

原包位于 `outputs/_run/restore-capture-f2-linux-process-preparation-20261028/`。冻结 capture 50,023B／SHA `fc3fa9cbdaa751c8efeb7ddb5a40c69efa0b7a3a7675c4b3f7832210577ed261`，runner 8,716B／SHA `960b96e8d20146de5553a5a13769c108815d04cabbb7552d597416bc57981bca`，expected 2,718B／SHA `762c4473f71906c1fc78bfddcc3daac0210f69faf1a8637d1ae58e079d6fc895`。三文件原字节、8个场景与顺序、3s／0.4s调用期限、12s共享cleanup、2s组消失与16s单场景上限全部保留。

选择复用本机已审公开 image `sha256:4c6c0e157d95d71933b18ecb28d89ec8fea34b629b4a39732f75d45c6d36db36` 和 `/usr/local/bin/python3` 3.12.13；不pull／安装／构建新镜像。远端运行需另一具体授权，Darwin桥接不能证明本门，均不采用。镜像amd64运行在aarch64 Docker VM：本门证明Linux waitid／WNOWAIT与进程语义，不能写成原生x86性能。

精确资源：root UID0（原runner要求）、cap-drop ALL、NNP、none network、只读root、1CPU、1536MiB memory及memory+swap、PID128，私有PID／cgroup namespace，只有 `/tmp` 16MiB和 `/var/lib/postgresql/data` <=1MiB tmpfs。后者覆盖已实测的镜像声明VOLUME，防止自动生成匿名卷；零bind／volume／端口／Docker socket。host仍要求free>=2GiB、stage<=896MiB。host wall180s一次性捕获，不刷新boot或deadline；guardian为有限轮询停止机制，不称瞬时硬杀。

采用Docker官方 `--init` 为PID1回收孤儿。Docker CLI将该选项写入HostConfig.Init；这不替代原owner／unreaped／SIGKILL／唯一wait断言。[官方定义](https://github.com/docker/cli/blob/master/cli/command/container/opts.go)经本轮ctx7 library＋docs两条命令核对，无quota错误。原runner在同一carrier PID中runpy执行，必须PID>1／PPID1／默认SIGCHLD，PID1身份和固定argv前缀、实际cap／NNP、固定cgroup限额、两个tmpfs容量与flags均先证明；未知即失败，无自建reaper或兼容fallback。

CODEONLY carrier只做有界公开payload校验、tmpfs独占0600材料化及前／后固定安全frame，原runner报告不改。纯测试先RED再GREEN，不能执行原runner或模拟成Linux通过。独立审阅后Root另行决定唯一actual；执行器复用原字节generic guardian／fence，唯一必要启动差异为子Python显式-B。首fail／unknown／late立即停止，保留容器／全部材料，不重试、不清理。

接受必须同时有：exact新CID／image／payload／环境证明；exit0／noOOM／原boot和wall接受；原8个case严格报告及完整PID／计数／signal绑定。真正读取第1025字节才能证明stdout／stderr上限，不能以timeout替代。原approved=false／actions=[]不改；host授权与acceptance另记。

当前仅代码准备。成功只关闭F2原Linux进程子门，不关闭PG25006／source facts／F-W-P-D／完整mixed DR，也不增加媒体原37节点计数。B2原21selectors及39资源／认证等实际断言已经有证据，不重复运行充数。AV19、slow取消1、生产B3、支付／第二产品／永久工作区／签名公证／净机／真实IME／系统剪贴板等其他门保持各自状态。

2026-10-31 后续准入调整：reviewed002固定CID已经创建且never-started，禁止重放旧create／main。使用独立首次启动续接执行器，无create路线；fresh核旧三文件集／原handleSHA／完整配置和零启动证据后，首次建立原180s guardian，最多一次start。为本机实测的image-only厂商标签差异，单独允许exact `desktop.docker.io/ports.scheme` 在body未声明时缺省，实际存在仍须值一致；其余标签与profile不变。这是本项目明确准入选择，不声称Docker公开保证。Root两次只读元数据GET与RAM-only精确例外后的完整配置复核通过，安全回执SHA5a2b5503865ca907a7454990cf7c54b015e842041c92a154875ea6a8d16b99ad；它不构成启动或原8用例通过。

## 2026-10-31 启动诊断后的 native 环境准备

前述 amd64 环境的实际尝试已在首个 case 前被 init executable 身份检查拒绝；原 CID 和诊断环境均保留，不再启动或重放。单独的公开 PID1 元数据探针已完成，但不代表原八例通过；观察和证据见[启动身份诊断](../../validation/2026-10-31-f2-init-identity.md)。不根据 guest argv 或 comm 放宽原 executable whitelist，也不推测未导出的实际目标路径。

Root 已核定下一步采用 native Linux/ARM64 profile 的**代码准备方案**，补齐相同 Linux-process-only 子门。正式获取和运行尚未发生。准备材料位于 `outputs/_run/f2-linux-local-20261031/native-profile-codeonly/`；源码 SHA 为 `f31b2d7227bdff2967771d80346f470d984c69375393188e9ddc2b512d6ee099`，manifest SHA 为 `f6c20c6d00675e9e92bb1623a0ddc10836bf80fb130baf88596dd45cd1a54a12`。纯派生器没有 main、资源客户端、文件系统、网络、子进程或实际采集入口。

新 image acquisition 引用固定为 `docker.io/library/python@sha256:3f9c7f75b916e8279e24136d7a57769aba9e193715be52570417698c52d6eed0`，platform 为 `linux/arm64/v8`，config descriptor 为 `sha256:5f832b881814761acc99ff0b29e9273cb7277bc4026701423b7cc8fde9bb3d21`。原公开 index／child 真实文件及经验证的完整 config 投影已保存到 `native-public-metadata/`。manifest、config 和本地 Docker Image.Id 的关联仍须在获取后实际确认；公开 metadata 本身不证明镜像已经可用。

逐字段比较只产生六处 recipe 变化：Image、platform、recipe nonce、Cmd 的 nonce、owned nonce label 和 body hash。冻结三文件、原 bootstrap／payload、三条 Env override、全部 HostConfig、root／Entrypoint、两个 tmpfs、原 180s／2GiB／896MiB 要求全部保持。新 image 默认 Env 与原 override 精确合并为六个 key；Labels 精确为三个 owned label，不继承旧镜像的 `ports.scheme` 缺省特例。公开 config 中 absent User 不能证明实际 root。

recipe canonical SHA 为 `24b657385c7a922566cbf1a6d19ffa57b19647e73cfb517eb561e3e787dcfd2b`，expected canonical SHA 为 `6adf4d7a465a567b743cf65a3bb79b158977653753c7a90fa15389572b71a910`；native profile digest 为 `4076b2b273d5e5f58cefabed2813e9524ea98ff5309ffe2349c45af48a73736a`。重复 Args／Config.Cmd 实算 110,839B，在原 128KiB metadata 上限下余 20,233B，超过 16KiB reserve；这不是实际 inspect 响应大小证明。

作者先 RED（1 fail／16 skip）后 GREEN。Root 完整阅读新源码和测试后实际运行 17 项纯测试，17 passed／0 failed／0 skipped；独立审阅也完成 17 项并核对全部输入和派生输出，无确定 P1／P2。Root 纯核验记录 SHA 为 `d6e48089e3e6ee902e7f00d7227e4c4b30407574c6d94a40029991bc126e175d`，独立报告 SHA 为 `eed10101901b8cc80e36a53f982ddde2cae661089b50ad915631f9e64c541ac1`。这些结果不计为实际 native Linux case 通过。

下一次实际顺序如下，不能从本节推断已执行：

1. 重新核对宿主和 Docker VM 的实际可用空间。镜像获取门要求两者均至少 8GiB；原运行门 2GiB 保持。45,068,039B 压缩层量不足以证明物理峰值。当前宿主约 2.49GiB，等待用户已选择的空间释放；不删除其他资源或旧交付包。
2. 容量满足后，为准确 digest 的单次获取、只读映射检查及最终有界执行器分别固定具体动作。获取结果未知时保留状态，不自动换 tag、补拉或重试。不使用旧 CID／nonce／stage。
3. 采用**单个 exact-profile 容器先完成原环境 proof，再执行原八例各一次**。无需草案中可选的额外 startup-only 容器：原 `carrier_runtime.py:259` 已先调用完整 `capture_runtime_proof()`，随后才解码／材料化并在 `:266` 执行原 runner；runner 又先 `load_frozen()` 再进入首例。失败在首例前停止，成功后 `:271` 还要求环境 proof 不变。这一调整没有删除或替代任何原断言。
4. 最终运行仍须独立审阅的精确配置、fresh never-started 事实、一次 create／start、原 boot／180s guardian／终态／当前 publication fence 和严格 stdout 验收。未知、失败、超时均停止并保留；不拼接其他 run 的结果。

只有该 accepted actual 的八例完整通过才关闭 Linux-process-only 子门。native 环境不扩张为 x86 兼容／性能、PG25006、source facts、F-W-P-D、完整恢复或生产证明；不增加原媒体 37 节点计数。准备文件中的 `approved_for_execution=false`、`actions=[]` 和两原 case 标志继续保持。
