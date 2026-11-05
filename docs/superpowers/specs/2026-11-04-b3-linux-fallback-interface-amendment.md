# B3 Linux network-none interface observation amendment — proposed

Status: user approved Route A implementation and verification on 2026-11-04. Exact approved proposal bytes and approval are archived under outputs/_run/b3-worker-artifact-preparation-20261104/network-amendment. No runtime acceptance or PostgreSQL restore success is implied. This does not extend the already approved bootstrap grantor exception. Current branch codex/ui-redesign; no production/model/source-DB changes.

## Actual finding

The current reviewed worker rejects `sorted(os.listdir('/sys/class/net')) != ['lo']` (execution_worker.py:321). Actual first cold launch of pinned worker image sha256:265b68fefa8c50915931b3893eae418732355fd50660097e932e9f3b75cb1ff1 ended exit1 with its fixed diagnostic. Original failed container 2fbc215f32f0ab254b983ff0d237e0ed58f6fecff4a1f456e0eb007b4b566a01 remains stopped and retained; never restarted or counted as PASS.

The first fresh public diagnostic container located the rejected phase/line; the second and third read bounded fixed kernel metadata to explain it. Docker configuration is networknone/noports/nobinds/ROroot/UID10001/capALLdrop/NNP/privatecgroup. `/proc/net/dev` and `/sys/class/net` both show nine default tunnel devices, all DOWN and IFF_UP clear; no veth/eth interface. `/sys/class/net/bonding_masters` is a regular sysfs attribute, not an interface. IPv4 route table has only its header; IPv6 routes use lo only; local IPv4 trie only127/8; IPv6 address inventory only::1 onlo. This is not sufficient final target isolation proof; no actual host-to-target namespace pair has been qualified.

Exact observed default-device table (public metadata):

| name | ARPHRD type | flags | iflink | operstate |
|---|---:|---|---:|---|
| tunl0 | 768 | 0x80 | 0 | down |
| gre0 | 778 | 0x80 | 0 | down |
| gretap0 | 1 | 0x1002 | 0 | down |
| erspan0 | 1 | 0x1002 | 0 | down |
| ip_vti0 | 768 | 0x80 | 0 | down |
| ip6_vti0 | 769 | 0x80 | 0 | down |
| sit0 | 776 | 0x80 | 0 | down |
| ip6tnl0 | 769 | 0x80 | 0 | down |
| ip6gre0 | 823 | 0x80 | 0 | down |

All hardware addresses observed zero. Evidence is retained under outputs/_run/b3-worker-artifact-preparation-20261104/cold-worker-once001 and cold-diagnostic-once001/002/003. The latter are diagnostics, never worker-entrypoint PASS. No original request, policy, duration or failed resources are replayed.

## Route A — exact inactive fallback profile

Keep the substantive isolation boundary: networknone at target creation, workers only join the exact owned target namespace; no external listener/port/bind/host socket; UID10001/capabilities0/NNP/ROroot; original-host deadline; independent live CID/PID/start/image and both kernel namespace observations. Do not change the PostgreSQL factory defaults or grant NET_ADMIN/NET_RAW/SYS_ADMIN, create privileged setup processes, alter the Docker VM/kernel/modules, remove interfaces from existing namespaces, or touch remote services.

Replace raw directory-name equality with a bounded explicit Linux link/address/route observation. Independently pinned code/profile, never archive policy, permits lo plus only a subset of the nine named defaults above. Require exact expected name/type/flags/iflink/operstate, zero hardware addresses, no addresses, no routes, and no external peer/underlying link for every extra device. Obtain and validate kernel link kind as well, especially Ethernet-like ARPHRD1 gretap/erspan; names alone cannot authorize acceptance. Reject if an expected kind cannot be independently established. Bounded rtnetlink GETLINK/GETADDR/GETROUTE observations are read-only, no outbound connection; current kernel/uapi sources must be checked before implementation. Reject unknown/down eth/veth/dummy devices, unknown sysfs controls, malformed/duplicate records, activated fallback devices, any nonloopback address or route, and all drift before/after provenance hashing.

