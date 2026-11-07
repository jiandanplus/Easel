# AV 源码获取工具实施账

本批执行既有剩余音视频交付方案中的完整源码准备步骤，参考 `outputs/_run/central-av-source-acquisition-next-review-20261028/report.md`。不改变产品功能、前端文案、供应商配置或资金规则。继续当前分支，不提交、推送或合并。

## 交付要求

使用标准库实现可审查、可重复执行的离线源码校验与安全提取工具；在线获取仅访问固定官方公开仓库，不使用账号凭据。先完成有意义的失败测试，再实现，随后独立审查。生产依赖安装、镜像拉取、构建／代码生成和部署属于后续步骤。

共同约束：压缩／网络输入128MiB总预算、单响应或pack64MiB、HTTP及单个Git对象metadata4MiB、tar及其压缩容器头部合计8MiB、单blob／普通文件16MiB、完整文件内容512MiB、最多30000成员；每请求20秒、整批600秒、最多64请求。数量／字节在读取过程中约束；不能以事后大小检查替代。提取、Git对象处理在后续受限离线进程运行，AS512MiB／CPU30秒为候选运行门，未实际实施时不得记为通过。临时目录排他创建0700，文件0600，失败保留、不得覆盖旧目录或自动重试。

2026-10-29技术修订：原root把所有metadata统一设4MiB，实际已签名FFmpeg归档10525个raw头本身就有5,388,800字节，原值与完整源码要求冲突。基于只读实测，将tar／压缩容器metadata单独改8MiB；不是用户商业规则或安全隔离授权变更，不把新值通过倒写成旧值通过。HTTP和Git对象metadata4MiB、其他资源限制不变。既有原4MiB反例及失败均保留；新增同规模合成兼容测试及显式低限额拒绝测试。

## Task 1：离线归档检查与提取

所有权：`services/platform/scripts/av_sources/safe_archive.py`、`services/platform/tests/test_av_source_archive.py`。不编辑其他任务文件。

API由实现者在报告中固定，至少提供两个清晰入口：只读检查归档并返回清单；在新目录提取已验证归档并返回文件清单。输入需显式带archive size和SHA256 pin及限制，不隐式信任文件名。支持本批FFmpeg实际使用的tar压缩格式，避免`extractall`。

- 核实际size／SHA，拒绝非普通文件和源symlink；读取中的压缩、展开、成员和单文件预算均有上限，PAX／GNU扩展、稀疏和特殊成员不能绕过。支持合法的顶层目录及普通源码文件；目录和父目录不跟随link。
- 路径拒绝绝对、`..`、NUL、规范化碰撞／重复、越界嵌套、设备／FIFO／socket、hardlink、setuid／setgid和未支持扩展语义。symlink若不安全支持，应明确拒绝，不悄悄漏掉文件并称完整提取。
- 所有成员和实际数据验证通过后才创建destination；写文件O_EXCL、O_NOFOLLOW，owned目录0700、文件0600，不恢复UID/GID。源码可执行位在清单记录，提取／检查阶段不执行。
- 保持原归档不变；清单含各文件SHA256、大小、原mode、目录与普通文件数。源在检查／写入之间发生变化时拒绝；不能把检查旧文件的结果套到新文件。
- 使用真实合成tar和临时目录测试：正常tar及压缩tar、错误size/SHA、截断、压缩炸弹预算、单文件／总量／成员／深度上限、路径穿越、重复／碰撞、source／destination／parent symlink、hardlink／特殊成员、特权位、已有目标不可覆盖、异常后原文件保全。测试不得访问任何真实账号、数据库、网络或用户工作区。

报告：`/private/tmp/easel-av-source-task1-report-20261028.md`，保存RED与GREEN原始日志和精确命令／源码SHA。最终只回状态、测试计数、接口和顾虑，不提交代码。

## Task 2：离线Git对象完整身份链

所有权：`services/platform/scripts/av_sources/git_objects.py`、`services/platform/tests/test_av_source_git_objects.py`。不编辑其他任务文件。

提供纯Python `verify_commit_tree(commit_oid, read_object, limits=...)`，loader回传`(type, raw_bytes)`；结果是完整、可序列化的路径／mode／object ID／SHA256清单及gitlinks，不能执行源码或访问网络。实现者可采用dataclass或dict，但必须在报告固定接口。调用方负责另行验证签名tag绑定，不能把commit/tree哈希自称签名。

