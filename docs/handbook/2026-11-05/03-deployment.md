# 03 · 部署手册

本手册是现有部署的交接说明和后续发布流程。服务器分工来自历史部署回执，未在本次重新连接服务器确认。实施前先核对目标主机、运行版本、服务路径与数据权威；不要复制旧时点配置直接覆盖当前环境。

## 1. 服务器分工

| 位置 | 已记录职责 | 对外入口 | 维护边界 |
| --- | --- | --- | --- |
| 新服务器 `154.217.245.179` | 中央 API、私有 Console、Logto、中央 PG16、身份 PG16、New API（2026-11-06 起）、Nginx、备份／健康检查 | accounts／login HTTPS | 管理、数据库与原始应用端口私有 |
| 火山引擎 `115.190.211.112` | 已停用，不续费（2026-11-04 起不通，旧 New API 数据未取回） | 无 | 不再使用 |
| 阿里云 `139.196.51.51` | 历史迁移中原网站保持原状 | 原网站 | 本项目未因该次分配自动部署额外服务 |
| 用户 Mac | Easel UI、本地 FastAPI、Pi、工作区、私有会话 | `127.0.0.1` | 不部署中央数据库或供应商公共密钥 |

这是分服务部署，中央 API、业务库与身份仍有单机故障域，不能称为高可用集群。若以后将公网入口移到阿里云，应另定账号 issuer、唯一写入点、跨机链路、证书和切换计划；当前手册不执行重新分配。

## 2. 地址与端口清单

| 地址 | 用途 | 允许访问方 |
| --- | --- | --- |
| `https://accounts.drawbookai.cn` | 中央用户 API／产品登录回调 | 用户设备，经 HTTPS |
| `https://login.drawbookai.cn` | Logto 托管注册登录；issuer 精确为 `/oidc` | 用户浏览器 |
| `console.drawbookai.cn` | 历史部署为公网 404 | 不承载公共管理登录 |
| 154 上 `127.0.0.1:19874` | 中央 API | Nginx／本机受控探针 |
| 154 上 `127.0.0.1:19875` | Console | SSH 私有转发 |
| 维护者 `127.0.0.1:18885` | Console 浏览器入口 | 本机 SSH 隧道 |
| 维护者 `127.0.0.1:18886` | Logto 管理入口 | 本机 SSH 隧道 |
| 154 上 `127.0.0.1:19443` | Nginx 到本机 New API 的 TLS 入口，见 [网关说明](../../../services/platform/deploy/newapi/README.md) | 中央服务 |
| 154 上 `127.0.0.1:19880` | New API | Nginx／SSH 私有转发 |
| `https://api.drawbookai.cn/console` | New API 管理入口 | 公网 443，Nginx 访问密码在前，密码在 Mac 的 `~/Easel-build/newapi-console-access.txt` |
| 维护者 `127.0.0.1:18887` | New API 管理入口（备用） | 本机 SSH 隧道 |
| 用户 `127.0.0.1:17868` | 已有原工作台 | 用户本机；端口不是服务版本标识 |

1Panel 原专用入口曾通过本机 18880 访问；具体目标端口和安全路径沿用私有维护记录，不在公开手册存凭据。容器内部 IP 如 `172.29.24.20` 是历史分配值，重建后要核对。

数据库、Redis、Console、Logto 管理端和 1Panel 不因“用 IP 访问”就变成私有。私有性由监听地址、防火墙和隧道决定。用户注册／订阅入口保持有效 HTTPS；不把生产 OIDC 改成公网裸 IP HTTP。

## 3. 已记录服务名

| 对象 | 名称 |
| --- | --- |
| 中央 API | `easel-central-production.service` |
| Console | `easel-console-production.service` |
| New API | compose 项目 `easel-newapi`（`/srv/easel/newapi`），容器 `easel-newapi`、`easel-newapi-pg`、`easel-newapi-redis` |
| 网关备份 | `easel-newapi-backup.timer` |
| HTTPS 代理 | `nginx.service` |
| 健康检查 | `easel-health-watch.service`、`easel-health-watch.timer` |
| 中央 PG | `easel-central-pg-20261024` |
| 身份 PG | `easel-identity-pg-v2-20261024` |
| Logto | `easel-logto-v2-20261024` |

