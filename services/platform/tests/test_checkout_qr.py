import base64
import xml.etree.ElementTree as ET

import pytest

from platform_service.errors import PlatformError


def test_checkout_qr_is_local_svg_with_no_embedded_active_payload():
    from platform_service.checkout_qr import qr_data_url

    result = qr_data_url(
        "weixin://wxpay/bizpayurl?pr=fixture-<script>alert(1)</script>"
    )
    assert result.startswith("data:image/svg+xml;base64,")
    data = base64.b64decode(result.split(",", 1)[1])
    tree = ET.fromstring(data)
    assert tree.tag.endswith("svg")
    assert b"<script" not in data and b"weixin" not in data
    assert not any(
        key.endswith("href") for element in tree.iter() for key in element.attrib
    )


def test_checkout_qr_accepts_provider_contract_length():
    from platform_service.checkout_qr import qr_data_url

    result = qr_data_url(("abcdefghijklmnopqrstuvwxyz0123456789" * 57)[:2048])
    assert result.startswith("data:image/svg+xml;base64,")
    assert len(result) <= 1000000  # Same bounded data-image contract as Easel UI.


@pytest.mark.parametrize("value", [None, "", "x" * 2049, "a\x00b"])
def test_checkout_qr_rejects_invalid_and_unbounded_payloads(value):
    from platform_service.checkout_qr import qr_data_url

    with pytest.raises(PlatformError):
        qr_data_url(value)