- 原始Git对象ID按 `type SP decimal-length NUL raw-bytes` 重算；严格验证目标commit、唯一tree header、tree原始mode/name/NUL/20-byte ID、全部目录及blob链。拒绝重复／规范化冲突、不安全路径、未知mode、错类型／坏hash、缺对象、循环、超限、非canonical或格式不完整对象。不要从规范化JSON补造原始commit。
- mode100644／100755均为普通blob；120000是原始symlink target blob，只记录并核其hash，不跟随／物化；160000是gitlink，记录固定commit但不向loader递归请求子模块。tree排序遵循Git的目录后缀规则，必须验证原始顺序及规范mode。
- 针对真实合成Git原始对象运行正反例；包含commit消息／时区完整保留、blob篡改、父tree改mode或hash、错类型、截断、重复／不规范排序、危险路径、总成员／深度／blob／总字节预算、symlink与gitlink区别。无需签名或数据库来测试对象算法，不能把synthetic当真实上游验收。

报告：`/private/tmp/easel-av-source-task2-report-20261028.md`，保存RED／GREEN日志、接口、源码SHA与限制。最终不提交代码。

## Task 3：固定仓库有界获取与集成（root）

先读取当前官方Git pack协议和index-pack文档，冻结协议实现和环境。采用固定GitHub official mirror的smart HTTP公开端点，或经证明等价且有界的既有工具；不能无界clone。禁止凭据、代理继承、任意redirect、submodule递归或仓库执行。固定libplacebo commit `275f7dea27924cdd34ab2e6c20e448b6bc4fab65`、配对Headers commit `74d8a6cb930c68ef617b202c3ff3c59d919e086b`；target须与固定公开引用／签名链一致。

单独测试packet framing／流式响应预算、错误／截断／超时／sideband错误、原始pack完整性及隔离处理；不得先进行真实上游请求再补测试。集成Task1／2，以真实小型合成仓库和归档证明完整链。实际154执行前固定脚本／输入载荷／主机与资源证据，按现有授权及安全审查进行；不扩大先前用户对F2材料上传或真实数据的授权。

## 当前进度

- Task1：首轮37项后独审发现3主项／2次项，统一修复后69项通过；第二轮限定规格／质量复审PASS，另16个独立观察通过。GNU普通格式与8MiB头预算的同规模合成兼容通过，未提取真实源码。
- Task2：初轮58项，独审2P2后先RED12／GREEN71；第二轮独立4个反例重放与规格／质量复审PASS，尚非真实上游签名／源码验收。
- Task3：有界v0协议34项含2个真实本机Git回放；HTTP首轮31项后独审发现3类缺口，统一修复新增25回归，协议／HTTP组合90项通过。第二轮限定规格／质量复审PASS、另10个独立检查通过；原FAIL和连接中断保留。仍未实现Linux资源／网络隔离及完整真实来源链的执行器，未在线取完整源码、构建或采用。
- root完整六文件组合：230 passed，0 failed，3.12秒；实际本机Python3.12.7，10个源码／测试输入前后逐SHA相同。命令与原始日志在 `/private/tmp/easel-av-source-tooling-full-20261029/`；这是离线工具组合，不是中央全量或目标Linux／真实上游验收。各任务／独立计数不与230相加。
- 154仅完成只读预检：已签名归档SHA保持；raw GNU头10525、普通文件10399、目录126，最大文件949519字节，无链接／扩展、展开流98703360字节。目标有Git／Python3.12.3／gpgv／unshare／systemd-run／prlimit，systemd255.4。采样资源与probe自己的AS/CPU设定不替代后续执行门；没有提取、安装、构建或生产切换。
- 全量发行、生产AV、真实供应商／恢复与永久工作区迁移等剩余目标保持原交付总账范围。

## 2026-11-02 Task3 增量（保留上列历史结果）

共享预算协调器与76项纯回归、独立代码审查已完成，原保护输入字节一致。Root冻结本机一次公开匿名获取后，固定两份pack候选首次实际取回（4请求，10.45121秒）；完整安全记录见 `docs/validation/2026-11-02-av-public-source-batch.md`。这只更新“尚未在线获取候选”的历史状态，完整来源认证/pack对象loader/tag签名/tree/gitlink、Linux硬隔离、原完整工具链/AV19和生产验收均未关闭，不把候选当完整源码或构建输入。
