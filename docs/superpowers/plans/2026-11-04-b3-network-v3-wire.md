# N4b v3 wire design

This specifies the next source task under the approved Route A, after the N4a reader is reviewed. No runtime or target acceptance is asserted. Existing `execution_contract` objects remain DATA. The host execution owner must retain its own original resources and independent observation; a request or parsed response never grants that authority.

## Independent pins

Extend the complete `ExecutionPolicy` with `network_profile_sha256` and `network_security_sha256`. The former must equal the independently frozen source profile domain SHA256 `b803b4094d8254da45b7ca9c4666d2875794d18532fc721c87488bd64c245582`; the latter is a lower-case SHA256 pin. It is not a global fixture digest: the security view includes the actual namespace identity and permanent interface identity. Before actual dispatch, Root's future host owner must acquire the expected security pin through a separate fixed reader bound to the original live owned target namespace and current image/source, and retain that provenance. Adopting the worker response, a caller-supplied result, or the test fixture to establish the expected pin is forbidden. Worker-side policy decoding itself does not claim to verify this host provenance.

Request `easel.b3.worker-request.v3` otherwise retains the exact existing complete binding, complete policy, remaining duration, policy hash and manifest hash. Manifest stays `easel.b3.worker-manifest.v1`: its execution plan is unchanged and the complete policy/request still hashes the new pins. Reject all v1/v2 requests and responses rather than reinterpreting them.

## Complete raw observation adapter

Add pure closed wire adapters, preferably a separate `network_wire.py` to keep the execution contract focused. Their API is `encode_network_observation(observation) -> dict` and `decode_network_observation(value) -> NetworkObservation`. They create only data and perform no I/O or resource discovery. Both directions validate by the existing pure N3 validator; parsed netlink caches are regenerated from the original raw datagrams through N1, never supplied on the wire.

The object has exactly `kernel_release`, `byteorder`, `namespace_before`, `namespace_after`, `sysfs_before`, `sysfs_after`, `proc_before`, `proc_after`, `dumps`. Namespace has exactly integer `device`, `inode`. Sysfs entries are arrays preserving raw source order: interface entry exactly `tag='interface'`, `name`, `index`, `iflink`, `arphrd`, `flags`, `operstate`, `address_hex`; control entry exactly `tag='control'`, `name`, `uid`, `gid`, `mode`, `file_type`. Proc object has exactly `dev`, `addresses6`, `route4`, `route6`, `fib_trie`; each value is lowercase hexadecimal encoding of the original bytes. A dump has exactly `request_type`, `family`, `sequence`, `local_portid`, `datagrams`; datagrams is an array of lowercase hexadecimal strings preserving packet order. No cached parsed message, expected policy, snapshot, profile result, arbitrary selector or extra metadata field is accepted.

Check exact built-in types, field closure and all lengths/counts before allocating decoded bytes or constructing records. Keep N3 limits (six dumps, maximum 256 packets/slot, 65,536 bytes/packet, 8 MiB aggregate, 65,536 bytes/proc view, eleven sysfs entries) and the execution result's existing overall limit. Hex must be ASCII lowercase canonical even-length, with no spaces or prefix. N3 still enforces exact semantics and the actual narrower supported profile. No runtime fixture, policy JSON or reference generator is imported. Errors remain constant and suppress input/chained exception details.

## v3 evidence

Retain existing request/binding/manifest/architecture/artifact/build/entrypoint/tool/executable/package-origin hashes, `worker_netns`, UID/GID/capability/NNP fields. Replace the untruthful v2 `interfaces=['lo'],routes=[]` fields with these exact fields:

- `network_before` and `network_after`: the two complete raw observations above, taken on either side of installed-provenance hashing.
- `network_profile_sha256`, `network_security_sha256`: derived by N3 from both observations; both must equal the original complete host policy pins.
- `network_security_snapshot`: the complete parsed canonical security JSON produced by N3. Its bytes must match both observations' complete N3 canonical security bytes; namespace must equal `worker_netns`.
- `observed_interfaces`, `active_interfaces`, `sysfs_controls`: derived exact arrays from both N3 results. Controls use the same closed control form as the raw adapter.
- `network_before_raw_sha256`, `network_after_raw_sha256`: derived from the corresponding original observations, never accepted as independent evidence.

The envelope is exactly `format='easel.b3.worker-attestation.v3'`, `operation='attest_worker'`, `evidence`. On encode and on validation against the original request/policy, decode and independently validate both full observations, derive the entire expected evidence, and use typed exact equality for every supplied field. Telemetry changes are allowed only where N3 permits them, remain in original raw bytes, and may make raw hashes differ. Snapshot/security hash equality does not imply an atomic view. The summary becomes `easel.b3.worker-attestation-summary.v3`, with its existing hash-only fields and no new authority claim.

## Worker behavior

Call the fixed N4a reader and pure validator before installed provenance hashing and again after it. Preserve actual namespace/capabilities/NNP/cgroup checks, compare stable process/resource metadata and canonical network security, and reject all drift or unsupported observations. Remove the v2 name-only and loose route logic; do not retain mutable route reference counts in a supposedly stable metadata hash. Root's future independent host checks of both namespaces remain mandatory.

Before a positive write, set stdout FD 1 nonblocking under the same original deadline and verify `os.get_blocking(1) is False`, retain offset through partial writes, EINTR and EAGAIN, wait for readiness under that deadline, reject broken/zero/oversize writes and stop on expiry. Do not renew the deadline or replay a write after unknown progress. Preserve the fixed best-effort stderr diagnostic and no-success-on-exception main behavior.

## Verification scope

Meaningful source tests cover full two-pass SAME fixture plus independently varied permissible counters, exact wire closure/type/hex/bounds, every new evidence/policy pin, request/response version rejection, original request mismatch, namespace/static mismatch, full snapshot/counter/raw-hash tampering, and stdout setup/partial/EINTR/EAGAIN/full-pipe/broken-pipe/original-deadline failures. Existing build/origin and UID/capability/cgroup regressions stay active. Tests are fake-primitive/data only. Source review precedes any new wheel, native cold worker or target qualification. Actual cold worker's expected baseline requires the separate provenance-bound observer above; an arbitrary expected hash cannot be substituted to obtain a passing run.
