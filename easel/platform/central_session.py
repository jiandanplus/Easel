"""Separate central product credentials; legacy development sessions stay intact."""

from .session import SessionStore


class CentralSessionStore(SessionStore):
    filename = "central-platform-session"
    version = 2
