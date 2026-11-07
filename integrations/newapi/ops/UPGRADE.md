# 单实例 New API 升级工具

`upgrade_newapi.py` 是可审查的生产操作脚本。当前本地验证只使用 fake subprocess 和临时文件；**本地测试通过不代表线上备份、恢复或升级已完成**。执行者必须先核对真实机器状态。本工具只适用于已核对的单节点 1Panel 实例，运行时强制要求 root、hostname `iv-ye8aaj2u4gcva4gofev6`，并断言服务、容器和持久化绑定。

默认 compose：`/opt/1panel/apps/new-api/new-api/docker-compose.yml`。要求恰好三个服务：`new-api` / `new-api-db` / `new-api-redis`；对应容器 `1panel-new-api` / `1panel-new-api-db` / `1panel-new-api-redis`。持久化目录分别是同级 `data`、`logs`、`postgres-data`、`redis-data`，挂载到 `/data`、`/app/logs`、`/var/lib/postgresql/data`、Redis `/data`。无法核对时直接停止。

此外，`/opt/1panel/www/sites/new-api` 网站目录必须存在；整个目录（包括代理配置、隐藏文件及其余内容）会归档为 `website.tar`，纳入磁盘预算、归档读取与 manifest SHA256 校验。工具不修改网站或 OpenResty 配置。

Python 3.10+、Docker Compose 的 JSON config 输出及官方 PostgreSQL 镜像内的工具是必要条件。不读取或输出管理员密码。数据库操作通过现有 PostgreSQL 容器内的 `POSTGRES_USER` / `POSTGRES_DB` 环境和本地连接执行，不把密码或 DSN 放进命令行。没有安装依赖、构建镜像、更新 PostgreSQL/Redis/OpenResty 或修改业务配置的步骤。

只读 plan 不创建备份目录、锁文件或其他生产文件，不 pull，不停服务，不创建数据库：

```bash
python3 /absolute/staging/path/upgrade_newapi.py \
  --lock /absolute/staging/path/catalog.lock.json \
  --backup-root /opt/backups/new-api-20261020
```

`--apply --backup-only` 执行并验证备份，之后恢复原 Redis 和原应用容器；不会更新 compose。新建的恢复验证数据库保留供管理员检查：

```bash
python3 /absolute/staging/path/upgrade_newapi.py \
  --lock /absolute/staging/path/catalog.lock.json \
  --backup-root /opt/backups/new-api-20261020 \
  --apply --backup-only
```

明确 `--apply` 才在通过备份验证后升级：

```bash
python3 /absolute/staging/path/upgrade_newapi.py \
  --lock /absolute/staging/path/catalog.lock.json \
  --backup-root /opt/backups/new-api-20261020 \
  --apply
```

执行流程如下：

