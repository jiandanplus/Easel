# AV network isolation policy revision proposal

## 用户决策摘要

**状态：待确认，尚未修改原执行器或验收规则。** 当前分支仍为 `codex/ui-redesign`。

本次已定位到的失败原因，是验收代码要求“接口列表只能有 lo”。在当前 Docker 内核中，同一隔离容器的进程网络视图有 10 个接口：1 个回环接口，另外 9 个接口的类型符合已知隧道类别，均处于 DOWN 状态；路由数为 0。诊断没有证明设备的创建者，也没有采集内核 link kind，不能把这些诊断当成完整隔离验收通过。

**推荐方案 A：增加严格的休眠设备验收。** 保留 `NetworkMode=none`、原路由判断、五项能力全清空、NNP、seccomp、只读挂载和全部原资源限制。完整列出当前网络命名空间的设备，回环接口之外，只允许下表九个固定名称，且每个都必须满足硬件类型正确、内核 link kind 正确、UP 和 RUNNING 位都未置位。未知设备、启用设备、类型不符、资料缺失或命名空间不符，一律拒绝。新增只读元数据采集，不提升权限，不修改网卡、内核设置或 Docker Desktop。

批准后，先补齐生产采集器、严格校验器和相应测试，再用未注入的原控制器／守护进程／worker 做新案例验证；通过后继续原资源控制、生存期、产物保留和真实源码包验证。旧失败记录不会改写为通过。

**方案 B：保留“只能有 lo”这一字面要求。** 准备符合该要求的专用 ARM64 Linux 验收环境，再运行原案例。具体资源与部署另作预检；选择本方案本身不授权修改现有主机内核、Docker 设置或购买资源。

本次选择只涉及这一条本地网络验收条件，其余开发与交付范围完整保留；前端不增加解释文案，现有登录、历史、自定义／订阅模式与主分支保持原状。下文为技术规则、证据和验证条件。

Status: **PROPOSED, awaiting user decision.** No original runtime, worker, guard, schema or limit has been changed. Branch remains `codex/ui-redesign`; no frontend copy, account/history/config, subscription/custom mode, commit/push/merge or package changes.

## Verified problem and limits

The existing execution plan (`2026-11-02-av-pack-executor.md`, global constraints) requires “only lo interface/no network route.” Current pinned runtime C08 implements `interfaces == ['lo'] and routes == []` at line36. Original native preflight failed at runtime and stays rejected. Four subsequent explicitly injected, unconditional-exit70 diagnostic variants are not original acceptance.

Successive bounded observations established:

- Current sysfs inventory has11interfaces (1lo,10other), with0reported routes.
- Same-case `/proc/self/net/dev` inventory has10interfaces (1lo,9other). Therefore a simple path substitution is insufficient.
- A new same-case read-only ioctl census classified all10current-namespace devices, with unknown_count0. Nine non-loopback devices have neither IFF_UP(1) nor IFF_RUNNING(64). Types match the expected hardware categories below.

| Closed role | Permitted kernel name in proposed policy | Observed ARPHRD | Observed short flags |
|---|---|---:|---:|
| loopback | lo | 772 | 73 |
| ipip | tunl0 | 768 | 128 |
| gre | gre0 | 778 | 128 |
| gretap | gretap0 | 1 | 4098 |
| erspan | erspan0 | 1 | 4098 |
| vti4 | ip_vti0 | 768 | 128 |
| sit | sit0 | 776 | 128 |
| ip6tnl | ip6tnl0 | 769 | 128 |
| ip6gre | ip6gre0 | 823 | 128 |
| vti6 | ip6_vti0 | 769 | 128 |

These are sequential observations, not an atomic snapshot or proof of creator identity, absent addresses, kernel link kind or complete isolation. Role names alone are insufficient. No diagnostic is reclassified as a successful native test. The original failure archive and every consumed nonce remain immutable.

