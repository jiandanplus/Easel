# Docker Desktop：B2 本地测试环境恢复候选

状态：用户已明确批准本次正常重启并核对恢复状态。执行前已保存141项B2源文件SHA与owned PG连接状态（无其他会话）；正常restart命令已发起，最终结果以本轮恢复回执为准。

本轮真实观察：已选 context 为 desktop-linux，DOCKER_CONTEXT／DOCKER_HOST 环境变量未设置；本地 daemon Unix socket 的 `GET /_ping` 在 3 秒超时，对精确 owned B2 PG 的只读 inspect 在 5 秒超时。PostgreSQL 回环连接仍可工作。以上区分 API 不响应与整个数据库已停，不据此声称数据损坏或缺失。原 T10 工具超时和 unknown 引用均保留，没有自动重提捕获。

用户 B2 授权明确限定本地开发与隔离验证、不维护真实服务；正常重启 Docker Desktop 会暂时中断本机已有 Docker 应用，因此需要这次具体确认。它不会操作远程中央服务、New API 或原17868工作台。当前可用空间约994MiB，4KiB私有文件写入／fsync／回读已通过；这不满足另一次桌面新包的2GiB门。

确认后的具体顺序：

1. 等全部本轮 PG／CLI 任务终止或记录为 unknown，保存 source pins、原始日志、拥有者记录及隔离测试结果；让实现者停止新的 Docker/PG 测试。
2. 先检查 Docker Desktop 自身可见状态。如能恢复 API 而无需重启，先验证健康与精确 owned 资源；如果 API 仍不可用，仅执行正常退出／启动 Docker Desktop。不得 reset、prune、删除容器／卷、改 Docker 虚拟磁盘或修改业务 restart policy。
3. 由原有 restart policy 恢复应用；不按旧历史快照手动批量启动任何容器。API 恢复后只读核对当前容器身份、状态和端口。若无法证明某项应运行，保持原观察并报告。
4. 本轮 owned B2 PG 使用 tmpfs，正常重启可能使合成库消失；保留原失败与身份，用新的明确 fixture 建库，重新生成纯合成数据，不把恢复前证据算作恢复后通过。原业务卷不得改动。
5. 仅对新 owned fixture 继续 B2 实际 dump、Linux 与完整 pipeline 验证；旧 unknown 运行不自动重跑，不回写原结果。失败时保存现场，再报告可确认的状态。

此确认只允许本机 Docker 恢复，不扩大 B2 到生产维护、真实密钥／账号库读取、远端 SSH、模型／付款、定时器或数据清理。


## 本轮正常恢复结果与新清理授权

用户随后明确要求删除之前测试中已无用的镜像与容器。本轮正常 restart 返回 Failed to stop Docker Desktop，进程仍存活；正常退出请求与菜单退出后，Desktop 状态、daemon 健康、直接 engine 健康和容器清单持续超时；镜像页显示加载失败。未执行删除、prune、reset、强制结束或远程操作。141 项源文件复核全部未变；新的纯 pipeline 35 项通过（4.37s），不代表 Docker / 实际导出 / Linux 恢复验证通过。

清理范围按最新用户请求扩展到已核实无用的测试容器及不再被任何保留容器引用的测试镜像；仍保留全部数据卷、业务容器、原17868用户数据、交付包、当前B2必要环境与失败证据。不得从缓存列表或历史快照直接推断 unused；live inspect 必须先确认精确身份、停止状态、挂载和归属。仅逐个精确ID删除；不使用强制删除、系统 prune 或卷清理。镜像删除须在容器清理后重新检查引用，基于共享镜像的测试不构成删除共享基础镜像的理由。

强制重启并未获得此前“正常重启”的授权。已单独提出具体范围确认；在回答前不强制结束 Docker 进程，不将恢复标为完成。回执：outputs/_run/b2-development-20261030/docker-recovery/normal-restart-attempt.safe.json。


用户已在后续明确批准受控强制重启并清理测试资源（call_ccd6f25e7a8642419673395591b51ba5）。仅处理实时核验的 Docker.app 自身进程，先 TERM，等待后必要时 KILL；保留系统网络 helper、全部 Docker 文件、容器和数据卷，重新打开后以实际 API 与容器状态验收，不使用旧清单恢复。精确进程动作保存在同目录 force-restart-processes.safe.json。此授权不扩展到 Mac 重启、数据重置、卷清理、业务容器删除或远程维护。


## 恢复与清理验收完成

受控重启后引擎返回200/OK。按新用户授权删除114个已停止旧测试容器和唯一未被引用的旧Node验证镜像，全部24个保留容器身份完整、15个原运行ID仍在运行，141个卷完全保留。未启动旧tmpfs B2 PG，未把真实导出／Linux验证标为通过。最终报告 docs/validation/2026-10-30-docker-test-cleanup.md，完整脱敏回执 outputs/_run/docker-test-cleanup-20261030/post-cleanup-verification.safe.json。宿主可用约656MiB，仍不满足桌面新包2GiB门。
