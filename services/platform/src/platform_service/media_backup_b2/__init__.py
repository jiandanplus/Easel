"""Import-only authenticated backup candidate; no control or production entrypoint."""
from .contracts import B2Error, CapturedSource, CommitRef, ExpectedV2

__all__ = ["B2Error", "CapturedSource", "CommitRef", "ExpectedV2"]