Linux official documentation describes automatic fallback tunnel creation in every new network namespace when the compatibility default is0: [kernel sysctl documentation](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux/blob/master/Documentation/admin-guide/sysctl/net.rst). Current-process network information is exposed through [proc_pid_net(5)](https://man7.org/linux/man-pages/man5/proc_pid_net.5.html). Flag getter semantics are documented in [netdevice(7)](https://man7.org/linux/man-pages/man7/netdevice.7.html); hardware identifiers in [Linux6.12 UAPI if_arp.h](https://raw.githubusercontent.com/torvalds/linux/v6.12/include/uapi/linux/if_arp.h). The observations are consistent with dormant fallback devices; origin has not been independently established.

## Option A: explicit strict inactive-device policy — recommended

Change this one local AV acceptance condition to **loopback plus a closed set of verified inactive kernel tunnel devices**. This is a versioned rule change requiring user approval, not a declaration that the old exact-onlylo condition passed.

The new producer/consumer must enforce all of the following before any decode and again during host report admission:

1. Original HostConfig NetworkMode=none, private namespaces, no privileges/devices/ports/restart, ALL capabilities dropped, NNP1, Seccomp2, readonly mounts and all original resource limits remain exact. All five capability masks must still be zero. The existing route predicate remains unchanged and must still pass; no route is newly allowed.
2. Enumerate the complete current-process namespace interface set using strict proc-net-dev parsing (65536bytes,128interfaces maximum) and one read-only RTM_GETLINK dump (65536cumulative responsebytes,256messages,128interfaces,64attributes permessage maximum). Bind sequence/sender, require complete DONE and reject truncated, duplicate or malformed records. Cross-check both inventories and require current/self and supervisorPID1 network-namespace identities to match. Do not compare against an invented macOS host-netns inode.
3. Keep the complete inventory in the runtime proof; never silently filter unknown devices. `lo` is required and must have the loopback hardware type and loopback flag. Any additional name outside the nine explicit names above fails, even if DOWN.
4. For each permitted extra name, require matching hardware type AND kernel link kind (ipip, gre, gretap, erspan, vti, sit, ip6tnl, ip6gre or vti6, respectively). Require both UP and RUNNING bits clear. Unknown/missing/malformed kind or type, an enabled device, duplicate/missing metadata or incomplete dump fails closed. The diagnostic has not yet observed link kinds; a fresh actual candidate must prove them before acceptance.
5. Collect only read-only metadata in the worker's existing envelope. Permit only fixed kernel-metadata requests; forbid network data-plane packets, interface setters, extra capabilities, privileged setup, host kernel/sysctl changes, mount changes or renewed timers. Retain original AS512MiB/CPU30/wall120, controller180 and all storage/transport limits. Metadata failure never authorizes adding capabilities or increasing limits.
6. Add a versioned network-policy identifier and complete device/namespace metadata to the exact runtime proof schema; update native producer, synthetic fixtures and strict consumer together. Hash-bind every changed public source in the new profile/stage. Default CLI uses native dependencies with all injection flags false. Historical schemas/failed receipts are not upgraded.

Implementation scope after approval: runtime collection/validation and its focused tests, worker synthetic-proof/schema expectations where necessary, source-pin closure and new private acceptance stages. No unrelated frontend, account, payment, model, provenance, source loader or API quota changes.

Required verification: parser boundary/type/duplicate/short/truncated tests; complete interface-set mismatch; unknown name even DOWN; matching name with wrong hardware or kind; UP/RUNNING transitions; missing namespace or supervisor mismatch; empty/extra/malformed netlink messages; socket cleanup and no setters; existing runtime/cgroup/limits/caps/signal regressions. Then a fresh original preflight with default dependencies, original guardian, zero injection flags, real full artifact retention and original deadline/publication fences. Only after that proceed to original AS/CPU/wall controls, near16MiB retention and real sourcepack gates. No existing AV/release gate is claimed closed by this proposal.

Tradeoff: more precise kernel metadata parsing and a revised local acceptance schema, with explicit evidence that allowed extra devices are unusable under the worker's zero-capability envelope. Physical/interface-name-only permissiveness is not acceptable.

## Option B: retain exact-onlylo rule

Leave the current runtime predicate and historical contract unchanged. Prepare a dedicated ARM64 Linux validation environment where new none-network namespaces actually contain onlylo, then execute the original cases there. Inspect resources and propose the environment before deployment; no automatic host-wide sysctl, Docker Desktop change, privileged cleanup or resource purchase is authorized by this option alone.

Tradeoff: preserves the literal criterion but requires separate environment preparation and resource validation. Current Mac Docker environment cannot be declared compatible merely by ignoring its extra devices.

## Decision and unchanged overall scope

The user must select A or B before dependent implementation or acceptance-rule changes. Original cancellation cases, full19AV requirements, complete76-token FFmpeg9.0.2 configuration, source identity/provenance/adoption, B2/B3/T13/T14/F2/DR, signing/notarization, clean-machine release and the remaining delivery requirements retain their original scope. Formal payments remain explicitly deferred. No successful deliverable is asserted yet.

Evidence locations: `outputs/_run/av-native-worker-20261103/` (original failure), and the runtime-location, runtime-network, runtime-view and runtime-device diagnostic directories dated20261103. Current typed device observation: CID f1698cd0967ea78cdf640f778c02cf7d45be2fa23d903ad0a8e81dd0043b781b, freeze fbc0d853238180991e3e24b91700dd0397422c42ab8161af63cd7d3b2c5f7c6c, raw validated stdout body1249bytes, payload1233bytes, archive49b7bf48497fdd1741f2235a64066a25686bd74fae02220796b34c35f8cb086e. Root records and independent bounded audits must stay distinguished from live private observations.
