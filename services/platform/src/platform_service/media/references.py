"""Authenticated HTTP correlations are distinct from provider resource IDs."""
import re
from ..errors import PlatformError

HTTP_REFERENCE_PATTERNS = {
    "openai_x_request_id_v1": re.compile(r"req_[A-Za-z0-9_-]{1,192}\Z", re.ASCII),
    "newapi_x_oneapi_request_id_v1": re.compile(r"[0-9]{23}[A-Za-z0-9]{16}\Z", re.ASCII),
    "easel_client_request_v1": re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z", re.ASCII),
}


def http_reference(namespace, value):
    pattern = HTTP_REFERENCE_PATTERNS.get(namespace) if isinstance(namespace, str) else None
    if pattern is None or not isinstance(value, str) or not pattern.fullmatch(value):
        raise PlatformError(422, "invalid_media_reference")
    return namespace, value
