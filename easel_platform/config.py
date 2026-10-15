from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
import ipaddress

@dataclass(frozen=True)
class Settings:
    database: Path
    upstream_url: str = ""
    upstream_key: str = field(default="", repr=False)
    upstream_model: str = ""
    public_model: str = ""

    def __post_init__(self):
        object.__setattr__(self, "database", Path(self.database))
        if self.upstream_url:
            try:
                u = urlsplit(self.upstream_url)
                u.port
            except ValueError:
                raise ValueError("Invalid upstream configuration") from None
            try:
                loopback = u.hostname == "localhost" or ipaddress.ip_address(u.hostname).is_loopback
            except ValueError:
                loopback = False
            if not u.hostname or u.username or u.password or u.query or u.fragment or (u.scheme != "https" and not (u.scheme == "http" and loopback)):
                raise ValueError("Invalid upstream configuration")
