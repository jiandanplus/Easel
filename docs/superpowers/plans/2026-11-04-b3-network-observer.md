# B3 approved inactive fallback observation implementation

Current branch codex/ui-redesign. User-approved amendment: docs/superpowers/specs/2026-11-04-b3-linux-fallback-interface-amendment.md; exactproposal/approval under outputs/_run/b3-worker-artifact-preparation-20261104/network-amendment. No production/remote/UI changes; bootstrap amendment remains separate. Do not reinterpret original failed cold worker as PASS or rerun old resources.

## N1 pure binary parser

Implement netlink_contract.py and unit tests from /private/tmp/easel-b3-netlink-parser-20261104-brief.md. Pure data: bounded multipart/framing/attributes/typed headers only. No socket, resource or profile acceptance. Original request selector/seq/localreceiverport bind every response. Error/interruption/filtering/truncation/overrun/unknown flags reject; exactly one final DONE0. No retry. Actual public Root fixture is data for tests, not a live namespace proof. Separate independent source review.

## N2 original-clock read-only transport

Linux-only fixed GETLINK/GETADDR/GETROUTE4/6/GETRULE4/6, no mutation selectors, no outbound network endpoint. Before socket construction retain original object/custody; bind no multicast, get actual localportid, kernelpeer(0,0); exact send, bounded recvmsg with MSG_TRUNC/CTRUNC rejection and deadline before/after. All calls use same worker-local remaining interval, never renew. No socket connect/Internet DNS, namespace switching or NET_ADMIN/NET_RAW. Pass only full finished dumps into N1. Unknown errors preserve non-success, no automaticdump retry. Simulated-primitive tests + source review before Root actual use. Import remains effect-free; worker attestation entrypoint is the only production caller.

## N3 closed network profile

Freeze fixed source-code profile and its hash independently of archive/observations. The observed kernel is6.12.76-linuxkit; reference v6.12.76 upstream is evidence about semantics, not proof of vendor kernel bytes. Allow onlylo plus exact subset of nine approved DOWN fallbacks; full name/type/kind/flags/peer/underlay/tunnel-config/address/routing checks, unknown metadata rejects. Distinguish regularrootowned bonding_masters sysfs control from actualinterface. Compare typed sysfs/proc/netlink views within the SAME actual worker; captures from different diagnostic containers cannot form a live combined snapshot. Record actual interface inventory and activeinterface inventory separately, with bounded full snapshot. Only known telemetry is excluded from security equality and remains rawobserved; no silent removal of security facts.

Root actual capture already records10links,2addresses,IPv4routes3,IPv6routes1,IPv4rules3,IPv6rules2. GETLINK identifies gretap/erspan kinds explicitly rather than name/type alone. ADDRESS defaultlo is127.0.0.1/8 and::1/128,scope254/IFA_F_PERMANENT128. NETLINK IPv4routes are exactkernel/table255:127/8 local scope254,127.0.0.1/32local scope254,127.255.255.255/32broadcast scope253; allsource127.0.0.1/ifindex1/proto2/flags0. IPv6table255::1/128localproto2/scope0/flags0/ifindex1/priority0,pref0,cacheinfoallzero. Rules:IPv4(0->255,32766->254,32767->253),IPv6(0->255,32766->254),actionTO_TBL1,allzeroheaderselectors,default suppress_prefixlen0xffffffff/protocol2. These observations guide a separately reviewed exactprofile; they do not themselves authorize arbitrary copied rules/attrs.

Actual /proc IPv6 additionally has two::/0 rowsflags0x00200200/metric0xffffffff/lo/zeroaddresses but fullRTM_GETROUTE IPv6 contains only::1. Matching upstream route.c284-303 defines kernel null entries asREJECT|NONEXTHOP/RTN_UNREACHABLE/ENETUNREACH;rt6_dump_route5972-5981 omitsfib6_null_entry. ip6_fib.c2551-2590 proc formatter printsfib6flags+metric+device without thatfilter. Primarysources/version/hashes archived linux-source-reference.safe.json. Treat classification as supported by bothactualdump/source semantics; do not accept arbitraryreject/defaults, or claimcompletevendorattestation. Unexpected flags, multiplicity, source/table relationships reject.

## N4 truthful v3 protocol and worker

New complete closed request/response version; no v2positive reinterpretation. Bind Rootfixedprofilehash plusactualcanonicalsecuritysnapshothash. Actualinterfaces,activeinterfaces andsysfscontrolentries reportedtruthfully. Fullbefore/aftersecurityrecheck continues originaldeadline andactualoriginverification. Hoststillindependentlybinds liveoriginalTargetHandle/VerifiedRestoreInput/CID/PID/start/image and bothkernelnamespaceidentities. Updateper-field negatives and freezes; independent review.

## N5 real artifact and target qualification

New currentwheel/image fromfrozenofflineinputs; preserveoriginalfailedimageandworker. New coldworker, then neworiginaljob/newqualifiedPGpair; no oldclock/targetreuse. Rootobserve actualkernel/profile/provenance/noegress/resource/termination andsrcpostcheck. ATTESTalone isnotSQLrestore orfullB3. Continue approvedbootstraprolehandover,remainingTasks4-9/app/fullmatrix afterward.

## N4 bounded-result transport follow-up

The current v2 worker response is small; a truthful v3 full security snapshot may exceed PIPE_BUF. Before accepting v3, make the original stdout FD nonblocking (or prove fixed atomic chunk size) and preserve the same deadline/offset through readiness/partial-write/EAGAIN handling. No blocking large os.write after readiness may bypass the original interval. Add meaningful partial-progress/full-pipe/broken-pipe/deadline tests against fake primitives; Root actual host draining remains a separate original-process/custody qualification. Keep static nonblocking stderr rejection behavior. This is a required consequence of the new payload, not a claim of an observed current v2 positive-write failure.