真实 release 最后明确记录为 `/srv/easel/releases/20261027-images-m1-1-size-v2`。不要将较早 `/opt/...` 示例路径或 10 月 24 日初始 release 当成当前 systemd 路径。正式发布前从实际 unit 核对。

## 4. 发布前只读预检

先完成一张具体部署记录：

| 必填项 | 核对内容 |
| --- | --- |
| 目标 | SSH host key、IP、hostname、OS、CPU架构、服务账号 |
| 现状 | API／Console release 与 PID、容器完整 ID／镜像、卷、监听、重启策略 |
| 域名与证书 | A／AAAA、精确 SNI、CA 验证、续期与路由、OIDC issuer／回调 |
| 容量 | 实际可用内存、磁盘、inode、数据库连接、备份与恢复峰值 |
| 数据 | 权威业务库／schema、身份库、媒体路径、在途及待核算、迁移校验和 |
| 制品 | 新 wheel、依赖锁、静态资产、来源清单、哈希、独立安装验证 |
| 保全 | 新私有备份收据、旧配置与 unit 摘要、恢复步骤、失败保留目录 |

无消费只读示例，在维护者电脑运行：

```bash
rtk proxy ssh -o StrictHostKeyChecking=yes easel-zhiyin \
  'hostname; df -h /; free -m; systemctl is-active easel-central-production.service easel-console-production.service nginx.service; docker ps --format "{{.ID}} {{.Names}} {{.Image}} {{.Status}}"'
```

SSH 别名须已经安全配置并核验主机密钥。不要输出 `docker inspect` 全环境、`env`、私有配置或完整回调日志。

## 5. 构建中央发布候选

先安装锁定 Console 依赖，构建 Console receipt，再生成 wheel 和全新部署准备目录：

```bash
rtk proxy npm ci --prefix services/platform/frontend --ignore-scripts
rtk proxy python3 services/platform/scripts/build_console.py
rtk proxy uv build --project services/platform --wheel --out-dir /ABS/NEW/central-wheel
rtk proxy python3 services/platform/scripts/prepare_deployment.py \
  --config /ABS/PUBLIC/central-public.json \
  --wheel /ABS/NEW/central-wheel/easel_central_platform-0.1.0-py3-none-any.whl \
  --output /ABS/NEW/central-deployment
```

配置结构见 [部署准备工具合同](../../../services/platform/deploy/README.md)。公开 JSON 仅放 URL、端口和路径，不放 DSN、secret 或业务 Key。工具只做离线准备，不安装服务、不迁移数据库，不把 `production_ready: false` 改为 true 作为上线证明。

生成器早期模板包含公网 Console 的通用示例；当前实际是私有 Console，不能直接覆盖现有 Nginx／unit。发布计划要明确检查并保留私有绑定。wheel 文件名如因版本改变，应使用实际产物名。

## 6. 服务器私有配置

| 配置组 | 使用者 | 不应携带 |
| --- | --- | --- |
| API runtime | 中央服务账号 | owner DSN、管理员 M2M、无关供应商凭据 |
| Console runtime | 独立 Console 账号 | API 业务 Key、owner、支付私钥 |
| operator | 一次性迁移／授权／运营命令 | 不挂到常驻进程或浏览器 |
| 文本／媒体供应商文件 | 对应服务账号 | 不复制到桌面发行包 |
| 支付商户文件 | 未来明确启用的支付进程 | 当前支付保持 disabled |

运行环境文件和模型文件按各自合同设为服务用户所有的受限普通文件，通常 0600，父目录收敛权限，不用符号链接。服务器实际路径从现有 unit 和私有维护清单读取；不要把 README 中的示例路径当成已存在路径。

中央模型文件 `CENTRAL_EXECUTION_CONFIG_FILE` 包含固定 endpoint、secret、公开模型到上游模型映射及超时策略；其变更会改变 provider binding，需要受控重启和新目录验收。任务请求不能自己指定 URL 或费率。

## 7. 数据库与 Logto 初次部署／迁移

数据库先建立 owner、runtime、Console 角色与受控 schema，收回不必要 PUBLIC 权限，再由 owner 追加迁移和最小授权。服务启动不自动迁移。新环境的命令形状：

```bash
rtk proxy uv run --project services/platform --frozen \
  --env-file /ABS/PRIVATE/central-operator.env python -m platform_service migrate
rtk proxy uv run --project services/platform --frozen \
  --env-file /ABS/PRIVATE/central-operator.env python -m platform_service grant-runtime --role central_runtime
```

