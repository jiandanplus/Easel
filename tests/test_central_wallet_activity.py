"""Read-only wallet counters keep unknown legacy values distinct from zero."""
import httpx
import pytest

from easel.platform.central import CentralError
from tests.test_central_platform_client import auth, client


def read_wallet(tmp_path, **fields):
    calls=[]
    value=dict(unit="microcredit",microcredits_per_point="1000000",available="9000000",reserved="9728",
               spent="1000",expired="0",issued="9010728",pending_reconciliation=2,test_only=False,**fields)
    def handle(request):
        calls.append(request)
        assert request.method=="GET" and request.url.path=="/v2/wallet"
        return httpx.Response(200,json=value)
    native=client(tmp_path,handle)
    native.save_auth(auth())
    result=native.call("GET","/v2/wallet")
    assert len(calls)==1
    return result


def test_wallet_exposes_separate_active_and_pending_limit_without_releasing_holds(tmp_path):
    result=read_wallet(tmp_path,active_requests=0,pending_reconciliation_limit=3)
    assert result["active_requests"]==0 and result["pending_reconciliation_limit"]==3
    assert result["pending_reconciliation"]==2 and result["reserved"]=="9728"


def test_legacy_wallet_does_not_invent_missing_request_counters(tmp_path):
    result=read_wallet(tmp_path)
    assert "active_requests" not in result and "pending_reconciliation_limit" not in result
    assert result["pending_reconciliation"]==2 and result["reserved"]=="9728"


@pytest.mark.parametrize("field,value", [
    ("active_requests",True),("active_requests",-1),("active_requests",1.5),
    ("active_requests","0"),("active_requests",None),("active_requests",2**53),
    ("pending_reconciliation_limit",True),("pending_reconciliation_limit",0),
    ("pending_reconciliation_limit",-1),("pending_reconciliation_limit",3.0),
    ("pending_reconciliation_limit","3"),("pending_reconciliation_limit",None),
])
def test_invalid_new_wallet_counters_are_not_forwarded_as_real_values(tmp_path,field,value):
    with pytest.raises(CentralError):
        read_wallet(tmp_path,**{field:value})


def test_pending_limit_is_a_fixed_public_error_without_provider_text():
    assert str(CentralError("pending_reconciliation_limit",409))=="待核算请求已达上限，请先查询或联系运营者核账"
