# New isolated source-service phase after the retained once001 failure

This is a distinct, reviewed local synthetic-source job. The previous attempt stays failed with its original intent, deadline, probe, private network and evidence. It is not resumed or replayed. No production/user account data, model calls, deployment or timers. Current branch only; no commit/push/merge.

Parent evidence: `outputs/_run/owned-pg-disk-fixture-20261103/once001-safe/manifest.safe.json`, SHA256 `c9277a26e0d4aa2efdb365436f16608d28c897a3b823e96f23a5801e5d051baa`. Root observed no planned source container/volume and all completed clients terminal0. Failure was an exact Options-map predicate rejecting Engine-populated safe IPv4/IPv6 flags, not a failed/unknown create operation. Original source7bed stays stopped and untouched.

The next source phase reuses only the already created exact empty private network `84e353779bed4c5348285ee22ddd5a69fc50aabc48308d68d2341cc4829817e6`, name `easel-b2-disk-b37bae3748c6f0993c99ed16067049b5-net`, labels purpose `b2-owned-regression-source` and nonce `b37bae3748c6f0993c99ed16067049b5`. Never create/restart/delete this network or the old probe. Fresh source/probe/volume names and credentials use a new job nonce. No prior mutable action is replayed; source creation has never run.

Closed network policy: exact full ID/name/labels, internal bridge, exact attachment set; Options keys subset of loopback-binding plus enable_ipv4/enable_ipv6, binding mandatory127.0.0.1, optional flags exactly strings true/false respectively; top-level EnableIPv4 is True / EnableIPv6 is False. No other keys/coercion. Before source start network empty; after start and before SQL/final attachment exactly new source CID.

Reuse reviewed helper source `outputs/_run/owned-pg-disk-fixture-20261103/prepare_service.py` only at SHA256 `463d776fa97195512c57ec3387637a0a46073ddfab472e847f8f62a770adf441`. Do not edit it or its failed outputs. New wrapper only under `outputs/_run/owned-pg-source-completion-20261103/`. Helper imports are inert; actual resource actor remains Root. Do not call its prepare() or its old network validator. Reuse exact cgroup/secret/runner/source/probe/SQL predicates; wrapper supplies closed network policy.

## Sequence

1. Code-only implementation and independent review; fake process/inspect/FS tests only. No actual Docker/SQL/key reads by actors.
2. Root `prepare`: new private nonce directory0700/currentUID/**currentGID before children**, fresh synthetic credentials, fresh public sealed manifest with full commands/names/image/hash/limits and parent evidence references. New job original start/deadline240seconds, no reset. Prepare starts no subprocess or resources. Root reads concrete manifest before execute. Existing failed records remain immutable.
3. Root `execute`: exclusive once execution marker; same frozen manifest/input hashes/deadline and helper/source hashes. Verify current host disk/RAM, old group absent, old source exact terminal, current image, parent evidence, exact reused network(empty) and absence of new names. Never read old DSN/password or old failed env. Username-only synthetic identifier is the only old logical input, pinned by existing fixed SHA.
4. One **new**96MiB/.25CPU/PID16/no-network/capless/RO/UID999 probe gives current VM metrics under the same bounded20-second subinterval. It is a separate admission observation, not a restart of the old successful probe. Retain it and all objects on failure; no automatic retry/cleanup.
5. Create only the new named volume and source container, inspect exact identities/limits/mount/env/requested port before first start, then one start, strict materialized network/loopback checks and independent exact attachment before SQL. Use source1CPU/1536MiB memory and same total-swap/PID128/RO/NNP/five exact bootstrap caps; PGDATA named volume, normal durability/default100connections/PG16. Use private read-only SQL worker under original deadline. Final fresh inspect, old-source metadata comparison, then descriptor and service_ready.
6. Root evidence review/adoption; readiness proves source only. Synthetic Logto145 initialization, actual full authenticated-stage positive, full installed regression and B3 target restoration remain separate.

No newly executed resource action is authorized by a code reviewer. Root's existing user-authorized local synthetic verification scope and concrete reviewed manifest govern actual execution. Unknown/failed job never auto-replays; observe original handles and preserve evidence. This does not extend the previous failed job's240-second deadline or mark it successful.

Status: prepared plan only; wrapper, tests, review and actual execution pending.