这些命令用于准备明确目标；正式服务器应使用本次 release 的固定运行环境，不依赖开发源码和自动联网解析依赖。Console 授权使用其独立 operator 文件及 `grant-console`。恢复已有数据时不重复创建会员、余额或体验额度。

Logto 精确产品回调为：

```text
https://accounts.drawbookai.cn/oidc/easel/callback
http://127.0.0.1:18885/oidc/console/callback
```

后者是当前 SSH 私有 Console 配置。两者必须使用各自应用 ID／secret。更换身份 issuer 先按 [身份迁移说明](../../../services/platform/IDENTITY_MIGRATION.md) 与 [迁移运行手册](../../platform/logto-migration-runbook.md) 做一对一可信映射、写入截止和本人登录，保留原用户、tenant、wallet、admin ID、禁用状态与审计；不按邮箱猜合并。

## 8. 正式升级顺序

1. 冻结本次制品、配置、现有迁移和授权清单；完成独立安装与对应测试。
2. 形成新备份收据，确认明确的隔离恢复证据与本次数据范围匹配。旧 R2 双库恢复不等于当前媒体全恢复。
3. 关闭所有产品的新任务、新购买和新登录准入，记录在途／待核算；让现有任务按计划结束或进入明确未知状态。
4. 停止对应 writer 与旧服务，确认数据库会话和服务管理器不会让旧版本继续写账。当前没有通用一键 drain/fence CLI，必须提供本次具体步骤。
5. 保存最终账本／媒体投影与配置摘要；用独立 owner 追加**本次批准的**迁移，更新 runtime／Console 授权并运行权限守卫。
6. 安装到全新 release 目录，受控修改 unit 路径，启动 API／Console；核对健康、资产、原配置、身份与财务投影、私有边界。
7. 逐步开放唯一权威入口，完成本人登录和限定模型、工具、下载、扣点与异常状态验证，随后记录实际版本。

迁移 017–020 及后续候选不能因为源码存在就全部应用到生产。发生未知提交结果先查状态，不自动再次迁移／恢复。失败关闭新准入、保留证据；数据库已变更时，“切回旧程序”不能视为完整回滚，也不能把旧钱包快照重新接上消费。

## 9. 私有后台访问

在维护者本机新终端保持隧道运行：

```bash
rtk proxy ssh -NT -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=yes \
  -L 127.0.0.1:18885:127.0.0.1:19875 \
  -L 127.0.0.1:18886:172.29.24.20:3002 easel-zhiyin
```

浏览器准确打开 `http://127.0.0.1:18885/`。不要换成 localhost、服务器 IP 或 19875；应用校验精确 Host／Origin，Logto 回调也必须匹配。第二条转发的容器 IP 使用前核对。产品与管理员建议使用独立浏览器 profile。

## 10. 代理、TLS 和扩容

Nginx 保留规范 Host／Origin，SSE 路径关闭响应缓冲，避免记录 Authorization、Cookie、正文和 OIDC 查询串。项目中央 CLI 明确关闭代理头信任；不要把框架通用示例中的信任所有来源参数复制进本项目。若增加路径前缀，须同时验证外部 origin、回调和 ASGI 路径，不能只改一层代理。框架概念核对见 [FastAPI 官方代理文档](https://fastapi.tiangolo.com/advanced/behind-a-proxy/)，本项目配置以当前源码合同为准。

初始 API 单进程、数据库池 max 5，Console max 2 的历史资源设计不是压测承诺。增加 Uvicorn workers 前必须确认当前进程内限额、租约、调度与幂等已跨实例一致。后续扩展建议按“入口／应用无状态化 → 共享状态与调度 → 数据库备份副本／HA → 故障演练”顺序推进，不依靠简单多起进程宣称高可用。

## 11. 桌面升级

桌面构建参见开发手册。升级前确认真实空闲、通过原实例认证的停止入口正常退出，备份实际工作区和私有配置；新版本使用同一受保护数据路径，不将通用包内种子覆盖用户文件。不通过占用端口或猜 PID 杀进程。旧 `harness.locked` 字段不等于任务空闲。

当前 v36 只有构建与限定验证记录，原 17868 最后激活记录仍为 v35。选择新制品、源清单、实际工作区和升级时间后，另执行保全与本人真实验证。
