"""Encode trusted checkout payloads as local SVG pixels, without remote assets."""

import base64

from .errors import PlatformError


def qr_data_url(value):
    if (
        not isinstance(value, str)
        or not 1 <= len(value.encode("utf-8")) <= 2048
        or any(ord(c) < 32 for c in value)
    ):
        raise PlatformError(502, "invalid_checkout_code")
    import qrcode
    from qrcode.image.svg import SvgPathFillImage

    try:
        image = qrcode.make(value, image_factory=SvgPathFillImage, box_size=4, border=4)
        encoded = image.to_string()
        return "data:image/svg+xml;base64," + base64.b64encode(encoded).decode("ascii")
    except (ValueError, OverflowError):
        raise PlatformError(502, "invalid_checkout_code") from None
