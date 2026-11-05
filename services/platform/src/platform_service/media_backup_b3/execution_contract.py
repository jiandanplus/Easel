"""Closed v3 worker attestation data contract; no execution or live authority.

The manifest is a plan projection, NOT a complete Docker inspect validator.
Root supplies independent resource limits, not measured capacity or admission.
Attestation is private self-report, never sufficient isolation/runtime proof.
The future host executor must retain original handles, measure resources and
independently observe container, process and kernel namespace identities.
Artifact/executable/origin hashes are data pins, not proof imports were observed.
No deserialization here creates a TargetHandle or VerifiedRestoreInput.
"""
from dataclasses import dataclass, fields
from enum import Enum
import hashlib
import json
import re

from .contracts import B3Error
from .network_wire import decode_network_observation
from .network_profile import validate_network_profile

_NETWORK_PROFILE = "b803b4094d8254da45b7ca9c4666d2875794d18532fc721c87488bd64c245582"


def _require(condition):
    if not condition:
        raise B3Error("invalid B3 execution contract")


def _hash(value):
    _require(type(value) is str and re.fullmatch(r"[a-f0-9]{64}", value, re.ASCII) is not None)


def _image(value):
    _require(type(value) is str and value.startswith("sha256:"))
    _hash(value[7:])


def _integer(value, minimum=1, maximum=None):
    _require(type(value) is int and value >= minimum and (maximum is None or value <= maximum))


class Operation(Enum):
    ATTEST_WORKER = "attest_worker"


@dataclass(frozen=True, repr=False)
class ExecutionBinding:
    """Plain independently held policy data; no freshness or capability claim."""
    restore_id: str
    operation_id: str
    kind: str
    operation: Operation
    source_container_id: str
    target_container_id: str
    target_identity_sha256: str
    verified_input_sha256: str
    context_sha256: str
    started_at_ns: int
    deadline_ns: int
    job_device: int
    job_inode: int

    def __repr__(self):
        return "<ExecutionBinding: private policy data>"

    def __post_init__(self):
        for value in (self.restore_id, self.operation_id):
            _require(type(value) is str and re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_-]{0,95}", value, re.ASCII) is not None)
        _require(type(self.kind) is str and self.kind in ("central", "identity"))
        _require(type(self.operation) is Operation and self.operation is Operation.ATTEST_WORKER)
        for value in (self.source_container_id, self.target_container_id, self.target_identity_sha256,
                      self.verified_input_sha256, self.context_sha256):
            _hash(value)
        _require(self.source_container_id != self.target_container_id)
        _integer(self.started_at_ns, 0, 2**63-1)
        _integer(self.deadline_ns, 1, 2**63-1)
        _require(0 < self.deadline_ns-self.started_at_ns <= 1800*10**9)
        _integer(self.job_device, 0)
        _integer(self.job_inode)


@dataclass(frozen=True, repr=False)
class ExecutionPolicy:
    """Root's independent limits and pins; not runtime qualification/admission."""
    binding: ExecutionBinding
    worker_image_id: str
    worker_artifact_sha256: str
    dependency_manifest_sha256: str
    entrypoint_sha256: str
    pg_restore_sha256: str
    architecture: str
    network_profile_sha256: str
    network_security_sha256: str
    cpu_nanos: int
    memory_bytes: int
    memory_swap_bytes: int
    pids_limit: int
    tmpfs_bytes: int
    envelope_limit: int
    private_result_limit: int
    diagnostic_limit: int

    def __repr__(self):
        return "<ExecutionPolicy: private policy data>"

    def __post_init__(self):
        _require(type(self.binding) is ExecutionBinding)
        self.binding.__post_init__()
        _image(self.worker_image_id)
        _require(type(self.network_profile_sha256) is str and self.network_profile_sha256 == _NETWORK_PROFILE)
        _hash(self.network_security_sha256)
        for value in (self.worker_artifact_sha256, self.dependency_manifest_sha256,
                      self.entrypoint_sha256, self.pg_restore_sha256):
            _hash(value)
        _require(type(self.architecture) is str and self.architecture in ("arm64", "amd64"))
        for value, maximum in ((self.cpu_nanos, 2*10**9), (self.memory_bytes, 2*1024**3),
                               (self.memory_swap_bytes, 2*1024**3), (self.pids_limit, 128),
                               (self.tmpfs_bytes, 256*1024**2), (self.envelope_limit, 64*1024),
                               (self.private_result_limit, 1024**2), (self.diagnostic_limit, 64*1024)):
            _integer(value, 1, maximum)
        _require(self.memory_swap_bytes == self.memory_bytes)


