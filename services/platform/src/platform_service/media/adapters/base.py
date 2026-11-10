from dataclasses import dataclass
from ...errors import PlatformError


@dataclass(frozen=True)
class PreparedRequest:
    url: str
    payload: bytes
    content_type: str
    upstream_model: str


@dataclass(frozen=True)
class HttpCorrelation:
    namespace: str
    value: str


@dataclass(frozen=True)
class ImageResult:
    response_id: str | None
    images: list[dict]
    quantities: dict[str, int]
    usage_hash: str | None
    evidence_hash: str
    correlation: HttpCorrelation | None = None


class MediaAdapter:
    async def query(self, association):
        raise PlatformError(405, "media_query_not_supported")

    async def cancel(self, association):
        raise PlatformError(405, "media_cancel_not_supported")
