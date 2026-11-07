# FFmpeg 9.0.2 构建准备：实际只读预检与离线方案

当前结论：上游 RSA 归档来源认证已通过；编译环境、依赖和运行能力尚未验收。本轮没有提取完整源码、选择或拉取镜像、联系 Docker daemon、安装编译器、编译、部署或重启服务。

来源见[实际 RSA 验签记录](2026-10-28-av-upstream-rsa-source.md)。服务器保存的归档为 12,040,788 bytes，SHA256 `8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e`。本轮只从已验证归档将 configure／INSTALL.md／LICENSE.md／RELEASE 四个公开文件读入内存，取得小字节副本；不是完整源码文件系统提取或安全提取器验收。configure SHA256 `5b281b249ad5ad859030b4da0a24ec105b5abff7b3d87a65b302df8bf5fd9833`。

实际 154 服务器只读预检：Ubuntu24.04、x86_64、4CPU、MemAvailable 2,969,522,176 bytes、无 swap、可用磁盘 41,182,883,840 bytes；这是采样时快照。gcc／g++／make／pkg-config／nasm／yasm／ld／ar／readelf 均不存在。patch2.7.6 和 Docker29.9.2 的本机 `--version` 可运行；没有访问 daemon 或业务容器配置，不据 CLI 版本推断实际 storage／cgroup 能力。回执 SHA256 `34ff15091178a523e93002ffd5efe431078631e02b359d2e5f04df0367ddfb80`。

离线方案根据旧两个归属明确的 ELF 内相同 76 个 configure 参数，对照新 configure 的真实选项集合，74项静态可接受，两个变化明确保留：原 `--disable-omx` 已无对应选项，拟记录为上游移除后的禁用空操作；`--enable-libglslang` 也已移除，源码支持 `--glslc=glslangValidator` 的候选映射。后者必须在实际 configure 证明 spirv_compiler 和相关能力启用，再核原 filter／profile，不能删除能力或凭参数名宣称等价。

新源码要求的 libplacebo≥7.351.0、Vulkan headers≥1.3.277 及较高的可选1.4门、shader 工具和传递依赖，尚未由已签名 Noble 包版本库存证明满足。没有假定现有包足够，也没有混入其他发行版二进制、降低版本要求或禁用 ASM。普通6.1.1基础包的安全安装阻断保留；新主归档的签名不证明所有源码、外库和构建产物没有漏洞。

拟议隔离编译环境为固定官方Ubuntu平台manifest digest的临时 builder，dev包只在 builder 内安装，真正编译断网／nonroot／只读rootfs，初始make-j1、CPU≤2、RAM及memory+swap≤2GiB、PID128。最终候选是受控私有 prefix 的 direct ELF 与完整 DSO／数据闭包，API用户无 Docker/socket 权限，运行不依赖容器。镜像和依赖锁尚未产生，方案不能执行。构建2GiB不改变既有运行512MiB AS／30秒每child CPU／共享60秒wall及900秒媒体上限。

方案区分硬限制和预算监测：source／work／out、image cache为预算，1秒监测和保留余量不等于硬聚合quota；不得为此改宿主fs、daemon或新增挂载。未来应先取得有界官方OCI／Ubuntu签名包元数据和固定daemon storage/cgroup子集，锁定来源、大小与依赖，再审构建动作；当前尚未执行这一步。完整 codec/profile、SBOM/CVE／许可证、实际API UID／systemd资源合同和生产发布各自保留待验。

小证据共12文件／433,205 bytes，已逐字节和SHA复制到 [持久准备目录](../../outputs/_run/central-av-build-preparation-20261028/copy-manifest.json)，manifest SHA256 `7ef56612de29870fad423bde916de3adccc7ee465f9fb75ed10e01b2e2644b2e`。[离线完整方案](../../outputs/_run/central-av-build-preparation-20261028/plan/brief.md)、[76参数映射](../../outputs/_run/central-av-build-preparation-20261028/plan/mapping.md)与[实际预检](../../outputs/_run/central-av-build-preparation-20261028/server/build-preflight.safe.json)均为准备证据，不是已构建或可安装证明。本机仅存公开小材料，没有供应商密钥、业务配置或真实用户数据。
