"""Pure binding contracts; synthetic identities only."""
import importlib, sys
from pathlib import Path
from uuid import uuid4
import pytest
sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
def api():
    try: return importlib.import_module("platform_service.media_maintenance.contracts")
    except ModuleNotFoundError: pytest.fail("B2 immutable binding contract is missing")
def worker(lib):
    return lib.WorkerIdentity(str(uuid4()), str(uuid4()), "synthetic-host", "synthetic-boot", "123:456")
def test_valid_bindings_are_immutable_and_nonce_never_enters_repr():
    lib=api(); w=worker(lib)
    op=lib.OperationBinding("job", "job-1", "attempt-1", w, "a"*64)
    window=lib.WindowBinding(str(uuid4()), b"s"*32, 2, w.host_id, w.boot_id, w.process_start, 1,2,3,4,"b"*64,100)
    assert op.worker is w and op.attempt_id=="attempt-1"
    assert "ssss" not in repr(window)
    with pytest.raises((AttributeError,TypeError)): op.subject_id="changed"
@pytest.mark.parametrize("field,value", [("epoch",True),("epoch",-1),("owner_nonce",b"x"),("deadline_ns",0),("generation_digest","A"*64),("window_id","not-uuid")])
def test_window_rejects_ambiguous_or_unbound_values(field,value):
    lib=api(); w=worker(lib); values=dict(window_id=str(uuid4()),owner_nonce=b"x"*32,epoch=0,host_id=w.host_id,boot_id=w.boot_id,process_start=w.process_start,control_dev=1,control_ino=2,source_dev=3,source_ino=4,generation_digest="a"*64,deadline_ns=1)
    values[field]=value
    with pytest.raises(lib.MaintenanceError,match="invalid_backup"):lib.WindowBinding(**values)
@pytest.mark.parametrize("kind,subject,attempt", [("unknown","x",None),("job","", "a"),("job","j",None),("upload","i","attempt"),("gc","key",None)])
def test_operation_kind_and_attempt_binding_are_strict(kind,subject,attempt):
    lib=api()
    with pytest.raises(lib.MaintenanceError):lib.OperationBinding(kind,subject,attempt,worker(lib),None)