def _policy(policy):
    _require(type(policy) is ExecutionPolicy)
    try:
        policy.__post_init__()
    except (AttributeError, TypeError):
        raise B3Error("invalid B3 execution contract") from None


def _binding_data(binding):
    return {field.name: (binding.operation.value if field.name == "operation" else getattr(binding, field.name))
            for field in fields(ExecutionBinding)}


def _policy_data(policy):
    return {field.name: (_binding_data(policy.binding) if field.name == "binding" else getattr(policy, field.name))
            for field in fields(ExecutionPolicy)}


def _tree(value, depth=0):
    _require(depth <= 24)
    if type(value) is dict:
        for key, child in value.items():
            _require(type(key) is str)
            _tree(key, depth+1)
            _tree(child, depth+1)
    elif type(value) is list:
        for child in value:
            _tree(child, depth+1)
    elif type(value) is str:
        _require("\x00" not in value and not any(0xd800 <= ord(c) <= 0xdfff for c in value))
    else:
        _require(type(value) in (int, bool) or value is None)


def _canonical(value):
    try:
        _tree(value)
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise B3Error("invalid B3 execution contract") from None


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _exact(actual, expected):
    """Typed structural equality: Python bool/int aliases are forbidden."""
    _require(type(actual) is type(expected))
    if type(expected) is dict:
        _require(all(type(k) is str for k in actual) and actual.keys() == expected.keys())
        for key in expected:
            _exact(actual[key], expected[key])
    elif type(expected) is list:
        _require(len(actual) == len(expected))
        for a, b in zip(actual, expected):
            _exact(a, b)
    else:
        _require(actual == expected)


def build_worker_manifest(policy):
    """Return a fresh closed plan, not validation of actual Docker inspect."""
    _policy(policy)
    return dict(format="easel.b3.worker-manifest.v1", image=policy.worker_image_id,
                architecture=policy.architecture, network_mode="container:"+policy.binding.target_container_id,
                user="10001:10001", entrypoint=["/opt/easel/bin/python", "-I", "-B", "-m",
                "platform_service.media_backup_b3.execution_worker"], command=["attest_worker"],
                readonly_rootfs=True, privileged=False, cap_drop=["ALL"], cap_add=[],
                security_opt=["no-new-privileges"], cpu_nanos=policy.cpu_nanos,
                memory_bytes=policy.memory_bytes, memory_swap_bytes=policy.memory_swap_bytes,
                pids_limit=policy.pids_limit,
                tmpfs={"/run/easel":"rw,nosuid,nodev,noexec,size="+str(policy.tmpfs_bytes)},
                mounts=[], ports={}, environment=[])


def validate_worker_manifest(policy, manifest):
    """Exact typed plan projection only; never a full runtime qualification."""
    _exact(manifest, build_worker_manifest(policy))
    return _canonical(manifest)


def _request(policy, remaining_ns):
    _policy(policy)
    _integer(remaining_ns, 1, policy.binding.deadline_ns-policy.binding.started_at_ns)
    return dict(format="easel.b3.worker-request.v3", operation="attest_worker",
                policy=_policy_data(policy), remaining_ns=remaining_ns,
                policy_sha256=_digest(_policy_data(policy)),
                manifest_sha256=_digest(build_worker_manifest(policy)))


