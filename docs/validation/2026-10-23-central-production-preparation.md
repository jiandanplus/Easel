# 中央平台 S5-A1 部署准备验收

日期：2026-10-23。当前分支 `codex/ui-redesign`。本批交付部署准备工具与可审阅配置，未正式切换现有账号、中央服务或管理后台。商业支付仍未接入；不将离线通过标为生产上线。

## 已确认部署目标

用户确认三个 HTTPS 域名，统一主机 `115.190.211.112`：

| 服务 | 域名 | 本批事实 |
| --- | --- | --- |
| 中央 API | accounts.drawbookai.cn | 公共 A 记录指向目标 |
| 中央 Console | console.drawbookai.cn | 最初指向139.196.51.51；用户修改独立 A 后已复核指向目标 |
| Logto 登录 | login.drawbookai.cn | 最初指向139.196.51.51；用户修改独立 A 后已复核指向目标 |

2026-10-23T14:57Z 使用公共 Cloudflare DNS over HTTPS 查询三者 A／AAAA：A 均为目标地址，无 AAAA。DNS 变更由用户在域名控制台完成；本代理只做只读查询，没有修改其他记录或通配记录。

2026-10-23T14:58Z 对目标 IP 的443端口执行固定 SNI／Host 的匿名受验证 TLS 检查，三站均在握手时产生 `SSLCertVerificationError`（verify_code62，主机名不匹配）；没有关闭证书验证，没有发送登录凭据，也没有取得可用于证明应用就绪的 HTTP 响应。**DNS 已正确，TLS 和正式服务尚未验收。** 现有 `api.drawbookai.cn` 的站点、New API 和本地预览未在本批修改。

## 本批实现

- 新增 `services/platform/scripts/prepare_deployment.py`：stdlib、固定公开 JSON 合同、URL／目录／端口注入防护、固定错误、拒绝不完整或篡改的 wheel、排他输出。
- 新增部署通用示例与 `drawbookai.public.json`，只含公开地址、端口和目录，不包含凭据。
- 生成两个独立非 root／loopback systemd unit、API／Console HTTPS Nginx片段、两个私有配置模板和离线报告。模板不安装／重启服务，也不加载 owner 配置。
- 生成报告永远为 `verification=offline_only`、`production_ready=false`；原 issuer 与目标不同会保留 `identity_migration_required=true`。
- 更新中央服务 README，纠正“所有真实模型仍未验收”的旧状态，分别引用最新工作台／Console 记录与生产准备门槛。
- 新增账号迁移审计与部署／备份恢复／回退手册：不按邮箱合并、不重建余额；默认保留原权威数据库／schema，恢复副本仅作演练；停止新派发不能代替旧 writer fencing 与最终核账。

## 新鲜验证证据

### 离线自动化

Root 最终执行：

```text
rtk proxy services/platform/.venv/bin/python -m pytest services/platform/tests/test_deployment_preparation.py -q --tb=short
79 passed in 0.15s
```

实现者保留了 RED／GREEN：初始缺模块；数字 DNS 边界；`.venv`、证书目录和元数据规范化问题；独立审查发现的分段 hex IPv4 别名。最后一项定向 RED 为6失败／9通过，修复后15通过，完整79通过。Root 完整重跑上述79项，未将之前630项后台回归当成本轮新运行。

覆盖实际约束：重复／未知／缺失 JSON 字段、HTTP／IP／hex数字别名／非法 origin、原 issuer 字面 loopback 正例、端口与路径注入、wheel Python／SQL／静态文件篡改／缺失／额外文件／重复ZIP／符号链接／大小边界、排他输出与权限、错误脱敏、规范 Host／Origin、流式缓冲、loopback 和服务凭据隔离。

### 构建和包一致性

重新运行独立 Console 构建：TypeScript 检查和 Vite 构建成功；10个源输入、3个静态产物，资产回执另计1个文件。资产回执 SHA256：

```text
c3fddd3e9849e2f71057f02f9ef4c3abf9086f5391f15142e5270e839651d62f
```

新构建中央 wheel，196047字节，SHA256：

```text
7addda835c3f4201d63c6c1f68e8173e5e8bb41e4f68f63dc98e44551745c24b
```

生成工具核对全部52文件：39 Python、9 SQL、4 Console静态文件，文件集合与字节完全匹配当前仓库；METADATA Name／Version 与公开项目配置和dist-info一致。依赖元数据、第三方供应链与签名不由此工具认证，该限制写入报告。

### 全新环境安装

使用新建的隔离 Python3.12.7 venv，没有复用预览服务或原项目依赖目录。由 `uv.lock` frozen 导出运行依赖，连同本地wheel以SHA256固定，执行 `uv pip install --require-hashes --no-deps` 安装26个包成功。

随后在清空业务环境变量的独立 `python -I` 子进程中使用合成配置，实际导入并构造 API 与 Console，确认9个迁移及静态首页存在。未启动ASGI lifespan、数据库连接或HTTP模型调用，不读取任何真实env、业务令牌、OIDC secret或客户端会话。

这证明本机构建产物可在干净的 macOS Python环境安装和构造，不证明 Linux主机已安装、低权运行、TLS、数据库迁移或真实OIDC可用。当前Mac没有Nginx或systemd-analyze，本轮没有执行目标主机的 `nginx -t` 或 systemd unit 验证；正式安装前必须执行。

## 独立审查

审查发现并补齐：实际切换需冻结唯一权威DB／schema，停止旧版晚到结算／recovery writer并记录最终核账截止点；默认不把恢复副本作为第二套生产钱包。另发现目标DNS会接受分段hex IPv4别名，已补测和修复。审查人员只读核对源码、模板和报告，没有替Root重新执行测试／DNS／数据库操作。

最终spec与quality结论、原发现与修复证据见 [独立审查](2026-10-23-central-production-preparation-review.md)。

## 可审阅产物

最终输出目录：`outputs/_run/central-production-preparation-20261023/`；其中`deployment/`的生成报告保留全部真实切换门槛，不能手改为ready。临时v1目录是修复前的中间证据，不用于交付。

公开部署配置：[drawbookai.public.json](../../services/platform/deploy/drawbookai.public.json)。操作手册：[部署准备与切换](../../services/platform/deploy/README.md)。身份影响：[身份迁移审计](2026-10-23-central-production-identity-audit.md)。本批设计和计划留在当前分支工作区，未自动提交／推送。

## 未执行的实际阶段

正式证书与代理站点、服务器常驻unit、新Logto部署、精确身份别名和原管理员ID迁移、旧writer停用与最终核账、真实备份／隔离恢复、生产权限与迁移、真实重新登录／模型／商户、性能／监控／故障恢复均未在本批完成。

目前产品外部身份表支持一用户多身份，但尚无本批验收的owner迁移工具；Console也尚无保留原admin ID的别名迁移能力。不能只修改issuer，或以相同邮箱／用户名重新授权代替迁移。

HEAD保持`e972af07444e181e3eb737441ecac306bca778b3`，main保持`bb3c8ff0463f36c202454d74171682ba6e470cb1`，分支仍为`codex/ui-redesign`；未合并主分支。没有服务器SSH、真实数据库／模型／支付调用或原预览服务操作。现有已运行的本地工作台、登录环境、中央API、Console与自定义接口配置不在本批修改范围。
