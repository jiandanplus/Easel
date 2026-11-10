"""Synthetic parsing proof only; no app import, external calls or resources."""
from test_media_backup_b3_network_fixtures import PROFILE, SECURITY, observation, network_evidence
import copy
import dataclasses
import hashlib
import importlib
import json
from pathlib import Path
import sys
import types
import unittest
from unittest import mock


def _forbid_external_calls(event, args):
    if event.startswith(("socket.", "subprocess.")) or event in ("os.system", "os.fork", "os.posix_spawn", "os.exec"):
        raise AssertionError("external calls prohibited in pure protocol tests")


ROOT = Path(__file__).resolve().parents[1] / "src" / "platform_service"
for name, path in (("platform_service", ROOT), ("platform_service.media_backup_b3", ROOT / "media_backup_b3")):
    if name not in sys.modules:
        package = types.ModuleType(name)
        package.__path__ = [str(path)]
        sys.modules[name] = package
m = importlib.import_module("platform_service.media_backup_b3.execution_contract")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def frame(value):
    payload = canonical(value)
    return len(payload).to_bytes(4, "big") + payload


def binding(**overrides):
    fields = dict(restore_id="synthetic_job", operation_id="synthetic_attest", kind="central",
                  operation=m.Operation.ATTEST_WORKER, source_container_id="1"*64,
                  target_container_id="2"*64, target_identity_sha256="3"*64,
                  verified_input_sha256="4"*64, context_sha256="5"*64,
                  started_at_ns=0, deadline_ns=1800*10**9, job_device=0, job_inode=123)
    fields.update(overrides)
    return m.ExecutionBinding(**fields)


def policy(**overrides):
    fields = dict(binding=binding(), worker_image_id="sha256:"+"6"*64,
                  worker_artifact_sha256="7"*64, dependency_manifest_sha256="8"*64,
                  entrypoint_sha256="9"*64, pg_restore_sha256="a"*64, architecture="arm64",
        network_profile_sha256=PROFILE,network_security_sha256=SECURITY,
                  cpu_nanos=10**9, memory_bytes=512*1024**2, memory_swap_bytes=512*1024**2,
                  pids_limit=64, tmpfs_bytes=16*1024**2, envelope_limit=65536,
                  private_result_limit=1024**2, diagnostic_limit=65536)
    fields.update(overrides)
    return m.ExecutionPolicy(**fields)


def manifest_fixture():
    # Independent literal expected plan. No builder-derived expected values.
    return dict(format="easel.b3.worker-manifest.v1", image="sha256:"+"6"*64,
                architecture="arm64", network_mode="container:"+"2"*64, user="10001:10001",
                entrypoint=["/opt/easel/bin/python", "-I", "-B", "-m",
                            "platform_service.media_backup_b3.execution_worker"], command=["attest_worker"],
                readonly_rootfs=True, privileged=False, cap_drop=["ALL"], cap_add=[],
                security_opt=["no-new-privileges"], cpu_nanos=10**9,
                memory_bytes=512*1024**2, memory_swap_bytes=512*1024**2, pids_limit=64,
                tmpfs={"/run/easel":"rw,nosuid,nodev,noexec,size=16777216"}, mounts=[], ports={}, environment=[])