Treat bonding_masters only as a separately verified root-owned regular sysfs control attribute, never silently filter arbitrary non-directory names. Compare actual interface list from independent kernel views rather than interpreting every sysfs entry as an interface.

### Closed route profile

Observe both address families and every routing table via bounded complete kernel dumps, including rule-selected tables; do not use interface name or an absent /proc IPv4 row as the only route classification. Only fixed kernel default rule/table relationships may be accepted; reject custom rule paths, missing/truncated multipart dumps, unknown tables/types/prefixes/attributes and forwarding/nexthop/gateway attributes. No archive-selected route policy.

The reachable route allowlist is only kernel local/broadcast IPv4 entries within127/8 on the observed lo index, and kernel local IPv6 ::1/128 onlo. Require family/table/type/prefix/scope/protocol and oif match the independently fixed profile; main/default/other tables contain no usable unicast route. Forbid a usable /0, non-loopback prefix, nonlo oif, gateway, multipath, policy bypass or route via a fallback device, even when it is currently DOWN. Unknown facts reject rather than being ignored.

Captured /proc IPv6 contains two ::/0 rows onlo with flags0x00200200, metric0xffffffff, zero source/destination/nexthop and no RTF_UP, plus the local::1 row. Linux public UAPI defines0x0200 as RTF_REJECT and0x00200000 as RTF_NONEXTHOP. These rows must be explicitly represented as candidate no-route/reject sentinels, never accepted merely because oif islo or treated as reachable default routes. Their kernel origin/type/table relationship must be independently resolved against the actual kernel dump/source before a positive implementation is accepted; if it cannot be established, remain rejected. Only this exact no-route sentinel form can be contemplated, not general blackhole/unreachable defaults. Preserve kernel multiplicity/table distinctions; /proc does not expose table IDs, so identical textual rows cannot alone define duplicate logical routes. Route flags, metric and mutable reference counters are recorded separately without pretending they grant forwarding authority.

Primary UAPI references checked for interpretation only: https://raw.githubusercontent.com/torvalds/linux/master/include/uapi/linux/route.h and https://raw.githubusercontent.com/torvalds/linux/master/include/uapi/linux/ipv6_route.h . No current parsed rtnetlink or actual v3 acceptance claim exists.

Record every actual observed interface and control entry. Use a new closed wire schema/version that reports actual observed interfaces and active_interfaces=['lo'] separately. Do not continue to emit interfaces=['lo'] as if default devices were absent; do not reinterpret v2 response history. Bind independently fixed profile hash and complete normalized snapshot hash to the request and response. The private response remains self-report; it cannot create TargetHandle, VerifiedRestoreInput or host kernel-pair proof.

Before accepting: clean RED/GREEN for the exact captured metadata and all per-field/unknown/active/address/route/control/malformed/drift negatives; independent spec/code review; rebuilt current wheel/image; cold actual worker in a new original container; then mandatory new job and freshly qualified PostgreSQL target pair with independently observed matching namespace identities. Original failed worker, build image and old restore targets remain separate evidence, never promoted or replayed.

## Route B — keep exact lo-only profile

Keep the current worker/profile unchanged and use a different independently qualified Linux environment whose actual new network namespaces contain only lo and no non-interface sysfs entries. Existing Docker VM configuration, other workloads and source DB remain untouched. This requires a separate environment/capacity decision; current Mac Docker cannot claim exact lo-only acceptance.

## Decision needed

Route A is recommended for Linux compatibility while keeping explicit no-egress checks and truthful observations. It revises the approved plan language “only-loopback routes/interfaces” from physically only one interface to only loopback active/reachable, so explicit approval is required before changing the existing acceptance gate. No production deployment or desktop release is included in this amendment. The bootstrap grantor exception remains approved and separate; its implementation and actual PostgreSQL verification are still pending.

## User approval

User selected “批准路线 A，严格识别关闭的默认接口并继续验证（推荐）”. The original failed worker remains retained. Parser/transport, closed profile, new wire version, fresh worker and target-pair proof remain implementation and verification gates; approval does not convert diagnostic observations into PASS.