@dataclass(frozen=True, repr=False)
class ParsedAttestRequest:
    """Validated plain request data, never authority or a runtime success flag.

    Publicly constructed records are revalidated against their canonical bytes
    before use. remaining_ns is a dispatch duration, not a Linux clock reading;
    the host retains its original deadline and all independent resource handles.
    """
    canonical_request: bytes
    policy: ExecutionPolicy
    remaining_ns: int

    def __repr__(self):
        return "<ParsedAttestRequest: private request data>"

    @property
    def request_sha256(self):
        _parsed_request(self)
        return hashlib.sha256(self.canonical_request).hexdigest()


def _parsed_request(parsed):
    _require(type(parsed) is ParsedAttestRequest)
    try:
        expected = _canonical(_request(parsed.policy, parsed.remaining_ns))
        _require(type(parsed.canonical_request) is bytes and parsed.canonical_request == expected)
        _require(len(expected) <= parsed.policy.envelope_limit)
    except AttributeError:
        raise B3Error("invalid B3 execution contract") from None


def _closed(value, keys):
    _require(type(value) is dict and all(type(k) is str for k in value) and value.keys() == set(keys))


def _decode_policy(value):
    # Check complete closed nested schemas before any dataclass construction.
    _closed(value, (field.name for field in fields(ExecutionPolicy)))
    binding = value["binding"]
    _closed(binding, (field.name for field in fields(ExecutionBinding)))
    for field in fields(ExecutionBinding):
        _require(type(binding[field.name]) is (str if field.name == "operation" else field.type))
    for field in fields(ExecutionPolicy):
        if field.name != "binding":
            _require(type(value[field.name]) is field.type)
    _exact(binding["operation"], "attest_worker")
    binding = ExecutionBinding(**{**binding, "operation": Operation.ATTEST_WORKER})
    return ExecutionPolicy(**{**value, "binding": binding})


def _frame(value, limit):
    payload = _canonical(value)
    _require(len(payload) <= limit)
    return len(payload).to_bytes(4, "big") + payload


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _bad_number(value):
    raise B3Error("invalid B3 execution contract")


def _unframe(raw, limit):
    _require(type(raw) is bytes and len(raw) >= 4)
    size = int.from_bytes(raw[:4], "big")
    # Check before payload copy/UTF8 decode/JSON parsing. Exactly one frame.
    _require(0 < size <= limit and len(raw) == size+4)
    try:
        value = json.loads(raw[4:].decode("utf-8"), object_pairs_hook=_pairs,
                           parse_float=_bad_number, parse_constant=_bad_number)
        _tree(value)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise B3Error("invalid B3 execution contract") from None


def encode_attest_request(policy, remaining_ns):
    """Encode complete policy over the sole framed stdin channel."""
    return _frame(_request(policy, remaining_ns), policy.envelope_limit)


def decode_worker_request(raw):
    """Decode plain policy data without independent host policy or authority."""
    value = _unframe(raw, 64*1024)
    _closed(value, ("format", "operation", "policy", "policy_sha256", "manifest_sha256", "remaining_ns"))
    _exact(value["format"], "easel.b3.worker-request.v3")
    _exact(value["operation"], "attest_worker")
    policy = _decode_policy(value["policy"])
    _require(len(raw)-4 <= policy.envelope_limit)
    _exact(value, _request(policy, value["remaining_ns"]))
    return ParsedAttestRequest(_canonical(value), policy, value["remaining_ns"])


def decode_attest_request(raw, expected_policy):
    """Check the independently held complete host policy; return immutable bytes."""
    _policy(expected_policy)
    parsed = decode_worker_request(raw)
    _exact(_policy_data(parsed.policy), _policy_data(expected_policy))
    return parsed.canonical_request