def request_fixture(p, remaining_ns=123):
    # Independent closed wire schema, not production serialization helpers.
    b = dict(restore_id=p.binding.restore_id, operation_id=p.binding.operation_id,
             kind=p.binding.kind, operation="attest_worker", source_container_id=p.binding.source_container_id,
             target_container_id=p.binding.target_container_id, target_identity_sha256=p.binding.target_identity_sha256,
             verified_input_sha256=p.binding.verified_input_sha256, context_sha256=p.binding.context_sha256,
             started_at_ns=p.binding.started_at_ns, deadline_ns=p.binding.deadline_ns,
             job_device=p.binding.job_device, job_inode=p.binding.job_inode)
    full_policy = dict(binding=b, worker_image_id=p.worker_image_id, worker_artifact_sha256=p.worker_artifact_sha256,
                       dependency_manifest_sha256=p.dependency_manifest_sha256, entrypoint_sha256=p.entrypoint_sha256,
                       pg_restore_sha256=p.pg_restore_sha256, architecture=p.architecture,
                       network_profile_sha256=p.network_profile_sha256,network_security_sha256=p.network_security_sha256, cpu_nanos=p.cpu_nanos,
                       memory_bytes=p.memory_bytes, memory_swap_bytes=p.memory_swap_bytes, pids_limit=p.pids_limit,
                       tmpfs_bytes=p.tmpfs_bytes, envelope_limit=p.envelope_limit,
                       private_result_limit=p.private_result_limit, diagnostic_limit=p.diagnostic_limit)
    return dict(format="easel.b3.worker-request.v3", operation="attest_worker", policy=full_policy, remaining_ns=remaining_ns,
                policy_sha256=digest(full_policy), manifest_sha256=digest(manifest_fixture()))


def evidence_fixture(p, remaining_ns=123):
    return dict(binding_sha256=digest(request_fixture(p)["policy"]["binding"]), manifest_sha256=digest(manifest_fixture()),
                policy_sha256=request_fixture(p)["policy_sha256"], request_sha256=digest(request_fixture(p, remaining_ns)),
                architecture="arm64",
                artifact_sha256=p.worker_artifact_sha256, dependency_manifest_sha256=p.dependency_manifest_sha256,
                entrypoint_sha256=p.entrypoint_sha256, pg_restore_sha256=p.pg_restore_sha256,
                executable_sha256="c"*64, package_origins_sha256="d"*64,
                worker_netns=network_evidence()["network_security_snapshot"]["namespace"],
                **network_evidence(),
                uid=10001, gid=10001,
                cap_effective=0, cap_permitted=0, cap_inheritable=0, cap_ambient=0, no_new_privileges=True)


def attestation_fixture(p):
    return dict(format="easel.b3.worker-attestation.v3", operation="attest_worker", evidence=evidence_fixture(p))


def mutations(value, path=()):
    """One invalid change per closed field, including nested collection leaves."""
    if type(value) is dict:
        yield path, {**value, "unexpected":"private-canary"}
        for key, child in value.items():
            if key in ("network_before","network_after","network_security_snapshot"):
                yield path+(key,), {**child,"unexpected":"private-canary"}
            else:
                yield from mutations(child, path+(key,))
    elif type(value) is list:
        yield path, value+["private-canary"]
        for key, child in enumerate(value):
            if key in ("network_before","network_after","network_security_snapshot"):
                yield path+(key,), {**child,"unexpected":"private-canary"}
            else:
                yield from mutations(child, path+(key,))
    elif type(value) is bool:
        yield path, int(value)
    elif type(value) is int:
        yield path, True
    elif type(value) is str:
        yield path, value+"private-canary"


def changed(value, path, replacement):
    if not path:
        return replacement
    result = copy.deepcopy(value)
    node = result
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = replacement
    return result