1. 核对 root/hostname、compose 服务、容器名/运行状态/镜像一致性、持久化挂载、PG/Redis 无公开端口、回环映射和可用空间。对应用在 HostConfig/NetworkSettings 两个视图中的全部端口映射逐项检查：3000 的所有 IPv4/IPv6 映射必须为 loopback，其他端口也不得公开，并必须存在可验证的 `127.0.0.1` 3000 映射。PG/Redis 仅允许未发布端口或显式 loopback 发布。默认要求备份盘至少 8 GiB 空闲，并根据待备份目录、网站目录、数据库大小额外估算；PG 盘也必须容纳独立恢复验证库。
2. apply 才创建 APP_DIR 内 root 私有 `.newapi-upgrade.lock` 并取得非阻塞 `flock`。其他 apply 无论选什么备份目录都无法并行执行。锁 inode 留在原处，进程退出自动释放，不按 PID 文件存在与否判断状态。取得锁后再次确认 compose 未变化。
3. 只 pull lock 指定的 `calciumion/new-api:<tag>@sha256:<digest>`，并核对 `docker image inspect` 的 RepoDigests；不 pull 数据库或 Redis。记录原镜像、image ID、原 compose SHA256、有效 compose SHA256，以及三个容器 Config/HostConfig/Mounts/Image 的哈希，记录中不包含环境变量明文。
4. 创建 root 私有备份子目录，先写 `manifest.partial.json` 和人工恢复指引 `RESTORE.md`。停应用并确认停止，再停 Redis 并确认停止。PostgreSQL 保持运行。更新 partial manifest 的 `stage` 便于私密后台轮询。
5. 使用 `pg_dump -Fc` 生成 `postgres.dump`；打包整个配置目录（包含 `.env` 等隐藏文件，排除四个单独持久化目录），再分别完整打包 `data`、`logs`、冷态 `redis-data`，以及整个网站目录 `website.tar`。Redis AOF 子目录、manifest、增量文件全部包括；不会热拷 PostgreSQL 物理文件。归档不跟随符号链接。
6. 校验所有备份 SHA256，执行 `pg_restore --list`，创建全新唯一命名 `newapi_verify_*` 数据库，`pg_restore --exit-on-error` 恢复，并比较源库与恢复库的 `users/channels/options` 行数。保留验证库，不执行 DROP。通过后写 `manifest.json` 和包含其 SHA256 的 `COMPLETE`。
7. 恢复原 Redis。只有 COMPLETE、manifest hash、全部归档 hash 都再次通过检查后，才原子替换 compose 中 `new-api` 的唯一直接 image 行；其余原始字节保持不变。重新解析有效 compose，并要求唯一语义差异是应用 image。任何其他差异都拒绝启动新应用，恢复原 compose 与原容器。
8. `docker compose up --detach --no-deps --pull never new-api` 只重建应用。之后核对新 image ID、容器 running/health、回环 `/api/status` 和启用证书校验的 HTTPS `/api/status` 版本，同时再次核对 PG/Redis 的配置哈希、健康与公开端口。没有 TLS 跳过或重定向放行参数。

备份失败时，在尚未尝试启动新镜像的前提下，脚本会通过 `compose start` 恢复原有 Redis 和应用容器。只有确认恢复了原 image ID，才报告 `backup_failed_old_services_restored`。如果恢复也失败，报告 `backup_failed_old_services_recovery_failed` 和 `manual_recovery_required`，保留备份路径与失败阶段，不会假称服务恢复。

一旦尝试启动新应用，数据库迁移就可能已经发生。之后的任何启动、健康、HTTPS、版本或端口验收失败都会停止新应用，并报告 `upgrade_failed_manual_database_recovery_required`；**绝不自动切回旧镜像连接可能已迁移的数据库**。备份目录内的 `RESTORE.md` 要求保留失败现场，将逻辑备份恢复到新的空库，恢复完整 Redis 冷备到独立卷，用原配置和旧 image ID 指向恢复库做隔离验证，再由管理员审查切换。该恢复指引是模板，没有执行证明。

此工具不实现先在克隆数据库上跑应用迁移再切换、自动数据库回滚、多节点停写、完整应用/Redis 恢复演练。独立 PG 恢复和基础表计数能证明归档可恢复及部分数据一致，不能证明计费、渠道、供应商推理或所有业务流程正常。升级后仍需单独核对版本发布说明涉及的视频价格、Playground 历史变化和敏感词开关；工具不会自动重配它们。

标准输出仅包含 JSON 摘要与备份路径。子进程 stdout/stderr、compose/env、密码/DSN、HTTP 原始响应都不输出。后台运行应将脚本的 JSON 摘要写入管理员控制的私密日志，并读取 `manifest.partial.json` 观察阶段；不要因连接断开就重复 apply。信号和正常异常会进入恢复分支；主机断电、内核杀进程或 SIGKILL 无法保证自动恢复，需按私密备份目录中的指引检查现场。

本地验证：

```bash
rtk proxy python3 -W error -m unittest discover -s integrations/newapi/ops/tests -p test_upgrade_newapi.py -v
rtk proxy python3 integrations/newapi/ops/upgrade_newapi.py --help
```

生产执行前还需把本地代码审查、fake subprocess 测试与实际服务器备份/恢复证据分别记录，不能用本测试替代真实验收。
