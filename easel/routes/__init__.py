"""Workbench HTTP routes split out of web/app.py by domain.

Each module exposes create_router(core), where `core` is the loaded web.app
module. Handlers read shared paths and helpers from `core` at call time, so
tests that monkeypatch web.app attributes keep working; state and helpers a
domain owns live in its module.
"""