class ExecutionContractTests(unittest.TestCase):
    def test_v3_required_version(self):
        self.assertEqual(json.loads(m.encode_attest_request(policy(),123)[4:])["format"], "easel.b3.worker-request.v3")

    def test_v3_worker_can_decode_without_external_policy(self):
        self.assertTrue(callable(getattr(m, "decode_worker_request", None)))
        raw = m.encode_attest_request(policy(), 123)
        parsed = m.decode_worker_request(raw)
        self.assertEqual(parsed.policy, policy())
        self.assertEqual(parsed.remaining_ns, 123)
        self.assertEqual(parsed.request_sha256, hashlib.sha256(parsed.canonical_request).hexdigest())

    def test_v3_response_is_bound_to_original_request(self):
        self.assertTrue(hasattr(m, "ParsedAttestRequest"))
        raw = m.encode_attest_request(policy(), 123)
        parsed = m.decode_worker_request(raw)
        evidence = evidence_fixture(policy())
        evidence.update(policy_sha256=digest(request_fixture(policy())["policy"]), request_sha256=parsed.request_sha256)
        for key in ("target_netns", "worker_container_id", "worker_image_id"):
            evidence.pop(key, None)
        response = m.encode_attestation(parsed, evidence)
        self.assertIs(type(m.validate_attestation(policy(), raw, response)), bytes)
        self.rejects(m.validate_attestation, policy(), m.encode_attest_request(policy(), 124), response)

    def test_v3_worker_only_namespace_evidence(self):
        self.assertEqual(attestation_fixture(policy())["format"], "easel.b3.worker-attestation.v3")
        evidence = evidence_fixture(policy())
        for key in ("target_netns", "worker_container_id", "worker_image_id"):
            self.assertNotIn(key, evidence)

    def test_v3_explicitly_rejects_v1(self):
        self.assertTrue(callable(getattr(m, "decode_worker_request", None)))
        current=request_fixture(policy())
        old=dict(format="easel.b3.worker-request.v1",operation="attest_worker",
                 binding=current["policy"]["binding"],policy_sha256=current["policy_sha256"],
                 manifest_sha256=current["manifest_sha256"])
        self.rejects(m.decode_worker_request,frame(old))
        self.rejects(m.decode_worker_request,frame({**current,"format":"easel.b3.worker-request.v1"}))

    def test_worker_complete_closed_policy_missing_extra_types_and_hashes(self):
        original = request_fixture(policy())
        for path, replacement in mutations(original):
            with self.subTest(path=path):
                self.rejects(m.decode_worker_request, frame(changed(original,path,replacement)))
        for node_path in ((), ("policy",), ("policy","binding")):
            node = original
            for key in node_path: node = node[key]
            for key in node:
                bad=copy.deepcopy(original); parent=bad
                for part in node_path: parent=parent[part]
                del parent[key]
                self.rejects(m.decode_worker_request,frame(bad))
        for path in (("policy_sha256",),("manifest_sha256",),("policy","worker_artifact_sha256"),
                     ("policy","binding","context_sha256")):
            self.rejects(m.decode_worker_request,frame(changed(original,path,"e"*64)))

    def test_nested_wire_types_checked_before_dataclass_construction(self):
        original=request_fixture(policy())
        for path in (("policy","cpu_nanos"),("policy","architecture"),("policy","binding","job_inode"),
                     ("policy","binding","operation")):
            bad=changed(original,path,True)
            with mock.patch.object(m.ExecutionBinding,"__post_init__",side_effect=AssertionError("constructed before types checked")):
                self.rejects(m.decode_worker_request,frame(bad))

    def test_worker_digest_valid_nested_policy_changes_still_require_host_policy(self):
        p=policy(); altered=policy(cpu_nanos=1)
        request=request_fixture(altered)
        manifest=manifest_fixture(); manifest["cpu_nanos"]=1
        request["manifest_sha256"]=digest(manifest)
        raw=frame(request)
        self.assertEqual(m.decode_worker_request(raw).policy,altered)
        self.rejects(m.decode_attest_request,raw,p)
        # A valid digest cannot make an invalid closed policy value acceptable.
        for path,value in ((("policy","binding","operation"),"restore"),
                           (("policy","worker_image_id"),"tag:latest"),
                           (("policy","worker_artifact_sha256"),"A"*64),
                           (("policy","memory_swap_bytes"),1)):
            bad=changed(request_fixture(p),path,value)
            bad["policy_sha256"]=digest(bad["policy"])
            self.rejects(m.decode_worker_request,frame(bad))

    def test_worker_duration_and_embedded_limit(self):
        for remaining in (0,-1,True,1800*10**9+1,1.0,"123",None):
            self.rejects(m.encode_attest_request,policy(),remaining)
            self.rejects(m.decode_worker_request,frame(request_fixture(policy(),remaining)))
        self.assertEqual(m.decode_worker_request(m.encode_attest_request(policy(),1800*10**9)).remaining_ns,1800*10**9)
        too_small=request_fixture(policy(envelope_limit=1))
        self.rejects(m.decode_worker_request,frame(too_small))
        # Prefix over the absolute cap is rejected before the JSON parser runs.
        with mock.patch.object(m.json,"loads",side_effect=AssertionError("parser reached")):
            self.rejects(m.decode_worker_request,(65537).to_bytes(4,"big")+b"x"*65537)

    def test_parsed_request_is_data_immutable_redacted_and_revalidated(self):
        raw=m.encode_attest_request(policy(),123); parsed=m.decode_worker_request(raw)
        self.assertIs(type(parsed.canonical_request),bytes)
        self.assertNotIn("synthetic_job",repr(parsed))
        with self.assertRaises(dataclasses.FrozenInstanceError): parsed.remaining_ns=124
        for overrides in ({"remaining_ns":True},{"remaining_ns":124},{"canonical_request":b"private-canary"},
                          {"policy":policy(cpu_nanos=1)},{"canonical_request":bytearray(parsed.canonical_request)}):
            forged=dataclasses.replace(parsed,**overrides)
            self.rejects(m.encode_attestation,forged,evidence_fixture(policy()))
        forged=object.__new__(m.ParsedAttestRequest)
        self.rejects(m.encode_attestation,forged,evidence_fixture(policy()))

    def test_worker_namespace_is_only_private_data(self):
        p=policy(); raw=m.encode_attest_request(p,123); parsed=m.decode_worker_request(raw)
        evidence=evidence_fixture(p); evidence["worker_netns"]=network_evidence()["network_security_snapshot"]["namespace"]
        response=m.encode_attestation(parsed,evidence)
        summary=m.attestation_summary(p,raw,response)
        self.assertFalse(any(type(value) is bool for value in summary.values()))
        for key,value in (("isolation_verified",True),("success",True),("target_netns",{"device":99,"inode":100}),
                          ("worker_container_id","b"*64),("worker_image_id",p.worker_image_id)):
            self.rejects(m.encode_attestation,parsed,{**evidence,key:value})
        old=attestation_fixture(p); old["format"]="easel.b3.worker-attestation.v1"
        self.rejects(m.validate_attestation,p,raw,frame(old))

    def test_v3_rejects_both_previous_versions_and_independent_network_pin_drift(self):
        p=policy();raw=m.encode_attest_request(p,123)
        for version in ("v1","v2"):
            request=request_fixture(p);request["format"]="easel.b3.worker-request."+version
            self.rejects(m.decode_worker_request,frame(request))
            response=attestation_fixture(p);response["format"]="easel.b3.worker-attestation."+version
            self.rejects(m.validate_attestation,p,raw,frame(response))
        for key in ("network_profile_sha256","network_security_sha256"):
            for value in (True,None,"A"*64,"private-canary"):
                self.rejects(lambda:policy(**{key:value}))
        self.rejects(lambda:policy(network_profile_sha256="e"*64))
        changed=policy(network_security_sha256="e"*64)
        self.rejects(m.decode_attest_request,raw,changed)
        parsed=m.decode_worker_request(m.encode_attest_request(changed,123))
        self.rejects(m.encode_attestation,parsed,evidence_fixture(changed))

    def test_v3_full_typed_snapshot_namespace_static_and_counter_hash_binding(self):
        p=policy();raw=m.encode_attest_request(p,123);parsed=m.decode_worker_request(raw)
        original=evidence_fixture(p)
        cases=[]
        bad=copy.deepcopy(original);bad["network_security_snapshot"]["schema"]=True;cases.append(bad)
        bad=copy.deepcopy(original);bad["network_security_snapshot"]["links"][0]["header"]["index"]+=1;cases.append(bad)
        bad=copy.deepcopy(original);bad["worker_netns"]["inode"]+=1;cases.append(bad)
        for key in ("network_profile_sha256","network_security_sha256","network_before_raw_sha256","network_after_raw_sha256"):
            bad=copy.deepcopy(original);bad[key]="e"*64;cases.append(bad)
        from test_media_backup_b3_network_fixtures import varied_observation
        from platform_service.media_backup_b3.network_wire import encode_network_observation
        bad=copy.deepcopy(original);bad["network_after"]=encode_network_observation(varied_observation());cases.append(bad)
        for bad in cases:
            self.rejects(m.encode_attestation,parsed,bad)
            self.rejects(m.validate_attestation,p,raw,frame(dict(format="easel.b3.worker-attestation.v3",operation="attest_worker",evidence=bad)))

    def test_i1_evidence_root_key_rejects_without_equality_dispatch(self):
        from test_media_backup_b3_network_fixtures import inert_key_probe
        p=policy();parsed=m.decode_worker_request(m.encode_attest_request(p,123))
        evidence,calls=inert_key_probe(evidence_fixture(p),"executable_sha256")
        self.rejects(m.encode_attestation,parsed,evidence)
        self.assertEqual(calls,[],"non-built-in key equality dispatched before rejection")

    def test_i1_snapshot_key_rejects_without_equality_dispatch(self):
        from test_media_backup_b3_network_fixtures import inert_key_probe
        p=policy();parsed=m.decode_worker_request(m.encode_attest_request(p,123))
        evidence=evidence_fixture(p)
        evidence["network_security_snapshot"],calls=inert_key_probe(evidence["network_security_snapshot"],"schema")
        self.rejects(m.encode_attestation,parsed,evidence)
        self.assertEqual(calls,[],"non-built-in key equality dispatched before rejection")

    def test_normal_import_does_not_register_audit_hook(self):
        source = compile(Path(__file__).read_bytes(), __file__, "exec")
        namespace = {"__name__":"execution_contract_import_probe", "__file__":__file__}
        with mock.patch.object(sys, "addaudithook") as register:
            exec(source, namespace)
        register.assert_not_called()

    def test_direct_entry_registers_guard_before_unittest_main(self):
        source = compile(Path(__file__).read_bytes(), __file__, "exec")
        namespace = {"__name__":"__main__", "__file__":__file__}
        calls = []
        with mock.patch.object(sys, "addaudithook", side_effect=lambda hook:calls.append(("hook",hook))), \
             mock.patch.object(unittest, "main", side_effect=lambda:calls.append(("main",None))):
            exec(source, namespace)
        self.assertEqual([name for name,_ in calls], ["hook","main"])
        self.assertIs(calls[0][1], namespace["_forbid_external_calls"])
        # Synthetic event only: no socket/process is actually created.
        with self.assertRaisesRegex(AssertionError,"external calls prohibited"):
            calls[0][1]("subprocess.Popen", ())

    def rejects(self, call, *args):
        with self.assertRaises(m.B3Error) as caught:
            call(*args)
        self.assertEqual(str(caught.exception), "invalid B3 execution contract")
        self.assertNotIn("private-canary", repr(caught.exception))

    def test_manifest_literal_and_independent_returns(self):
        p = policy()
        expected = manifest_fixture()
        actual = m.build_worker_manifest(p)
        self.assertEqual(actual, expected)
        self.assertEqual(m.validate_worker_manifest(p, expected), canonical(expected))
        actual["entrypoint"][0] = "private-canary"
        actual["tmpfs"].clear()
        self.assertEqual(m.build_worker_manifest(p), expected)

    def test_manifest_every_leaf_and_extra_nested_keys(self):
        for path, replacement in mutations(manifest_fixture()):
            with self.subTest(path=path):
                self.rejects(m.validate_worker_manifest, policy(), changed(manifest_fixture(), path, replacement))
        for key, values in {"network_mode":["host", "none", "container:"+"1"*64, "container:"+"2"*12],
                            "environment":[["SECRET=private-canary"]], "mounts":[[{"source":"/private"}]],
                            "entrypoint":[["sh", "-c", "unsafe"]], "privileged":[True], "readonly_rootfs":[False]}.items():
            for value in values:
                self.rejects(m.validate_worker_manifest, policy(), {**manifest_fixture(), key:value})

    def test_request_deterministic_canonical(self):
        p = policy()
        expected = request_fixture(p)
        self.assertEqual(m.encode_attest_request(p, 123), frame(expected))
        decoded = m.decode_attest_request(frame(expected), p)
        self.assertIs(type(decoded), bytes)
        self.assertEqual(decoded, canonical(expected))
        noncanonical = json.dumps(expected, indent=2).encode()
        self.assertEqual(m.decode_attest_request(len(noncanonical).to_bytes(4,"big")+noncanonical,p), decoded)

    def test_request_every_leaf_and_missing_fields(self):
        p = policy()
        for path, replacement in mutations(request_fixture(p)):
            with self.subTest(path=path):
                self.rejects(m.decode_attest_request, frame(changed(request_fixture(p),path,replacement)), p)
        for key in request_fixture(p):
            bad=request_fixture(p); del bad[key]
            self.rejects(m.decode_attest_request, frame(bad), p)

    def test_independently_held_policy_binding_and_resource_drift(self):
        p = policy(); raw=m.encode_attest_request(p, 123)
        changes = dict(context_sha256="e"*64, verified_input_sha256="e"*64,
                       target_identity_sha256="e"*64, operation_id="other", restore_id="other",
                       started_at_ns=1, deadline_ns=100, job_device=1, job_inode=124,
                       source_container_id="e"*64, target_container_id="e"*64, kind="identity")
        for key,value in changes.items():
            self.rejects(m.decode_attest_request,raw,policy(binding=dataclasses.replace(p.binding,**{key:value})))
        for key,value in dict(cpu_nanos=1, private_result_limit=2048, diagnostic_limit=1,
                              worker_artifact_sha256="e"*64, architecture="amd64").items():
            self.rejects(m.decode_attest_request,raw,dataclasses.replace(p,**{key:value}))

    def test_binding_bounds_and_types(self):
        for key in ("source_container_id", "target_container_id", "target_identity_sha256", "verified_input_sha256", "context_sha256"):
            for value in ("A"*64, "a"*63, "a"*65, None, True, "private-canary"):
                with self.subTest(key=key,value=value):
                    self.rejects(lambda: binding(**{key:value}))
        for key in ("restore_id", "operation_id"):
            for value in ("", "a"*97, "-start", "汉字", "a\x00", True):
                self.rejects(lambda: binding(**{key:value}))
        for key in ("started_at_ns","deadline_ns","job_device","job_inode"):
            for value in (True, -1, 1.0, float("nan"), float("inf"), "1"):
                self.rejects(lambda: binding(**{key:value}))
        for values in (dict(deadline_ns=0),dict(deadline_ns=1800*10**9+1),dict(started_at_ns=2**63-1,deadline_ns=2**63),
                       dict(job_inode=0), dict(target_container_id="1"*64),dict(kind="other"),
                       dict(operation="restore"),dict(operation="attest_worker")):
            self.rejects(lambda: binding(**values))
        self.assertEqual(list(m.Operation), [m.Operation.ATTEST_WORKER])
        binding(restore_id="a"*96, operation_id="A_9-", started_at_ns=2**63-2,deadline_ns=2**63-1)

    def test_policy_resource_limits_and_pins(self):
        caps = dict(cpu_nanos=2*10**9,memory_bytes=2*1024**3,memory_swap_bytes=2*1024**3,
                    pids_limit=128,tmpfs_bytes=256*1024**2,envelope_limit=65536,
                    private_result_limit=1024**2,diagnostic_limit=65536)
        policy(**caps)
        for key,limit in caps.items():
            for value in (True, 0, -1, limit+1, 1.0, float("nan"), float("inf")):
                self.rejects(lambda: policy(**{key:value}))
        for key in ("worker_artifact_sha256","dependency_manifest_sha256","entrypoint_sha256","pg_restore_sha256","network_security_sha256"):
            self.rejects(lambda:policy(**{key:"private-canary"}))
        for values in (dict(worker_image_id="tag:latest"),dict(architecture="x86_64"),dict(binding={}),dict(memory_swap_bytes=1)):
            self.rejects(lambda:policy(**values))

    def test_attestation_roundtrip_and_hashes_only_summary(self):
        p=policy(); expected=attestation_fixture(p); evidence=expected["evidence"]
        raw=m.encode_attestation(m.decode_worker_request(m.encode_attest_request(p,123)),evidence)
        private=m.validate_attestation(p,m.encode_attest_request(p,123),raw)
        self.assertEqual(raw, frame(expected))
        self.assertIs(type(private),bytes)
        self.assertEqual(private,canonical(expected))
        self.assertEqual(m.attestation_summary(p,m.encode_attest_request(p,123),raw),dict(format="easel.b3.worker-attestation-summary.v3",
            operation="attest_worker",binding_sha256=evidence["binding_sha256"],manifest_sha256=evidence["manifest_sha256"],
            request_sha256=evidence["request_sha256"],private_attestation_sha256=hashlib.sha256(private).hexdigest()))
        evidence["worker_netns"]["inode"]=999
        self.assertEqual(m.validate_attestation(p,m.encode_attest_request(p,123),raw),private)
        self.assertNotIn("worker_container_id",canonical(m.attestation_summary(p,m.encode_attest_request(p,123),raw)).decode())

    def test_attestation_closed_evidence_and_drift(self):
        p=policy(); original=attestation_fixture(p)
        # Executable/origin hashes remain self-report; network identity is policy-bound.
        for path,replacement in mutations(original):
            with self.subTest(path=path):
                bad=changed(original,path,replacement)
                self.rejects(m.validate_attestation,p,m.encode_attest_request(p,123),frame(bad))
                if path and path[0]=="evidence":
                    self.rejects(m.encode_attestation,m.decode_worker_request(m.encode_attest_request(p,123)),bad["evidence"])
        for key in original["evidence"]:
            bad=copy.deepcopy(original); del bad["evidence"][key]
            self.rejects(m.validate_attestation,p,m.encode_attest_request(p,123),frame(bad))
        for key,value in (("worker_container_id","1"*64),("worker_container_id","2"*64),
                          ("worker_container_id","b"*12),("worker_image_id","sha256:"+"f"*64),
                          ("architecture","amd64"),("interfaces",["lo","eth0"]),("routes",[{}]),
                          ("uid",0),("gid",0),("cap_effective",1),("cap_permitted",1),
                          ("cap_ambient",1),("cap_inheritable",1),("no_new_privileges",False),
                          ("target_netns",{"device":42,"inode":99}),
                          ("worker_netns",{"device":0,"inode":1234})):
            self.rejects(m.validate_attestation,p,m.encode_attest_request(p,123),frame(changed(original,("evidence",key),value)))
        for value in (0,-1,True,1.0):
            bad=copy.deepcopy(original)
            for key in ("worker_netns",):bad["evidence"][key]["inode"]=value
            self.rejects(m.validate_attestation,p,m.encode_attest_request(p,123),frame(bad))
        self.rejects(m.validate_attestation,policy(binding=binding(context_sha256="e"*64)),m.encode_attest_request(p,123),frame(original))

    def test_valid_hash_substitutions_rejected_for_every_attestation_pin(self):
        p=policy(); original=attestation_fixture(p)
        for key in ("binding_sha256","policy_sha256","request_sha256","manifest_sha256","artifact_sha256","dependency_manifest_sha256",
                    "entrypoint_sha256","pg_restore_sha256"):
            bad=changed(original,("evidence",key),"e"*64)
            self.rejects(m.validate_attestation,p,m.encode_attest_request(p,123),frame(bad))
            self.rejects(m.encode_attestation,m.decode_worker_request(m.encode_attest_request(p,123)),bad["evidence"])
        for key in original:
            bad=copy.deepcopy(original); del bad[key]
            self.rejects(m.validate_attestation,p,m.encode_attest_request(p,123),frame(bad))
        # Variable executable/origin hashes remain self-report.
        evidence=evidence_fixture(p)
        evidence.update(executable_sha256="e"*64,package_origins_sha256="f"*64)
        evidence["worker_netns"]=network_evidence()["network_security_snapshot"]["namespace"]
        self.assertIs(type(m.validate_attestation(p,m.encode_attest_request(p,123),m.encode_attestation(m.decode_worker_request(m.encode_attest_request(p,123)),evidence))),bytes)

    def test_frame_parser_adversarial_inputs_both_protocols(self):
        p=policy()
        for valid,limit,decode in ((m.encode_attest_request(p, 123),p.envelope_limit,lambda raw:m.decode_attest_request(raw,p)),
                                    (m.encode_attestation(m.decode_worker_request(m.encode_attest_request(p,123)),evidence_fixture(p)),p.private_result_limit,lambda raw:m.validate_attestation(p,m.encode_attest_request(p,123),raw))):
            invalid=[b"",b"\x00",valid[:-1],valid+b"x",valid+valid,(limit+1).to_bytes(4,"big"),
                     valid[0:4]+valid[4:]+b"\x00",bytearray(valid),memoryview(valid),"private-canary",None]
            payloads=[b"\xff",b'{"x":"\x00"}',b'{"x":"\\ud800"}',b'{"x":NaN}',b'{"x":Infinity}',
                      b'{"x":-Infinity}',b'{"x":1.0}',b'{"x":1e9}',b'{"x":0,"x":0}',
                      b'{"nested":{"private-canary":0,"private-canary":0}}',b'['*1100+b']'*1100]
            # Duplicates in otherwise valid frames must fail even for equal values.
            body=valid[4:]; payloads.append(body.replace(b'"operation":"attest_worker"',b'"operation":"attest_worker","operation":"attest_worker"',1))
            # Duplicates in valid nested objects cannot be silently discarded.
            needle = b'"job_inode":123' if b'"job_inode":123' in body else (b'"inode":'+str(evidence_fixture(p)["worker_netns"]["inode"]).encode())
            payloads.append(body.replace(needle,needle+b','+needle,1))
            for payload in payloads:invalid.append(len(payload).to_bytes(4,"big")+payload)
            for raw in invalid:
                self.rejects(decode,raw)

    def test_small_limits_and_record_privacy(self):
        p=policy(envelope_limit=1,private_result_limit=1)
        self.rejects(m.encode_attest_request,p,123)
        limited=policy(private_result_limit=1)
        request=m.encode_attest_request(limited,123)
        self.rejects(m.encode_attestation,m.decode_worker_request(request),evidence_fixture(limited))
        self.rejects(m.validate_attestation,limited,request,frame(attestation_fixture(limited)))
        for record in (binding(restore_id="private-canary"),policy()):
            self.assertNotIn("private-canary",repr(record))
            self.assertNotIn("666666",repr(record))
            with self.assertRaises(dataclasses.FrozenInstanceError):record.anything=1


if __name__ == "__main__":
    sys.addaudithook(_forbid_external_calls)
    unittest.main()
