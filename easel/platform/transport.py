"""Local HTTP account authority; CLI callers have no HTTP peer context.

Owned Uvicorn entrypoints disable proxy header rewriting. Reverse-proxy account
access needs a separate authenticated transport policy and is not supported.
"""

from contextvars import ContextVar
import ipaddress

http_peer = ContextVar("platform_http_peer", default=None)


def require_loopback(host):
    try:
        address = ipaddress.ip_address(host)
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        if address.is_loopback:
            return
    except ValueError:
        pass
    from fastapi import HTTPException

    raise HTTPException(
        403,
        {
            "code": "local_peer_required",
            "message": "平台账号与订阅服务仅允许本机直连访问",
        },
    )


def require_local_http():
    peer = http_peer.get()
    if peer is not None:
        require_loopback(peer)
