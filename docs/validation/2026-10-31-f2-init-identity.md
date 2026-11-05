# F2 Linux 启动身份诊断

本次完成了一次本机、公开材料、独立容器的 PID 1 元数据诊断。它确认原 F2 执行前检查的卡点，未执行或通过原来的八个进程用例。没有修改产品界面、业务配置或原工作台，也没有操作远端服务、账号、钱包、模型、付款或备份定时器。

## 实际执行与独立核验

执行器为 `once_probe.py`，SHA-256 `3af9b4e714915be4ec5b85b102c5ba47d342c659c892fea44c9d3a209f93dee0`；代码包 manifest 为 `84780f7de9a38c7c599234f5914eca2159f21b77fd86b20fff6f2b70c547b7fa`。独立代码审阅报告为 `70c5c5fe7f41c672b5fc5bcccc88ae67e3b88aa7fe7f3fcc4ab3941f868fac4a`。代码准备阶段的 22 项纯测试仅使用 fake API 和合成数据，不计为实际 Linux 验证。

Root 为这份具体载荷作单次本地准入，记录在 `outputs/_run/f2-linux-local-20261031/init-identity-actual-r1/root-admission.safe.json`，SHA-256 `d04ad5af7129403a9be000d0d51826d97411574844b3e6b4fc1ee3b5800cbfc8`。该准入包含原固定的只读 `sysctl -n kern.boottime`，用于绑定宿主启动周期。它没有授权原工作台切换、拉取镜像、清理或原八例运行。

本次仅创建并启动一次 CID `e9aa055f93143f0f3a9f244bba155d364049dbf7d1f0f87e51cf79b50617174b`，使用原有镜像 `sha256:4c6c0e157d95d71933b18ecb28d89ec8fea34b629b4a39732f75d45c6d36db36`。未拉取、构建、安装、重启或删除资源。新容器的资源、环境、标签和 never-started 事实先完整核验，再建立原 180 秒监护并启动。root、cap-drop ALL、NNP、none network、只读根目录、私有命名空间、CPU／内存／PID 和两个 tmpfs 限制保持不变。

执行器退出后，Root 在原 180 秒截止前另作两个只读 GET，检查固定镜像及本次 CID 的完整配置、exit 0／no OOM／Pid 0 终态、原 boot／monotonic wall／terminal／publication fence，以及唯一 DTO 的严格解析和保存哈希。没有再次读取日志。独立核验记录为 `root-fresh-verification.safe.json`，SHA-256 `a065ac23129ccfb729713bdca333a889c52e288e3cae23c858fd9b999e8f3f7b`。

## 观察结果和边界

- `comm` 为 `docker-init`，固定 `/sbin/docker-init` guest argv 前缀匹配。
- PID 为 1；UID／GID 四项均为 0；`CapEff=0`，`NoNewPrivs=1`。
- `/proc/1/exe` 的实际目标不属于本次预置的公开路径清单。安全结果只保存 `path=null`、长度 21 和 SHA-256 `dc19ffdd06fa8b715544609b7661b53bdef329a850e57076fe82cf2c9888731d`，没有 deleted 后缀。没有导出未知路径、原始 cmdline／status，也没有读取或哈希 executable 内容。
- `probe_complete=true` 表示本次元数据采集完成；`original_cases_executed=false`、`original_eight_accepted=false`。exit 0 本身不能替代 DTO 或原八例的验收。

该结果与先前 `init_prefix` 中 executable membership 检查拒绝的位置一致。guest argv 或 `comm` 不足以替代 executable 身份检查，也不足以判定具体仿真器或其路径。没有放宽原 whitelist、改写冻结 helper、延长超时或重放失败容器。

公开代码与仅允许的安全记录已归档到 `init-identity-actual-r1/`，29 项文件共 198,573 字节；归档 manifest SHA-256 为 `7e95840bc6738cdedee342176a8b8a7e8ee4779dab85a6c39e901f224f690602`。原 watchdog 原始日志没有导出，失败环境继续保留。

## 后续验证环境

后续可在本机原生 ARM64 Linux 官方 Python 3.12.13 镜像中验证相同的 Linux 进程合同。该环境调整需要固定新镜像、完整派生配置及独立检查；不能把这次诊断视为原八例、x86、PostgreSQL、完整恢复或生产验收。

公开元数据已取得 `python:3.12.13-slim-bookworm` 的 index → `linux/arm64/v8` manifest → config 关联。固定 child digest 为 `sha256:3f9c7f75b916e8279e24136d7a57769aba9e193715be52570417698c52d6eed0`，config digest 为 `sha256:5f832b881814761acc99ff0b29e9273cb7277bc4026701423b7cc8fde9bb3d21`。公开 config 投影另行保存了字段存在性和完整 Env；缺省 User 不是实际 root 证据。没有下载镜像层或运行该镜像。

四层的压缩量为 45,068,039 字节，不能作为解压和 Docker 存储物理峰值上界。当前约 2.49 GiB 可用空间仅满足原小型运行的 2 GiB 门槛，尚未满足本轮建议的 8 GiB 镜像获取／完整验证门槛。用户已选择先释放空间；不重复请求，不自动删除其他材料。