def _network_evidence(before, after):
    """Derive complete DATA evidence afresh; never accept a result/hash as authority."""
    try:
        a = validate_network_profile(decode_network_observation(before))
        b = validate_network_profile(decode_network_observation(after))
        _require(a.canonical_security_json == b.canonical_security_json)
        _require(a.profile_sha256 == b.profile_sha256 and a.security_sha256 == b.security_sha256)
        def control(e):
            return dict(tag="control", name=e.name, uid=e.uid, gid=e.gid, mode=e.mode, file_type=e.file_type)
        _require(a.observed_interfaces == b.observed_interfaces and a.active_interfaces == b.active_interfaces and a.sysfs_controls == b.sysfs_controls)
        return dict(network_before=before, network_after=after,
                    network_profile_sha256=a.profile_sha256, network_security_sha256=a.security_sha256,
                    network_security_snapshot=json.loads(a.canonical_security_json),
                    observed_interfaces=list(a.observed_interfaces), active_interfaces=list(a.active_interfaces),
                    sysfs_controls=[control(e) for e in a.sysfs_controls],
                    network_before_raw_sha256=a.raw_observation_sha256, network_after_raw_sha256=b.raw_observation_sha256)
    except Exception:
        raise B3Error("invalid B3 execution contract") from None


def _attestation(parsed, evidence):
    _parsed_request(parsed)
    policy = parsed.policy
    # The worker observes only its own namespace. The host must independently
    # bind this response stream to its original container/image and observe BOTH
    # namespaces; no expected identity is echoed here as an actual observation.
    _require(type(evidence) is dict)
    _tree(evidence)  # Type-check every nested key before membership or lookup.
    dynamic = ("executable_sha256", "package_origins_sha256", "worker_netns")
    _require(all(key in evidence for key in dynamic))
    for key in dynamic[:2]:
        _hash(evidence[key])
    ns = evidence["worker_netns"]
    _closed(ns, ("device", "inode"))
    _integer(ns["device"])
    _integer(ns["inode"])
    expected = dict(binding_sha256=_digest(_binding_data(policy.binding)),
                    policy_sha256=_digest(_policy_data(policy)), request_sha256=parsed.request_sha256,
                    manifest_sha256=_digest(build_worker_manifest(policy)), architecture=policy.architecture,
                    artifact_sha256=policy.worker_artifact_sha256,
                    dependency_manifest_sha256=policy.dependency_manifest_sha256,
                    entrypoint_sha256=policy.entrypoint_sha256, pg_restore_sha256=policy.pg_restore_sha256,
                    uid=10001, gid=10001,
                    cap_effective=0, cap_permitted=0, cap_inheritable=0, cap_ambient=0, no_new_privileges=True)
    _require("network_before" in evidence and "network_after" in evidence)
    network = _network_evidence(evidence["network_before"], evidence["network_after"])
    _exact(network["network_profile_sha256"], policy.network_profile_sha256)
    _exact(network["network_security_sha256"], policy.network_security_sha256)
    _exact(network["network_security_snapshot"]["namespace"], ns)
    expected.update(network)
    expected.update({key:evidence[key] for key in dynamic})
    _exact(evidence, expected)
    return dict(format="easel.b3.worker-attestation.v3", operation="attest_worker", evidence=evidence)


def encode_attestation(parsed_request, evidence):
    """Frame closed request-bound private self-report; no execution proof."""
    value = _attestation(parsed_request, evidence)
    return _frame(value, parsed_request.policy.private_result_limit)


def validate_attestation(expected_policy, original_request_raw, response_raw):
    """Validate all pins against the host's original policy AND request bytes."""
    canonical = decode_attest_request(original_request_raw, expected_policy)
    parsed = decode_worker_request(original_request_raw)
    _require(canonical == parsed.canonical_request)
    value = _unframe(response_raw, expected_policy.private_result_limit)
    _closed(value, ("format", "operation", "evidence"))
    _exact(value, _attestation(parsed, value["evidence"]))
    return _canonical(value)


def attestation_summary(expected_policy, original_request_raw, response_raw):
    """Hashes only; parsing never proves runtime isolation or creates authority."""
    private = validate_attestation(expected_policy, original_request_raw, response_raw)
    request = decode_attest_request(original_request_raw, expected_policy)
    return dict(format="easel.b3.worker-attestation-summary.v3", operation="attest_worker",
                binding_sha256=_digest(_binding_data(expected_policy.binding)),
                manifest_sha256=_digest(build_worker_manifest(expected_policy)),
                request_sha256=hashlib.sha256(request).hexdigest(),
                private_attestation_sha256=hashlib.sha256(private).hexdigest())
