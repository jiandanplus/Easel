"""Standalone platform service. No desktop runtime or workspace dependencies."""
from .config import Settings
from .store import Store
from .api import create_app

__all__ = ["Settings", "Store", "create_app"]
